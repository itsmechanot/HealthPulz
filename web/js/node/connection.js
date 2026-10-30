// The node's live WebSocket channel to the RHU (protocol section 3).
//
// Events (CustomEvent, data in event.detail):
//   status        "connecting" | "online" | "offline"
//   message       any message from the RHU (STORED, ALERT, ERROR, ...)
//   unregistered  the RHU doesn't know this node (close code 4404)

import { wsUrl } from "../common/api.js";

const HEARTBEAT_MS = 10_000;
const MAX_RECONNECT_MS = 15_000;
const CLOSE_UNKNOWN_NODE = 4404;

export class NodeConnection extends EventTarget {
  constructor(nodeId, getPendingBatches) {
    super();
    this.nodeId = nodeId;
    this.getPendingBatches = getPendingBatches;
    this.ws = null;
    this.heartbeat = null;
    this.reconnectDelay = 1000;
    this.stopped = false;
  }

  connect() {
    this.stopped = false;
    this.emit("status", "connecting");
    const ws = new WebSocket(wsUrl(`/ws/node/${this.nodeId}`));
    this.ws = ws;

    ws.onopen = () => this.send({ type: "HELLO", node_id: this.nodeId, pending_batches: this.getPendingBatches() });

    ws.onmessage = (event) => {
      const message = JSON.parse(event.data);
      if (message.type === "WELCOME") {
        this.reconnectDelay = 1000;
        this.startHeartbeat();
        this.emit("status", "online");
      }
      this.emit("message", message);
    };

    ws.onclose = (event) => {
      this.stopHeartbeat();
      this.emit("status", "offline");
      if (event.code === CLOSE_UNKNOWN_NODE) this.emit("unregistered");
      if (!this.stopped) this.scheduleReconnect();
    };
  }

  close() {
    this.stopped = true;
    this.ws?.close();
  }

  send(message) {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(message));
  }

  startHeartbeat() {
    this.stopHeartbeat();
    this.heartbeat = setInterval(
      () => this.send({ type: "HEARTBEAT", node_id: this.nodeId, pending_batches: this.getPendingBatches() }),
      HEARTBEAT_MS,
    );
  }

  stopHeartbeat() {
    clearInterval(this.heartbeat);
  }

  /** Exponential backoff so a downed RHU isn't flooded with reconnects. */
  scheduleReconnect() {
    setTimeout(() => this.connect(), this.reconnectDelay);
    this.reconnectDelay = Math.min(this.reconnectDelay * 2, MAX_RECONNECT_MS);
  }

  emit(type, detail) {
    this.dispatchEvent(new CustomEvent(type, { detail }));
  }
}

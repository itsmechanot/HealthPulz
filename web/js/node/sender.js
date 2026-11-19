// Sends outbox batches until STORED; after 30 s it checks and resends (duplicates are ignored).

import * as api from "../common/api.js";
import * as outbox from "./outbox.js";

const STORED_TIMEOUT_MS = 30_000;
const PUMP_INTERVAL_MS = 5_000;

export class Sender extends EventTarget {
  constructor(nodeId, reRegister) {
    super();
    this.nodeId = nodeId;
    this.reRegister = reRegister;
    this.inFlight = new Map(); // batch_id -> time it was RECEIVED
    this.pausedUntil = 0;
    this.pumping = false;
    this.pumpAgain = false;
  }

  start() {
    this.timer = setInterval(() => this.pump(), PUMP_INTERVAL_MS);
    this.pump();
  }

  async enqueue(batches) {
    await outbox.addBatches(batches);
    this.emit("changed");
    this.pump();
  }

  /** Called with every STORED message the WebSocket receives. Returns whether the batch was this device's. */
  async confirmStored(message) {
    // A resent batch can be confirmed twice; only the first confirmation counts.
    const waiting = this.inFlight.delete(message.batch_id) || (await outbox.hasBatch(message.batch_id));
    if (!waiting) return false;
    await outbox.removeBatch(message.batch_id);
    this.emit("stored", message);
    this.emit("changed");
    return true;
  }

  /** Go through the outbox once. Calls made while a pump is running trigger one more pass. */
  async pump() {
    if (this.pumping) {
      this.pumpAgain = true;
      return;
    }
    this.pumping = true;
    try {
      do {
        this.pumpAgain = false;
        for (const batch of await outbox.allBatches(this.nodeId)) {
          if (Date.now() < this.pausedUntil) return;
          if (!(await this.process(batch))) return;
        }
      } while (this.pumpAgain);
    } finally {
      this.pumping = false;
    }
  }

  /** Returns false when sending should stop for now (RHU busy or unreachable). */
  async process(batch) {
    const receivedAt = this.inFlight.get(batch.batch_id);
    if (receivedAt && Date.now() - receivedAt < STORED_TIMEOUT_MS) return true; // waiting for STORED

    try {
      if (receivedAt) {
        // STORED is overdue (maybe it arrived while we were disconnected): ask the RHU.
        const status = await api.getBatchStatus(batch.batch_id);
        if (status.status === "STORED") {
          await this.confirmStored({ ...status, type: "STORED" }); // here `rejected` is a count, not a list
          return true;
        }
      }

      const res = await api.submitBatch(batch);
      switch (res.status) {
        case 202:
          this.inFlight.set(batch.batch_id, Date.now());
          this.emit("sent", batch);
          return true;
        case 503:
          this.pausedUntil = Date.now() + res.retryAfter * 1000;
          this.emit("busy", res.retryAfter);
          return false;
        case 404:
          await this.reRegister();
          return false; // retried on the next pump
        case 400:
          await outbox.removeBatch(batch.batch_id);
          this.emit("dropped", { batch, body: res.body });
          this.emit("changed");
          return true;
        default:
          return false;
      }
    } catch {
      this.emit("offline");
      return false;
    }
  }

  emit(type, detail) {
    this.dispatchEvent(new CustomEvent(type, { detail }));
  }
}

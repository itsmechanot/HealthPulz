"""Live WebSocket connections to BHS nodes and System Monitor pages.

All bookkeeping happens on the event loop thread. Other threads (such as the
DB writer) never touch a socket directly: they hand the message over with
send_to_node_threadsafe(), which schedules the send on the event loop.
"""

import asyncio
import logging
import time
from dataclasses import dataclass

from fastapi import WebSocket

from rhu.config import HEARTBEAT_TIMEOUT_SECONDS

log = logging.getLogger(__name__)


@dataclass
class Presence:
    last_seen: float  # time.monotonic() of the last HELLO/HEARTBEAT
    pending_batches: int


class Hub:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        # A station can have many open connections: every device logged in as that barangay.
        self._nodes: dict[str, set[WebSocket]] = {}
        self._presence: dict[str, Presence] = {}
        self._monitors: set[WebSocket] = set()

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    # ---- nodes -----------------------------------------------------------

    def add_node(self, node_id: str, ws: WebSocket) -> None:
        self._nodes.setdefault(node_id, set()).add(ws)

    def remove_node(self, node_id: str, ws: WebSocket) -> None:
        connections = self._nodes.get(node_id)
        if connections is None:
            return
        connections.discard(ws)
        if not connections:
            del self._nodes[node_id]

    def is_connected(self, node_id: str) -> bool:
        return node_id in self._nodes

    def connection_count(self, node_id: str) -> int:
        return len(self._nodes.get(node_id, ()))

    def heartbeat(self, node_id: str, pending_batches: int) -> None:
        self._presence[node_id] = Presence(time.monotonic(), pending_batches)

    def node_status(self, node_id: str) -> tuple[str, int]:
        """Returns (ONLINE | OFFLINE, pending_batches reported by the node)."""
        presence = self._presence.get(node_id)
        if presence is None:
            return "OFFLINE", 0
        fresh = time.monotonic() - presence.last_seen < HEARTBEAT_TIMEOUT_SECONDS
        status = "ONLINE" if fresh and self.is_connected(node_id) else "OFFLINE"
        return status, presence.pending_batches

    async def send_to_node(self, node_id: str, message: dict) -> None:
        for ws in list(self._nodes.get(node_id, ())):
            if not await _send(ws, message):
                self.remove_node(node_id, ws)

    def send_to_node_threadsafe(self, node_id: str, message: dict) -> None:
        """Called from worker threads. If the node isn't connected the message is
        dropped; the node recovers with GET /api/batches/{id} or by resending."""
        if self._loop is not None:
            asyncio.run_coroutine_threadsafe(self.send_to_node(node_id, message), self._loop)

    # ---- monitors --------------------------------------------------------

    def add_monitor(self, ws: WebSocket) -> None:
        self._monitors.add(ws)

    def remove_monitor(self, ws: WebSocket) -> None:
        self._monitors.discard(ws)

    def has_monitors(self) -> bool:
        return bool(self._monitors)

    async def broadcast_to_monitors(self, message: dict) -> None:
        for ws in list(self._monitors):
            if not await _send(ws, message):
                self.remove_monitor(ws)


async def _send(ws: WebSocket, message: dict) -> bool:
    try:
        await ws.send_json(message)
        return True
    except Exception:
        # The other side went away mid-send; the caller drops this connection.
        log.debug("Dropping closed WebSocket", exc_info=True)
        return False

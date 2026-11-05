"""WebSocket endpoints: BHS nodes (/ws/node/{node_id}) and the System Monitor (/ws/monitor)."""

import asyncio
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, FastAPI, WebSocket, WebSocketDisconnect

from rhu import db
from rhu.config import MONITOR_INTERVAL_SECONDS, NODE_RECENT_HOURS
from rhu.hub import Hub

router = APIRouter()

CLOSE_BAD_HANDSHAKE = 4400
CLOSE_UNKNOWN_NODE = 4404


@router.websocket("/ws/node/{node_id}")
async def node_socket(ws: WebSocket, node_id: str) -> None:
    hub: Hub = ws.app.state.hub
    db_path: Path = ws.app.state.db_path
    joined = False
    await ws.accept()
    try:
        hello = await ws.receive_json()
        if _type(hello) != "HELLO" or hello.get("node_id") != node_id:
            await ws.close(code=CLOSE_BAD_HANDSHAKE, reason="First message must be HELLO for this node_id")
            return
        if not await asyncio.to_thread(_node_exists, db_path, node_id):
            await ws.close(code=CLOSE_UNKNOWN_NODE, reason="Unknown node_id; register first")
            return

        hub.add_node(node_id, ws)
        hub.heartbeat(node_id, _pending_batches(hello))
        joined = True
        await asyncio.to_thread(_set_node_status, db_path, node_id, "ONLINE")
        await ws.send_json({"type": "WELCOME", "node_id": node_id})
        await ws.app.state.alerts.send_pending(node_id)  # alerts it missed while offline

        while True:
            message = await ws.receive_json()
            match _type(message):
                case "HEARTBEAT":
                    hub.heartbeat(node_id, _pending_batches(message))
                case "ALERT_ACK" if isinstance(message.get("alert_id"), str):
                    await ws.app.state.alerts.acknowledge(message["alert_id"], node_id)
                case _:
                    await ws.send_json({"type": "ERROR", "reason": "UNKNOWN_TYPE"})
    except (WebSocketDisconnect, RuntimeError):
        # RuntimeError: the device left while the server was pushing to it (see Hub._send),
        # so Starlette reports the next read differently. Either way, the device is gone.
        pass
    finally:
        if joined:
            hub.remove_node(node_id, ws)
            if not hub.is_connected(node_id):
                await asyncio.to_thread(_set_node_status, db_path, node_id, "OFFLINE")


@router.websocket("/ws/monitor")
async def monitor_socket(ws: WebSocket) -> None:
    hub: Hub = ws.app.state.hub
    await ws.accept()
    hub.add_monitor(ws)
    try:
        # Send one snapshot right away so the page doesn't start empty.
        await ws.send_json(await build_snapshot(ws.app, records_per_second=0.0))
        while True:
            await ws.receive_text()  # monitors only listen; this just waits for the disconnect
    except (WebSocketDisconnect, RuntimeError):
        pass  # the page left (RuntimeError: while the server was pushing to it)
    finally:
        hub.remove_monitor(ws)


async def monitor_feed(app: FastAPI) -> None:
    """Background task: push a snapshot to every open System Monitor each interval."""
    pipeline = app.state.pipeline
    hub: Hub = app.state.hub
    last_count = pipeline.stats.snapshot()["records_accepted"]
    last_time = time.monotonic()
    while True:
        await asyncio.sleep(MONITOR_INTERVAL_SECONDS)
        count, now = pipeline.stats.snapshot()["records_accepted"], time.monotonic()
        rate = (count - last_count) / (now - last_time)
        last_count, last_time = count, now
        if hub.has_monitors():
            await hub.broadcast_to_monitors(await build_snapshot(app, rate))


async def build_snapshot(app: FastAPI, records_per_second: float) -> dict:
    hub: Hub = app.state.hub
    registered = await asyncio.to_thread(_registered_nodes, app.state.db_path)
    nodes = []
    for node_id, barangay_id, last_seen in registered:
        # Old stations stay in the database (their cases point to them), but not on the monitor.
        if not hub.is_connected(node_id) and last_seen < _recent_cutoff():
            continue
        status, pending = hub.node_status(node_id)
        nodes.append({
            "node_id": node_id, "barangay_id": barangay_id, "status": status, "pending_batches": pending,
            "connections": hub.connection_count(node_id),
        })
    analytics = app.state.analytics.last_result
    return {
        "type": "MONITOR",
        "at": db.utc_now(),
        "pipeline": {**app.state.pipeline.snapshot(), "records_per_second": round(records_per_second, 1)},
        "analytics": analytics.to_dict() if analytics else None,
        "nodes": nodes,
    }


def _type(message) -> str | None:
    return message.get("type") if isinstance(message, dict) else None


def _pending_batches(message: dict) -> int:
    value = message.get("pending_batches", 0)
    return value if isinstance(value, int) and value >= 0 else 0


# ---- blocking DB helpers (run with asyncio.to_thread) ----------------------

def _node_exists(db_path: Path, node_id: str) -> bool:
    with closing(db.connect(db_path)) as conn:
        return conn.execute("SELECT 1 FROM nodes WHERE node_id = ?", (node_id,)).fetchone() is not None


def _set_node_status(db_path: Path, node_id: str, status: str) -> None:
    with closing(db.connect(db_path)) as conn, conn:
        conn.execute(
            "UPDATE nodes SET status = ?, last_seen_at = ? WHERE node_id = ?", (status, db.utc_now(), node_id)
        )


def _recent_cutoff() -> str:
    """Same format as db.utc_now(), so the timestamps compare as text."""
    return (datetime.now(timezone.utc) - timedelta(hours=NODE_RECENT_HOURS)).isoformat(timespec="milliseconds")


def _registered_nodes(db_path: Path) -> list[tuple[str, str, str]]:
    with closing(db.connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT node_id, barangay_id, COALESCE(last_seen_at, registered_at) FROM nodes WHERE kind = 'LIVE' ORDER BY node_id"
        )
        return [tuple(row) for row in rows]

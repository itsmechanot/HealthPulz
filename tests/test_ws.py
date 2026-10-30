"""Tests for the WebSocket endpoints (node channel and System Monitor)."""

import pytest
from fastapi import WebSocketDisconnect

from rhu.history import import_history
from tests.factories import NODE, make_batch


def hello(ws, node_id=NODE):
    ws.send_json({"type": "HELLO", "node_id": node_id, "pending_batches": 0})
    return ws.receive_json()


def node_entry(snapshot, node_id=NODE):
    return next(n for n in snapshot["nodes"] if n["node_id"] == node_id)


def test_hello_is_welcomed(registered):
    with registered.websocket_connect(f"/ws/node/{NODE}") as ws:
        assert hello(ws) == {"type": "WELCOME", "node_id": NODE}


def test_stored_is_pushed_to_the_node(registered):
    with registered.websocket_connect(f"/ws/node/{NODE}") as ws:
        hello(ws)
        batch = make_batch(NODE, count=5)
        registered.post("/api/batches", json=batch)

        assert ws.receive_json() == {
            "type": "STORED", "batch_id": batch["batch_id"], "accepted": 5, "duplicates": 0, "rejected": [],
        }


def test_first_message_must_be_hello(registered):
    with registered.websocket_connect(f"/ws/node/{NODE}") as ws:
        ws.send_json({"type": "HEARTBEAT", "node_id": NODE})
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
    assert closed.value.code == 4400


def test_unregistered_node_is_closed(client):
    with client.websocket_connect(f"/ws/node/{NODE}") as ws:
        ws.send_json({"type": "HELLO", "node_id": NODE})
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
    assert closed.value.code == 4404


def test_unknown_message_type_gets_an_error(registered):
    with registered.websocket_connect(f"/ws/node/{NODE}") as ws:
        hello(ws)
        ws.send_json({"type": "DANCE"})
        assert ws.receive_json() == {"type": "ERROR", "reason": "UNKNOWN_TYPE"}


def test_monitor_shows_connected_node_online(registered):
    with registered.websocket_connect(f"/ws/node/{NODE}") as node:
        hello(node)
        node.send_json({"type": "HEARTBEAT", "node_id": NODE, "pending_batches": 3})
        node.send_json({"type": "DANCE"})  # any reply proves the heartbeat was processed
        node.receive_json()

        with registered.websocket_connect("/ws/monitor") as monitor:
            snapshot = monitor.receive_json()

    assert snapshot["type"] == "MONITOR"
    assert node_entry(snapshot) == {
        "node_id": NODE, "barangay_id": "BRGY-08", "status": "ONLINE", "pending_batches": 3,
    }
    assert {"records_per_second", "ingest_queue", "validator_threads"} <= snapshot["pipeline"].keys()


def test_monitor_lists_only_live_nodes(registered):
    import_history(registered.app.state.db_path, years=1)
    with registered.websocket_connect("/ws/monitor") as monitor:
        assert [n["node_id"] for n in monitor.receive_json()["nodes"]] == [NODE]


def test_monitor_shows_registered_but_unconnected_node_offline(registered):
    with registered.websocket_connect("/ws/monitor") as monitor:
        assert node_entry(monitor.receive_json())["status"] == "OFFLINE"

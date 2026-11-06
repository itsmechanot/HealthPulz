"""Tests for the WebSocket endpoints (node channel and System Monitor)."""

from contextlib import closing

import pytest
from fastapi import WebSocketDisconnect

from rhu import db
from rhu.history import import_history
from tests.factories import NODE, make_batch


STATION = "BRGY-08-MAIN"


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
            "type": "STORED", "batch_id": batch["batch_id"], "accepted": 5, "updated": 0, "duplicates": 0, "rejected": [],
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
        "node_id": NODE, "barangay_id": "BRGY-08", "status": "ONLINE", "pending_batches": 3, "connections": 1,
    }
    assert {"records_per_second", "ingest_queue", "validator_threads"} <= snapshot["pipeline"].keys()


def register_station(client):
    client.post("/api/nodes/register", json={"type": "REGISTER", "node_id": STATION, "barangay_id": "BRGY-08"})


def test_monitor_lists_only_live_nodes(registered):
    register_station(registered)
    import_history(registered.app.state.db_path, years=1)
    with registered.websocket_connect("/ws/monitor") as monitor:
        assert [n["node_id"] for n in monitor.receive_json()["nodes"]] == [STATION]


def test_monitor_hides_old_ids_of_a_barangay_once_disconnected(registered):
    """NODE is an ID from before the fixed stations, or a load-test node."""
    register_station(registered)
    with registered.websocket_connect(f"/ws/node/{NODE}") as node:
        hello(node)
        with registered.websocket_connect("/ws/monitor") as monitor:
            assert {n["node_id"] for n in monitor.receive_json()["nodes"]} == {STATION, NODE}
    with registered.websocket_connect("/ws/monitor") as monitor:
        assert [n["node_id"] for n in monitor.receive_json()["nodes"]] == [STATION]


def test_monitor_shows_a_registered_but_unconnected_station_offline(registered):
    register_station(registered)
    with registered.websocket_connect("/ws/monitor") as monitor:
        assert node_entry(monitor.receive_json(), STATION)["status"] == "OFFLINE"


def test_monitor_hides_stations_not_seen_recently(registered):
    register_station(registered)
    with closing(db.connect(registered.app.state.db_path)) as conn, conn:
        conn.execute("INSERT INTO nodes (node_id, barangay_id, registered_at) VALUES ('BRGY-01-MAIN', 'BRGY-01', '2020-01-01T00:00:00.000+00:00')")

    with registered.websocket_connect("/ws/monitor") as monitor:
        assert [n["node_id"] for n in monitor.receive_json()["nodes"]] == [STATION]


def test_monitor_always_shows_a_connected_station(registered):
    with closing(db.connect(registered.app.state.db_path)) as conn, conn:
        conn.execute("INSERT INTO nodes (node_id, barangay_id, registered_at) VALUES ('BRGY-01-OLD1', 'BRGY-01', '2020-01-01T00:00:00.000+00:00')")

    with registered.websocket_connect("/ws/node/BRGY-01-OLD1") as node:
        hello(node, "BRGY-01-OLD1")
        with registered.websocket_connect("/ws/monitor") as monitor:
            entry = node_entry(monitor.receive_json(), "BRGY-01-OLD1")
    assert entry["status"] == "ONLINE"


# ---- synchronized burst ----------------------------------------------------------------

def test_burst_reaches_every_connected_device(registered):
    register_station(registered)
    with registered.websocket_connect(f"/ws/node/{STATION}") as phone, registered.websocket_connect(f"/ws/node/{STATION}") as laptop:
        hello(phone, STATION)
        hello(laptop, STATION)
        with registered.websocket_connect(f"/ws/node/{NODE}") as other:
            hello(other)

            res = registered.post("/api/burst", json={"records_per_device": 500})

            assert res.json() == {"devices": 3, "records_per_device": 500}
            for ws in (phone, laptop, other):
                assert ws.receive_json() == {"type": "BURST", "records_per_device": 500}


def test_burst_with_nobody_connected_reaches_no_one(client):
    assert client.post("/api/burst", json={"records_per_device": 500}).json()["devices"] == 0


def test_burst_is_not_kept_for_a_device_that_connects_later(registered):
    registered.post("/api/burst", json={"records_per_device": 500})
    with registered.websocket_connect(f"/ws/node/{NODE}") as late:
        hello(late)
        late.send_json({"type": "DANCE"})
        assert late.receive_json() == {"type": "ERROR", "reason": "UNKNOWN_TYPE"}  # the next message, not BURST


@pytest.mark.parametrize("size", [0, -1, 5001, "many"])
def test_burst_size_must_be_sensible(client, size):
    assert client.post("/api/burst", json={"records_per_device": size}).status_code == 400


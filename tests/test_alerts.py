"""Tests for outbreak alert detection and delivery."""

from contextlib import closing
from datetime import date

import pytest

from rhu import db, rules
from rhu.alerts import acknowledge, classify, detect_alerts, recipients, weeks_to_check
from tests.factories import NODE, make_batch, make_record, wait_for_stored

TODAY = date(2026, 10, 30)  # ISO week 2026-W44


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    db.init_db(path)
    return path


def put_stats(db_path, count, alert=4.0, epidemic=6.0, disease="DEN", barangay="BRGY-08", year=2026, week=44):
    with closing(db.connect(db_path)) as conn, conn:
        conn.execute(
            "INSERT OR REPLACE INTO weekly_stats VALUES (?, ?, ?, ?, ?, ?, ?)",
            (barangay, disease, year, week, count, alert, epidemic),
        )


def add_node(db_path, node_id, kind="LIVE"):
    with closing(db.connect(db_path)) as conn, conn:
        conn.execute(
            "INSERT INTO nodes (node_id, barangay_id, registered_at, kind) VALUES (?, ?, 'now', ?)",
            (node_id, node_id.rsplit("-", 1)[0], kind),
        )


@pytest.fixture
def caraycaray_borders_catmon(monkeypatch):
    """Pretend Caraycaray (BRGY-08) and Catmon (BRGY-09) are neighbors."""
    barangays = [dict(b) for b in rules.barangays()]
    by_id = {b["barangay_id"]: b for b in barangays}
    by_id["BRGY-08"]["neighbors"] = ["BRGY-09"]
    by_id["BRGY-09"]["neighbors"] = ["BRGY-08"]
    monkeypatch.setattr(rules, "barangays", lambda: barangays)
    monkeypatch.setattr(rules, "barangays_by_id", lambda: by_id)


# ---- classification ----------------------------------------------------------

@pytest.mark.parametrize(
    ("count", "alert", "epidemic", "single", "expected"),
    [
        (5, 4.0, 6.0, False, ("ALERT", 4.0)),
        (7, 4.0, 6.0, False, ("EPIDEMIC", 6.0)),
        (4, 4.0, 6.0, False, None),          # must be above, not equal
        (2, 0.5, 1.0, False, None),          # below the 3-case minimum
        (9, None, None, False, None),        # no baseline yet
        (1, None, None, True, ("ALERT", 0.0)),  # measles: any case
        (0, None, None, True, None),
    ],
)
def test_classify(count, alert, epidemic, single, expected):
    assert classify(count, alert, epidemic, single) == expected


def test_weeks_to_check_are_previous_and_current():
    assert weeks_to_check(TODAY) == [(2026, 43), (2026, 44)]
    assert weeks_to_check(date(2027, 1, 6)) == [(2026, 53), (2027, 1)]


# ---- detection ------------------------------------------------------------------

def test_alert_is_created_once(db_path):
    put_stats(db_path, count=5)
    [alert] = detect_alerts(db_path, TODAY)
    assert (alert["level"], alert["observed"], alert["threshold"]) == ("ALERT", 5, 4.0)
    assert "Caraycaray" in alert["message"]

    put_stats(db_path, count=5)
    assert detect_alerts(db_path, TODAY) == []


def test_alert_escalates_to_epidemic_and_is_sent_again(db_path):
    put_stats(db_path, count=5)
    [first] = detect_alerts(db_path, TODAY)
    put_stats(db_path, count=9)
    [escalated] = detect_alerts(db_path, TODAY)

    assert escalated["alert_id"] == first["alert_id"]
    assert (escalated["level"], escalated["observed"]) == ("EPIDEMIC", 9)


def test_level_is_never_lowered(db_path):
    put_stats(db_path, count=9)
    detect_alerts(db_path, TODAY)
    put_stats(db_path, count=5)
    assert detect_alerts(db_path, TODAY) == []

    with closing(db.connect(db_path)) as conn:
        assert tuple(conn.execute("SELECT level, observed FROM alerts").fetchone()) == ("EPIDEMIC", 9)


def test_previous_week_is_checked_but_older_weeks_are_not(db_path):
    put_stats(db_path, count=9, week=43)
    put_stats(db_path, count=9, week=42, disease="ILI")
    assert [(a["iso_week"], a["disease_code"]) for a in detect_alerts(db_path, TODAY)] == [(43, "DEN")]


def test_single_measles_case_alerts(db_path):
    put_stats(db_path, count=1, alert=None, epidemic=None, disease="MEA")
    [alert] = detect_alerts(db_path, TODAY)
    assert alert["disease_code"] == "MEA"
    assert "immediate investigation" in alert["message"]


def test_recipients_are_local_and_neighbor_live_nodes(db_path, caraycaray_borders_catmon):
    add_node(db_path, "BRGY-08-AAAA")
    add_node(db_path, "BRGY-09-BBBB")
    add_node(db_path, "BRGY-10-CCCC")
    add_node(db_path, "BRGY-08-HIST", kind="HISTORY")

    assert sorted(recipients(db_path, "BRGY-08")) == [("BRGY-08-AAAA", "LOCAL"), ("BRGY-09-BBBB", "NEIGHBOR")]


def test_acknowledging_an_unknown_alert_is_ignored(db_path):
    add_node(db_path, NODE)
    assert acknowledge(db_path, "no-such-alert", NODE) is False


# ---- end to end: node → RHU → alert → node ---------------------------------------

def send_cases(client, records, drill=False):
    batch = {**make_batch(NODE, records=records), "drill": drill}
    client.post("/api/batches", json=batch)
    wait_for_stored(client, batch["batch_id"])


def hello(ws):
    ws.send_json({"type": "HELLO", "node_id": NODE, "pending_batches": 0})
    assert ws.receive_json()["type"] == "WELCOME"


def sync(ws):
    """Round trip that proves every earlier message from the node was processed."""
    ws.send_json({"type": "PING"})
    return ws.receive_json()


def test_measles_case_alerts_the_connected_node(registered):
    with registered.websocket_connect(f"/ws/node/{NODE}") as ws:
        hello(ws)
        send_cases(registered, [make_record(disease_code="MEA")])
        assert ws.receive_json()["type"] == "STORED"

        assert registered.post("/api/analytics/run").json()["alerts_sent"] == 1
        alert = ws.receive_json()
        assert (alert["type"], alert["disease_code"], alert["scope"]) == ("ALERT", "MEA", "LOCAL")

        ws.send_json({"type": "ALERT_ACK", "alert_id": alert["alert_id"]})
        sync(ws)

    [listed] = registered.get("/api/alerts").json()
    assert (listed["nodes_delivered"], listed["nodes_acknowledged"]) == (1, 1)


def test_drill_cases_do_not_raise_alerts(registered):
    send_cases(registered, [make_record(disease_code="MEA")], drill=True)
    assert registered.post("/api/analytics/run").json()["alerts_sent"] == 0
    assert registered.get("/api/alerts").json() == []


def test_offline_node_gets_the_alert_when_it_reconnects(registered):
    send_cases(registered, [make_record(disease_code="MEA")])
    registered.post("/api/analytics/run")  # the node is not connected yet

    with registered.websocket_connect(f"/ws/node/{NODE}") as ws:
        hello(ws)
        missed = ws.receive_json()
        assert (missed["type"], missed["acknowledged"]) == ("ALERT", False)


def test_acknowledged_alert_comes_back_marked_on_reconnect(registered):
    """A reloaded node page must still list its alerts, without announcing them again."""
    send_cases(registered, [make_record(disease_code="MEA")])
    with registered.websocket_connect(f"/ws/node/{NODE}") as ws:
        hello(ws)
        registered.post("/api/analytics/run")
        alert = ws.receive_json()
        assert alert["acknowledged"] is False
        ws.send_json({"type": "ALERT_ACK", "alert_id": alert["alert_id"]})
        sync(ws)

    with registered.websocket_connect(f"/ws/node/{NODE}") as ws:
        hello(ws)
        again = ws.receive_json()
        assert (again["alert_id"], again["acknowledged"]) == (alert["alert_id"], True)


def test_resolved_alert_is_not_sent_on_reconnect(registered):
    send_cases(registered, [make_record(disease_code="MEA")])
    registered.post("/api/analytics/run")
    [alert] = registered.get("/api/alerts").json()

    res = registered.post(f"/api/alerts/{alert['alert_id']}/status", json={"status": "RESOLVED"})
    assert res.json() == {"alert_id": alert["alert_id"], "status": "RESOLVED"}

    with registered.websocket_connect(f"/ws/node/{NODE}") as ws:
        hello(ws)
        assert sync(ws)["type"] == "ERROR"  # the reply to PING; no ALERT came before it


def test_alert_list_filters_by_status(registered):
    send_cases(registered, [make_record(disease_code="MEA")])
    registered.post("/api/analytics/run")
    assert len(registered.get("/api/alerts?status=OPEN").json()) == 1
    assert registered.get("/api/alerts?status=RESOLVED").json() == []


def test_unknown_alert_status_update_is_404(client):
    assert client.post("/api/alerts/nope/status", json={"status": "RESOLVED"}).status_code == 404

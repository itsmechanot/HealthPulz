"""Tests for outbreak alert detection and delivery."""

from contextlib import closing
from datetime import date

import pytest

from rhu import db, rules
from rhu.alerts import _describe, reword_old_messages, acknowledge, classify, detect_alerts, recipients, weeks_to_check
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
        (1, None, None, True, ("URGENT", 0.0)),  # the RHU's urgent list: any case
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
    put_stats(db_path, count=9, week=42, disease="ARI")
    assert [(a["iso_week"], a["disease_code"]) for a in detect_alerts(db_path, TODAY)] == [(43, "DEN")]


@pytest.mark.parametrize("disease", ["AFP", "DIP", "MEA", "NTT", "TET", "PER"])
def test_single_case_of_an_urgent_disease_alerts(db_path, disease):
    put_stats(db_path, count=1, alert=None, epidemic=None, disease=disease)
    [alert] = detect_alerts(db_path, TODAY)
    assert (alert["disease_code"], alert["level"]) == (disease, "URGENT")
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


# ---- "Other consultations": counted, but no thresholds, alerts or case totals -----

def test_other_consultations_are_counted_but_never_alert(registered):
    today = date.today().isoformat()
    send_cases(registered, [make_record(disease_code="OTH", onset_date=today, consult_date=today) for _ in range(9)])
    assert registered.post("/api/analytics/run").json()["alerts_sent"] == 0

    summary = registered.get("/api/stats/summary").json()
    assert summary["other_consultations"]["this_week"] == 9
    assert summary["totals"]["this_week"] == 0

    grid = registered.get("/api/stats/barangays").json()["barangays"]
    caraycaray = next(b for b in grid if b["barangay_id"] == "BRGY-08")
    assert (caraycaray["other_consultations"], caraycaray["total"]) == (9, 0)
    assert "OTH" not in caraycaray["diseases"]

    assert registered.get("/api/stats/trend?weeks=1").json()["weeks"][-1]["cases"] == 0
    other = registered.get("/api/stats/trend?disease=OTH&barangay_id=BRGY-08&weeks=1").json()["weeks"][-1]
    assert (other["cases"], other["alert_threshold"]) == (9, None)


def test_correction_leaves_the_alert_open_and_reports_the_new_count(registered):
    record = make_record(disease_code="MEA")
    send_cases(registered, [record])
    registered.post("/api/analytics/run")

    send_cases(registered, [{**record, "revision": 2, "voided": True}])
    assert registered.post("/api/analytics/run").json()["alerts_sent"] == 0

    [alert] = registered.get("/api/alerts").json()
    assert (alert["status"], alert["observed"], alert["current_count"]) == ("OPEN", 1, 0)


@pytest.mark.parametrize("disease", ["HTN", "ART", "KID", "WND"])
def test_non_communicable_illnesses_never_alert(registered, disease):
    today = date.today().isoformat()
    send_cases(registered, [make_record(disease_code=disease, onset_date=today, consult_date=today) for _ in range(30)])
    assert registered.post("/api/analytics/run").json()["alerts_sent"] == 0

    summary = registered.get("/api/stats/summary").json()
    assert next(d for d in summary["diseases"] if d["disease_code"] == disease)["this_week"] == 30
    assert summary["totals"]["this_week"] == 30  # counted like any tracked illness


# ---- real dates, not week codes ----------------------------------------------------------

def old_alert(conn, message):
    conn.execute(
        """INSERT INTO alerts (alert_id, disease_code, barangay_id, level, iso_year, iso_week, observed, threshold, message, status, created_at)
           VALUES ('old-1', 'DEN', 'BRGY-08', 'ALERT', 2026, 44, 9, 8.6, ?, 'ACKNOWLEDGED', '2026-10-30T00:00:00.000+00:00')""",
        (message,),
    )


def test_a_new_alert_message_gives_the_dates():
    disease = next(d for d in rules.diseases() if d["disease_code"] == "DEN")
    message = _describe(disease, "BRGY-08", 2026, 44, 9, "ALERT", 8.6)
    assert message == "Dengue cases in Brgy. Caraycaray in the week of Oct 26 – Nov 1, 2026: 9, above the alert threshold of 8.6."


def test_an_urgent_alert_message_gives_the_dates():
    disease = next(d for d in rules.diseases() if d["single_case_alert"])
    message = _describe(disease, "BRGY-08", 2026, 45, 1, "URGENT", 0)
    assert "in the week of Nov 2 – 8, 2026." in message
    assert "-W" not in message


def test_old_alert_messages_are_reworded_and_nothing_else_changes(tmp_path):
    db_path = tmp_path / "old.db"
    db.init_db(db_path)
    with closing(db.connect(db_path)) as conn, conn:
        old_alert(conn, "Dengue cases in Brgy. Caraycaray in week 2026-W44: 9, above the alert threshold of 8.6.")

    assert reword_old_messages(db_path) == 1
    assert reword_old_messages(db_path) == 0  # nothing left to do on the next start

    with closing(db.connect(db_path)) as conn:
        row = dict(conn.execute("SELECT * FROM alerts").fetchone())
    assert row["message"] == "Dengue cases in Brgy. Caraycaray in the week of Oct 26 – Nov 1, 2026: 9, above the alert threshold of 8.6."
    assert (row["status"], row["observed"], row["iso_week"]) == ("ACKNOWLEDGED", 9, 44)


def test_the_alert_list_gives_the_dates(client):
    with closing(db.connect(client.app.state.db_path)) as conn, conn:
        old_alert(conn, "Some message")
    assert client.get("/api/alerts").json()[0]["week_dates"] == "Oct 26 – Nov 1, 2026"


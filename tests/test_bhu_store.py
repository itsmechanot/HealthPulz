"""Tests for the BHU records: one account per barangay, reports to the RHU through an outbox."""

import sqlite3
import threading
import time
import uuid
from contextlib import closing
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from tests.factories import create_test_app

from rhu.bhu_store import BhuStores, OutboxRelay, age_on, station_id
from rhu.ingest import PipelineBusy

BARANGAY = "BRGY-08"
OTHER_BARANGAY = "BRGY-01"
TODAY = date.today().isoformat()

CLIENT = {
    "last_name": "Dela Cruz", "first_name": "Maria", "middle_name": "", "birthdate": "2014-03-15", "sex": "F",
    "civil_status": "", "purok": 3, "address": "Sitio Mabuhay", "contact": "09171234567", "philhealth": "12-345",
}
DENGUE = {
    "visit_date": TODAY, "onset_date": TODAY, "chief_complaint": "Fever for 3 days", "temperature": "38.9", "bp_systolic": "",
    "findings": "Positive tourniquet test", "diagnosis_code": "DEN", "case_status": "SUSPECTED", "treatment": "Paracetamol",
}


def url(path, barangay=BARANGAY):
    return f"/api/bhu/{barangay}{path}"


def add_client(client, fields=CLIENT, barangay=BARANGAY):
    client_id = str(uuid.uuid4())
    res = client.put(url(f"/clients/{client_id}", barangay), json=fields)
    assert res.status_code == 200, res.text
    return client_id


def add_visit(client, client_id, fields=DENGUE, barangay=BARANGAY):
    consultation_id = str(uuid.uuid4())
    res = client.put(url(f"/clients/{client_id}/consultations/{consultation_id}", barangay), json=fields)
    assert res.status_code == 200, res.text
    return consultation_id


def wait_for_sync(client, consultation_id, expected="STORED", barangay=BARANGAY):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        visit = client.get(url(f"/consultations/{consultation_id}", barangay)).json()
        if visit["sync"] == expected:
            return visit
        time.sleep(0.03)
    raise AssertionError(f"checkup never reached {expected}: {visit['sync']}")


def rhu_cases(client):
    with closing(sqlite3.connect(client.app.state.db_path)) as conn:
        return conn.execute(
            "SELECT disease_code, age_years, sex, purok, case_status, voided, node_id FROM cases ORDER BY rowid"
        ).fetchall()


def test_age_counts_completed_years():
    assert age_on("2014-03-15", "2026-03-14") == 11
    assert age_on("2014-03-15", "2026-03-15") == 12


def test_records_belong_to_the_barangay_not_to_a_device(client):
    client_id = add_client(client)
    consultation_id = add_visit(client, client_id)

    # Any device asking for this barangay gets the same records; there is no per-device state.
    assert [c["client_id"] for c in client.get(url("/clients")).json()] == [client_id]
    assert [v["consultation_id"] for v in client.get(url("/consultations")).json()] == [consultation_id]


def test_barangays_cannot_see_each_others_records(client):
    client_id = add_client(client)
    assert client.get(url("/clients", OTHER_BARANGAY)).json() == []
    assert client.get(url(f"/clients/{client_id}", OTHER_BARANGAY)).status_code == 404


def test_unknown_barangay_is_refused(client):
    assert client.get("/api/bhu/BRGY-99/clients").status_code == 404
    assert client.get("/api/bhu/..%2F..%2Fhealthpulz/clients").status_code == 404


def test_checkup_reaches_the_rhu_without_identifying_details(client):
    client_id = add_client(client)
    wait_for_sync(client, add_visit(client, client_id))

    assert rhu_cases(client) == [("DEN", age_on("2014-03-15", TODAY), "F", 3, "SUSPECTED", 0, "BRGY-08-MAIN")]
    with closing(sqlite3.connect(client.app.state.db_path)) as conn:
        dump = "\n".join(conn.iterdump())
    for private in ("Dela Cruz", "Maria", "09171234567", "Sitio Mabuhay", "2014-03-15", "Fever for 3 days", "tourniquet", "Paracetamol"):
        assert private not in dump


def test_saving_a_checkup_twice_records_it_once(client):
    client_id = add_client(client)
    consultation_id = str(uuid.uuid4())
    path = url(f"/clients/{client_id}/consultations/{consultation_id}")
    client.put(path, json=DENGUE)
    client.put(path, json=DENGUE)

    assert len(client.get(url("/consultations")).json()) == 1
    wait_for_sync(client, consultation_id)
    assert len(rhu_cases(client)) == 1


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"visit_date": "2000-01-01"}, "before the client's birthdate"),
        ({"visit_date": (date.today() + timedelta(days=5)).isoformat()}, "future"),
        ({"onset_date": ""}, "onset is required"),
        ({"diagnosis_code": "XYZ"}, "Unknown diagnosis"),
        ({"diagnosis_code": "OTH", "diagnosis_text": " "}, "specify"),
    ],
)
def test_invalid_checkups_are_refused_with_a_readable_reason(client, changes, message):
    client_id = add_client(client)
    res = client.put(url(f"/clients/{client_id}/consultations/{uuid.uuid4()}"), json={**DENGUE, **changes})
    assert res.status_code == 400
    assert message in res.json()["detail"]


def test_vital_signs_out_of_range_are_refused(client):
    client_id = add_client(client)
    assert client.put(url(f"/clients/{client_id}/consultations/{uuid.uuid4()}"), json={**DENGUE, "temperature": "90"}).status_code == 400


def test_other_diagnosis_reaches_the_rhu_without_its_text(client):
    client_id = add_client(client)
    fields = {"visit_date": TODAY, "chief_complaint": "Headache", "diagnosis_code": "OTH", "diagnosis_text": "Migraine"}
    wait_for_sync(client, add_visit(client, client_id, fields))

    [case] = rhu_cases(client)
    assert case[0] == "OTH"
    with closing(sqlite3.connect(client.app.state.db_path)) as conn:
        assert "Migraine" not in "\n".join(conn.iterdump())


def test_correction_replaces_the_rhu_case_and_keeps_a_history(client):
    client_id = add_client(client)
    consultation_id = add_visit(client, client_id)
    wait_for_sync(client, consultation_id)

    res = client.post(url(f"/consultations/{consultation_id}/correct"), json={
        "fields": {**DENGUE, "diagnosis_code": "ARI"},
        "reason": "Test came back negative",
        "changes": [{"field": "Diagnosis", "from": "Dengue", "to": "Acute respiratory tract infection"}],
    })
    assert res.status_code == 200, res.text
    visit = wait_for_sync(client, consultation_id)

    assert [c[0] for c in rhu_cases(client)] == ["ARI"]
    assert visit["history"][0]["reason"] == "Test came back negative"
    assert visit["history"][0]["changes"] == [{"field": "Diagnosis", "from": "Dengue", "to": "Acute respiratory tract infection"}]


def test_void_keeps_the_record_and_the_rhu_stops_counting(client):
    client_id = add_client(client)
    consultation_id = add_visit(client, client_id)
    wait_for_sync(client, consultation_id)

    client.post(url(f"/consultations/{consultation_id}/void"), json={"reason": "Wrong client"})
    visit = wait_for_sync(client, consultation_id)

    assert visit["voided"] is True
    assert rhu_cases(client)[0][5] == 1
    res = client.post(url(f"/consultations/{consultation_id}/correct"), json={
        "fields": DENGUE, "reason": "x", "changes": [{"field": "a", "from": "b", "to": "c"}],
    })
    assert res.status_code == 400


def test_correcting_birthdate_or_sex_resends_reports(client):
    client_id = add_client(client)
    consultation_id = add_visit(client, client_id)
    wait_for_sync(client, consultation_id)

    client.put(url(f"/clients/{client_id}"), json={**CLIENT, "birthdate": "2010-03-15", "sex": "M"})
    visit = wait_for_sync(client, consultation_id)

    assert visit["history"][-1]["action"] == "CLIENT_CORRECTED"
    assert rhu_cases(client)[0][1:3] == (age_on("2010-03-15", TODAY), "M")


def test_moving_purok_does_not_change_past_reports(client):
    client_id = add_client(client)
    consultation_id = add_visit(client, client_id)
    wait_for_sync(client, consultation_id)
    client.put(url(f"/clients/{client_id}"), json={**CLIENT, "purok": 5})

    assert client.get(url(f"/consultations/{consultation_id}")).json()["history"] == []
    assert rhu_cases(client)[0][3] == 3


def test_birthdate_after_a_checkup_is_refused(client):
    client_id = add_client(client)
    ten_days_ago = (date.today() - timedelta(days=10)).isoformat()
    add_visit(client, client_id, {**DENGUE, "visit_date": ten_days_ago, "onset_date": ten_days_ago})
    res = client.put(url(f"/clients/{client_id}"), json={**CLIENT, "birthdate": (date.today() - timedelta(days=5)).isoformat()})
    assert res.status_code == 400
    assert "before this birthdate" in res.json()["detail"]


def test_backup_round_trip_and_wrong_barangay(client):
    client_id = add_client(client)
    consultation_id = add_visit(client, client_id)
    wait_for_sync(client, consultation_id)
    backup = client.get(url("/backup")).json()

    assert client.post(url("/backup", OTHER_BARANGAY), json=backup).status_code == 400
    assert client.post(url("/backup"), json={"format": "nope"}).status_code == 400

    assert client.post(url("/backup"), json=backup).status_code == 200
    assert len(client.get(url("/consultations")).json()) == 1
    assert len(rhu_cases(client)) == 1


def test_records_moved_from_a_browser_are_sent_to_the_rhu_once(client):
    client_id, consultation_id, batch_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    moved = {
        "format": "healthpulz-bhu-backup", "barangay_id": BARANGAY,
        "clients": [{**CLIENT, "client_id": client_id, "barangay_id": BARANGAY, "created_at": "2026-11-01", "updated_at": "2026-11-01"}],
        "consultations": [{
            **DENGUE, "consultation_id": consultation_id, "client_id": client_id, "barangay_id": BARANGAY,
            "batch_id": batch_id, "sync": "WAITING", "created_at": "2026-11-01",
        }],
    }
    res = client.post(url("/backup"), json=moved)
    assert res.json() == {"clients": 1, "consultations": 1}
    wait_for_sync(client, consultation_id)
    assert len(rhu_cases(client)) == 1

    client.post(url("/backup"), json=moved)  # moving twice changes nothing
    assert len(client.get(url("/consultations")).json()) == 1
    assert client.get(url(f"/consultations/{consultation_id}")).json()["sync"] == "STORED"


def test_report_left_in_the_outbox_is_delivered_after_a_restart(tmp_path):
    db_path = tmp_path / "test.db"
    with TestClient(create_test_app(db_path)) as first:
        client_id = add_client(first)

    # The server stops right after the commit, before the relay could send the report.
    stores = BhuStores(tmp_path / "bhu")
    consultation_id = str(uuid.uuid4())
    stores.save_consultation(BARANGAY, client_id, consultation_id, {**DENGUE, "temperature": 38.9, "bp_systolic": None})
    assert len(stores.pending_reports(BARANGAY)) == 1

    with TestClient(create_test_app(db_path)) as second:
        second.post("/api/nodes/register", json={"type": "REGISTER", "node_id": "BRGY-08-MAIN", "barangay_id": BARANGAY})
        wait_for_sync(second, consultation_id)
        assert len(rhu_cases(second)) == 1
    assert stores.pending_reports(BARANGAY) == []


def test_relay_retries_when_the_pipeline_is_busy(tmp_path):
    class BusyOnce:
        calls = 0

        def submit(self, batch):
            self.calls += 1
            if self.calls == 1:
                raise PipelineBusy

    stores = BhuStores(tmp_path)
    client_id = str(uuid.uuid4())
    stores.save_client(BARANGAY, client_id, {**CLIENT, "purok": 3})
    stores.save_consultation(BARANGAY, client_id, str(uuid.uuid4()), DENGUE | {"temperature": None, "bp_systolic": None})

    pipeline = BusyOnce()
    relay = OutboxRelay(stores, pipeline)
    relay._send_pending()
    assert len(stores.pending_reports(BARANGAY)) == 1
    relay._send_pending()
    assert pipeline.calls == 2


def test_station_id_is_fixed_per_barangay():
    assert station_id("BRGY-08") == "BRGY-08-MAIN"


def test_monitor_counts_devices_on_one_station(registered):
    registered.post("/api/nodes/register", json={"type": "REGISTER", "node_id": "BRGY-01-MAIN", "barangay_id": "BRGY-01"})
    hello = {"type": "HELLO", "node_id": "BRGY-01-MAIN", "pending_batches": 0}
    with registered.websocket_connect("/ws/node/BRGY-01-MAIN") as phone, registered.websocket_connect("/ws/node/BRGY-01-MAIN") as laptop:
        for device in (phone, laptop):
            device.send_json(hello)
            device.receive_json()
        with registered.websocket_connect("/ws/monitor") as monitor:
            nodes = {n["node_id"]: n for n in monitor.receive_json()["nodes"]}
    assert nodes["BRGY-01-MAIN"]["connections"] == 2


def test_first_use_of_a_barangay_survives_simultaneous_requests(tmp_path):
    """A page loads clients and checkups at once; both must wait for the tables."""
    stores = BhuStores(tmp_path)
    errors = []

    def first_request(barangay_id, read):
        try:
            read(barangay_id)
        except Exception as error:
            errors.append(error)

    threads = [
        threading.Thread(target=first_request, args=(f"BRGY-{i:02d}", read))
        for i in range(1, 27)
        for read in (stores.list_clients, stores.list_consultations, stores.pending_reports) * 3
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []

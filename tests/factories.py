"""Builders for protocol messages and other helpers used across the tests."""

import time
import uuid
from datetime import date, datetime, timedelta, timezone

from rhu import auth
from rhu.accounts import Session
from rhu.main import create_app

NODE = "BRGY-08-K7QX"


class AllAccess(Session):
    """Lets a test reach every endpoint without logging in. test_auth.py covers the real rules."""

    is_rhu = True

    def may_open_records(self, barangay_id: str) -> bool:
        return True

    def may_send_for(self, barangay_id: str) -> bool:
        return True


def create_test_app(db_path):
    # One analytics worker: starting worker processes is slow on Windows (test_analytics covers the pool).
    # A long interval keeps the scheduler to its startup run; tests trigger runs via POST /api/analytics/run.
    app = create_app(db_path, analytics_workers=1, analytics_interval=3600)
    app.dependency_overrides[auth.optional_session] = lambda: AllAccess("test", "TEST")
    return app


def make_record(**overrides) -> dict:
    record = {
        "record_id": str(uuid.uuid4()),
        "disease_code": "DEN",
        "onset_date": (date.today() - timedelta(days=3)).isoformat(),
        "consult_date": (date.today() - timedelta(days=1)).isoformat(),
        "age_years": 12,
        "sex": "F",
        "purok": 3,
        "case_status": "SUSPECTED",
    }
    return {**record, **overrides}


def make_batch(node_id: str = NODE, records: list[dict] | None = None, count: int = 10) -> dict:
    return {
        "type": "SUBMIT_BATCH",
        "protocol_version": 1,
        "node_id": node_id,
        "batch_id": str(uuid.uuid4()),
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "records": records if records is not None else [make_record() for _ in range(count)],
    }


def wait_for_stored(client, batch_id, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"/api/batches/{batch_id}").json()
        if body["status"] == "STORED":
            return body
        time.sleep(0.02)
    raise AssertionError(f"batch {batch_id} was not stored within {timeout}s")

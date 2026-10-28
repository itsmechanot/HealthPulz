"""Builders for protocol messages used across the tests."""

import uuid
from datetime import date, datetime, timedelta, timezone


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


def make_batch(node_id: str = "BRGY-08-K7QX", records: list[dict] | None = None, count: int = 10) -> dict:
    return {
        "type": "SUBMIT_BATCH",
        "protocol_version": 1,
        "node_id": node_id,
        "batch_id": str(uuid.uuid4()),
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "records": records if records is not None else [make_record() for _ in range(count)],
    }

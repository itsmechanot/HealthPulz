"""Tests for the ingestion pipeline threads, without going through HTTP."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import date, timedelta

import pytest

from rhu import db
from rhu.ingest import IngestPipeline, PipelineBusy
from rhu.models import SubmitBatch
from tests.factories import make_batch, make_record

NODE = "BRGY-08-K7QX"


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    db.init_db(path)
    add_nodes(path, [NODE])
    return path


def add_nodes(db_path, node_ids):
    with closing(db.connect(db_path)) as conn, conn:
        conn.executemany(
            "INSERT INTO nodes (node_id, barangay_id, registered_at) VALUES (?, ?, ?)",
            [(n, n.rsplit("-", 1)[0], db.utc_now()) for n in node_ids],
        )


def count(db_path, table):
    with closing(db.connect(db_path)) as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def run(db_path, batches, **kwargs):
    """Push batches through a pipeline, drain it, and return (pipeline, STORED messages)."""
    stored = []
    pipeline = IngestPipeline(db_path, on_stored=lambda node_id, msg: stored.append(msg), **kwargs)
    pipeline.start()
    for batch in batches:
        pipeline.submit(SubmitBatch.model_validate(batch))
    pipeline.stop()
    return pipeline, stored


def test_valid_batch_is_stored(db_path):
    batch = make_batch(NODE, count=10)
    _, stored = run(db_path, [batch])

    assert stored == [
        {"type": "STORED", "batch_id": batch["batch_id"], "accepted": 10, "duplicates": 0, "rejected": []}
    ]
    assert count(db_path, "cases") == 10


def test_invalid_records_are_rejected_and_the_rest_kept(db_path):
    good = make_record()
    tomorrow_plus_one = (date.today() + timedelta(days=2)).isoformat()
    batch = make_batch(NODE, records=[
        good,
        make_record(record_id=good["record_id"]),
        make_record(disease_code="XYZ"),
        make_record(onset_date="2026-11-10", consult_date="2026-11-09"),
        make_record(onset_date=tomorrow_plus_one, consult_date=tomorrow_plus_one),
    ])
    _, [message] = run(db_path, [batch])

    assert message["accepted"] == 1
    assert [r["reason"] for r in message["rejected"]] == [
        "DUPLICATE_IN_BATCH", "INVALID_DISEASE_CODE", "CONSULT_BEFORE_ONSET", "FUTURE_DATE",
    ]
    assert count(db_path, "cases") == 1


def test_record_sent_again_in_a_new_batch_is_a_duplicate(db_path):
    record = make_record()
    _, stored = run(db_path, [make_batch(NODE, records=[record]), make_batch(NODE, records=[record])])

    assert [(m["accepted"], m["duplicates"]) for m in stored] == [(1, 0), (0, 1)]
    assert count(db_path, "cases") == 1


def test_resent_batch_is_not_stored_twice(db_path):
    batch = make_batch(NODE, count=5)
    pipeline, stored = run(db_path, [batch, batch])

    # Both attempts get STORED (so the node can clear its queue), with the original counts.
    assert [m["accepted"] for m in stored] == [5, 5]
    assert count(db_path, "cases") == 5
    assert count(db_path, "batches") == 1
    assert pipeline.stats.snapshot()["batches_resent"] == 1


def test_full_queue_refuses_new_batches(db_path):
    pipeline = IngestPipeline(db_path, queue_size=2)  # not started, so nothing drains the queue
    pipeline.submit(SubmitBatch.model_validate(make_batch(NODE)))
    pipeline.submit(SubmitBatch.model_validate(make_batch(NODE)))

    with pytest.raises(PipelineBusy):
        pipeline.submit(SubmitBatch.model_validate(make_batch(NODE)))
    assert pipeline.stats.snapshot()["batches_busy"] == 1


def test_burst_from_40_nodes_loses_nothing(db_path):
    """The demo scenario: 40 nodes send 5 batches of 100 records each at the same time."""
    nodes = [f"BRGY-{i % 26 + 1:02d}-N{i:03d}" for i in range(40)]
    add_nodes(db_path, nodes)
    stored = []
    pipeline = IngestPipeline(db_path, on_stored=lambda node_id, msg: stored.append(msg))
    pipeline.start()

    def node_sends(node_id):
        for _ in range(5):
            pipeline.submit(SubmitBatch.model_validate(make_batch(node_id, count=100)))

    with ThreadPoolExecutor(max_workers=len(nodes)) as executor:
        list(executor.map(node_sends, nodes))
    pipeline.stop()

    assert count(db_path, "cases") == 40 * 5 * 100
    assert count(db_path, "batches") == 200
    assert len(stored) == 200
    stats = pipeline.stats.snapshot()
    assert stats["records_accepted"] == 20_000
    assert stats["batches_busy"] == 0

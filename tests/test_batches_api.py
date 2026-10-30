"""Tests for the batch endpoints (HTTP side of the ingestion pipeline)."""

from rhu.ingest import PipelineBusy
from tests.factories import NODE, make_batch, wait_for_stored


def test_batch_is_received_then_stored(registered):
    batch = make_batch(NODE, count=10)
    res = registered.post("/api/batches", json=batch)

    assert res.status_code == 202
    assert res.json()["type"] == "RECEIVED"
    status = wait_for_stored(registered, batch["batch_id"])
    assert (status["accepted"], status["duplicates"], status["rejected"]) == (10, 0, 0)


def test_unknown_batch_is_pending(client):
    res = client.get("/api/batches/6f1c2e0a-8b7d-4c1e-9a55-1f2d3c4b5a60")
    assert res.json()["status"] == "PENDING"


def test_unregistered_node_is_refused(client):
    assert client.post("/api/batches", json=make_batch(NODE)).status_code == 404


def test_batch_over_the_record_limit_is_malformed(registered):
    assert registered.post("/api/batches", json=make_batch(NODE, count=101)).status_code == 400


def test_wrong_protocol_version_is_malformed(registered):
    batch = {**make_batch(NODE), "protocol_version": 2}
    assert registered.post("/api/batches", json=batch).status_code == 400


def test_full_queue_returns_503_with_retry_after(registered):
    def always_busy(batch):
        raise PipelineBusy

    registered.app.state.pipeline.submit = always_busy
    res = registered.post("/api/batches", json=make_batch(NODE))

    assert res.status_code == 503
    assert res.headers["Retry-After"] == "2"
    assert res.json()["detail"] == {"type": "BUSY", "retry_after": 2}


def test_monitor_stats(registered):
    batch = make_batch(NODE, count=3)
    registered.post("/api/batches", json=batch)
    wait_for_stored(registered, batch["batch_id"])

    stats = registered.get("/api/monitor/stats").json()
    assert stats["batches_received"] == 1
    assert stats["records_accepted"] == 3
    assert stats["validator_threads"] == 4
    assert {"ingest_queue", "write_queue"} <= stats.keys()

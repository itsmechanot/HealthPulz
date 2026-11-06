"""Tests for the ingestion pipeline threads, without going through HTTP."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import date, timedelta

import pytest

from rhu import db, reports
from rhu.history import import_history
from rhu.ingest import IngestPipeline, PipelineBusy
from rhu.models import SubmitBatch
from tests.factories import NODE, make_batch, make_record


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
        {"type": "STORED", "batch_id": batch["batch_id"], "accepted": 10, "updated": 0, "duplicates": 0, "rejected": []}
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
    stats = pipeline.stats.snapshot()
    assert stats["batches_resent"] == 1
    assert stats["records_received"] == 10
    assert (stats["records_accepted"], stats["records_duplicate"]) == (5, 5)


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
    assert stats["records_accepted"] == stats["records_received"] == 20_000
    assert stats["batches_busy"] == 0


def test_every_received_record_is_accounted_for(db_path):
    """The System Monitor's zero-loss check: received = accepted + duplicate + rejected."""
    record = make_record()
    batches = [
        make_batch(NODE, records=[record, make_record(disease_code="XYZ"), make_record()]),
        make_batch(NODE, records=[record]),  # duplicate record in a new batch
    ]
    batches.append(batches[0])  # the same batch resent
    pipeline, _ = run(db_path, batches)

    s = pipeline.stats.snapshot()
    assert s["records_received"] == 7
    assert s["records_received"] == s["records_accepted"] + s["records_updated"] + s["records_duplicate"] + s["records_rejected"]


# ---- corrections and voids ----------------------------------------------------------

def case_row(db_path, record_id):
    with closing(db.connect(db_path)) as conn:
        row = conn.execute(
            "SELECT disease_code, age_years, revision, voided FROM cases WHERE record_id = ?", (record_id,)
        ).fetchone()
    return tuple(row)


def test_higher_revision_replaces_the_case(db_path):
    record = make_record(disease_code="DEN", age_years=12)
    correction = {**record, "disease_code": "ARI", "age_years": 13, "revision": 5}
    pipeline, stored = run(db_path, [make_batch(NODE, records=[record]), make_batch(NODE, records=[correction])])

    assert [(m["accepted"], m["updated"], m["duplicates"]) for m in stored] == [(1, 0, 0), (0, 1, 0)]
    assert case_row(db_path, record["record_id"]) == ("ARI", 13, 5, 0)
    assert count(db_path, "cases") == 1
    assert pipeline.stats.snapshot()["records_updated"] == 1


def test_late_older_revision_cannot_undo_a_correction(db_path):
    record = make_record(disease_code="DEN")
    newer = {**record, "disease_code": "ARI", "revision": 9}
    older = {**record, "disease_code": "DIA", "revision": 4}
    _, stored = run(db_path, [make_batch(NODE, records=[newer]), make_batch(NODE, records=[older]), make_batch(NODE, records=[record])])

    assert [(m["accepted"], m["updated"], m["duplicates"]) for m in stored] == [(0, 1, 0), (0, 0, 1), (0, 0, 1)]
    disease, _, revision, _ = case_row(db_path, record["record_id"])
    assert (disease, revision) == ("ARI", 9)


def test_same_revision_sent_again_is_a_duplicate(db_path):
    record = make_record(disease_code="DEN", revision=3)
    _, stored = run(db_path, [make_batch(NODE, records=[record]), make_batch(NODE, records=[record])])
    assert [(m["updated"], m["duplicates"]) for m in stored] == [(1, 0), (0, 1)]


def test_void_keeps_the_row(db_path):
    record = make_record()
    run(db_path, [make_batch(NODE, records=[record]), make_batch(NODE, records=[{**record, "revision": 2, "voided": True}])])
    assert case_row(db_path, record["record_id"])[3] == 1
    assert count(db_path, "cases") == 1


def test_another_barangay_cannot_change_a_case(db_path):
    other = "BRGY-01-AAAA"
    add_nodes(db_path, [other])
    record = make_record(disease_code="DEN")
    _, stored = run(db_path, [make_batch(NODE, records=[record]), make_batch(other, records=[{**record, "disease_code": "ARI", "revision": 2}])])
    assert stored[1]["duplicates"] == 1
    assert case_row(db_path, record["record_id"])[0] == "DEN"


def test_resent_correction_batch_keeps_the_accounting_balanced(db_path):
    record = make_record()
    correction = make_batch(NODE, records=[{**record, "revision": 2}])
    pipeline, stored = run(db_path, [make_batch(NODE, records=[record]), correction, correction])

    assert stored[2]["updated"] == 1  # the original counts of that batch
    s = pipeline.stats.snapshot()
    assert s["records_received"] == 3
    assert s["records_received"] == s["records_accepted"] + s["records_updated"] + s["records_duplicate"] + s["records_rejected"]


# ---- latest reports --------------------------------------------------------------------

def run_batches(db_path, *batches):
    """Send the batches through the threads, in order, and wait until all are stored."""
    pipeline = IngestPipeline(db_path)
    pipeline.start()
    for batch in batches:
        pipeline.submit(SubmitBatch(**batch))
    pipeline.stop()
    return pipeline


def listed_ids(pipeline):
    return [r["record_id"] for r in pipeline.latest_reports.snapshot()]


def test_a_stored_report_is_listed_without_private_details(db_path):
    record = make_record(disease_code="FEV", onset_date="2026-11-01", consult_date="2026-11-06", age_years=22, sex="M", purok=1)
    [report] = run_batches(db_path, make_batch(NODE, records=[record])).latest_reports.snapshot()

    assert report["barangay_id"] == "BRGY-08"
    assert report["disease_code"] == "FEV"
    assert report["counted_in_week"] == "2026-W45"  # the checkup's week; the illness started in W44
    assert report["counted_week_dates"] == "Nov 2 – 8, 2026"
    assert (report["age_years"], report["sex"], report["purok"]) == (22, "M", 1)
    assert report["received_at"]
    assert not {"name", "last_name", "first_name", "address", "contact", "findings"} & report.keys()


def test_newest_reports_come_first_and_the_list_stays_short(db_path):
    records = [make_record() for _ in range(reports.MAX_REPORTS + 5)]
    pipeline = run_batches(db_path, make_batch(NODE, records=records))
    assert listed_ids(pipeline) == [r["record_id"] for r in reversed(records[5:])]


def test_test_data_is_not_listed(db_path):
    assert listed_ids(run_batches(db_path, {**make_batch(NODE, count=3), "drill": True})) == []


def test_rejected_records_are_not_listed(db_path):
    bad = make_record(disease_code="NOPE")
    good = make_record()
    assert listed_ids(run_batches(db_path, make_batch(NODE, records=[bad, good]))) == [good["record_id"]]


def test_a_resent_batch_is_listed_once(db_path):
    batch = make_batch(NODE, count=2)
    assert len(listed_ids(run_batches(db_path, batch, batch))) == 2


def test_a_correction_replaces_the_earlier_entry(db_path):
    record = make_record(disease_code="DEN")
    corrected = {**record, "disease_code": "TYP", "revision": 1}
    [report] = run_batches(
        db_path, make_batch(NODE, records=[record]), make_batch(NODE, records=[corrected]),
    ).latest_reports.snapshot()
    assert (report["disease_code"], report["revision"]) == ("TYP", 1)


def test_a_void_shows_on_the_entry(db_path):
    record = make_record()
    voided = {**record, "revision": 1, "voided": True}
    [report] = run_batches(db_path, make_batch(NODE, records=[record]), make_batch(NODE, records=[voided])).latest_reports.snapshot()
    assert report["voided"] is True


def test_the_list_survives_a_restart_without_test_data_or_history(db_path):
    real = make_record()
    run_batches(db_path, make_batch(NODE, records=[real]), {**make_batch(NODE, count=3), "drill": True})
    import_history(db_path, years=1)

    assert listed_ids(run_batches(db_path)) == [real["record_id"]]


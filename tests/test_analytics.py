"""Tests for the parallel analytics (weekly counts and thresholds)."""

import sqlite3
import time
from collections import Counter
from contextlib import closing
from datetime import date, timedelta

import pytest

from fastapi.testclient import TestClient

from rhu import auth, config, db, rules
from rhu.analytics import ANALYTICS_QUERY, Analytics, _cases_signature, _stored_rows, _write_changed_rows, baseline_values
from rhu.history import import_history
from rhu.ingest import IngestPipeline
from rhu.models import SubmitBatch
from rhu.main import create_app
from tests.factories import NODE, AllAccess, make_batch, make_record, wait_for_stored

TODAY = date(2026, 10, 30)
FIRST_WEEK = date.fromisocalendar(2023, 1, 1)


@pytest.fixture(scope="module")
def history(tmp_path_factory):
    db_path = tmp_path_factory.mktemp("analytics") / "history.db"
    return db_path, import_history(db_path, years=3, today=TODAY)


def weekly_stats(db_path):
    with closing(sqlite3.connect(db_path)) as conn:
        return conn.execute("SELECT * FROM weekly_stats ORDER BY 1, 2, 3, 4").fetchall()


def test_baseline_uses_same_week_plus_minus_one_in_past_years():
    counts = Counter({("DEN", date.fromisocalendar(2025, 10, 1)): 4, ("DEN", date.fromisocalendar(2024, 11, 1)): 2})

    values = baseline_values(counts, "DEN", date.fromisocalendar(2026, 10, 1), FIRST_WEEK)

    assert len(values) == 9  # 3 past years × 3 weeks
    assert sorted(values)[-2:] == [2, 4]


def test_baseline_needs_two_past_years():
    counts = Counter()
    assert len(baseline_values(counts, "DEN", date.fromisocalendar(2025, 10, 1), FIRST_WEEK)) == 6
    assert baseline_values(counts, "DEN", date.fromisocalendar(2024, 10, 1), FIRST_WEEK) is None


def test_week_53_uses_the_last_week_of_shorter_years():
    # 2026 has 53 ISO weeks; 2025 and 2024 have 52.
    values = baseline_values(Counter(), "DEN", date.fromisocalendar(2026, 53, 1), FIRST_WEEK)
    assert len(values) == 9


def test_sequential_run(history):
    db_path, imported = history
    result = Analytics(db_path, workers=1).run(today=TODAY)

    weeks = (date.fromisocalendar(2026, 44, 1) - FIRST_WEEK).days // 7 + 1  # up to the current week
    assert result.partitions == 26
    assert result.rows == 26 * 20 * weeks  # 19 tracked diseases + Other consultations
    assert result.cases == imported.cases

    rows = weekly_stats(db_path)
    assert sum(r[4] for r in rows) == imported.cases
    assert all(r[5] is None for r in rows if r[2] == 2024)      # only 1 past year
    thresholded = {d["disease_code"] for d in rules.diseases() if d["threshold_alerts"]}
    assert all((r[5] is not None) == (r[1] in thresholded) for r in rows if r[2] == 2026)  # 3 past years


def test_parallel_gives_the_same_result_as_sequential(history):
    db_path, _ = history
    Analytics(db_path, workers=1).run(today=TODAY)
    sequential = weekly_stats(db_path)

    analytics = Analytics(db_path, workers=2)
    try:
        result = analytics.run(today=TODAY)
    finally:
        analytics.close()

    assert result.workers == 2
    assert weekly_stats(db_path) == sequential


def test_injected_outbreaks_go_over_the_alert_threshold(history):
    db_path, imported = history
    Analytics(db_path, workers=1).run(today=TODAY)
    with closing(sqlite3.connect(db_path)) as conn:
        def exceeded(outbreak):
            for w in range(outbreak.weeks):
                year, week, _ = (outbreak.start + timedelta(weeks=w)).isocalendar()
                count, threshold = conn.execute(
                    "SELECT case_count, alert_threshold FROM weekly_stats "
                    "WHERE barangay_id = ? AND disease_code = ? AND iso_year = ? AND iso_week = ?",
                    (outbreak.barangay_id, outbreak.disease_code, year, week),
                ).fetchone()
                if threshold is not None and count > threshold:
                    return True
            return False

        checkable = [o for o in imported.outbreaks if o.start.year >= 2025]  # thresholds exist from 2025
        detected = [o for o in checkable if exceeded(o)]

    assert checkable
    assert len(detected) / len(checkable) >= 0.75


def test_run_with_no_cases(tmp_path):
    db_path = tmp_path / "empty.db"
    db.init_db(db_path)
    result = Analytics(db_path, workers=1).run(today=TODAY)
    assert (result.cases, result.rows) == (0, 0)


def test_analytics_endpoints(client):
    run = client.post("/api/analytics/run").json()["last_run"]
    assert run["workers"] == 1
    assert run["partitions"] == 26
    assert client.get("/api/analytics").json()["last_run"]["finished_at"] == run["finished_at"]


def test_corrections_and_voids_change_the_counts(registered):
    today = date.today().isoformat()
    first = make_record(disease_code="DEN", onset_date=today, consult_date=today)
    second = make_record(disease_code="DEN", onset_date=today, consult_date=today)

    def send(records):
        batch = make_batch(NODE, records=records)
        registered.post("/api/batches", json=batch)
        wait_for_stored(registered, batch["batch_id"])
        registered.post("/api/analytics/run")
        summary = registered.get("/api/stats/summary").json()
        return {d["disease_code"]: d["this_week"] for d in summary["diseases"]}

    assert send([first, second])["DEN"] == 2
    counts = send([{**first, "disease_code": "ARI", "revision": 2}])  # wrong diagnosis corrected
    assert (counts["DEN"], counts["ARI"]) == (1, 1)
    assert send([{**second, "revision": 2, "voided": True}])["DEN"] == 0  # entered by mistake


def test_scheduler_notices_a_correction(registered):
    db_path = registered.app.state.db_path
    record = make_record()
    batch = make_batch(NODE, records=[record])
    registered.post("/api/batches", json=batch)
    wait_for_stored(registered, batch["batch_id"])
    before = _cases_signature(db_path)

    correction = make_batch(NODE, records=[{**record, "disease_code": "ARI", "revision": 2}])
    registered.post("/api/batches", json=correction)
    wait_for_stored(registered, correction["batch_id"])

    assert _cases_signature(db_path) != before


def test_analytics_query_only_reads_the_index(tmp_path):
    db_path = tmp_path / "plan.db"
    db.init_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        plan = conn.execute(f"EXPLAIN QUERY PLAN {ANALYTICS_QUERY}", ("BRGY-01",)).fetchall()
    assert "COVERING INDEX idx_cases_weekly" in plan[0][3]


def test_a_case_counts_in_the_week_of_its_checkup(tmp_path):
    """Illness started in one week, checked up in the next: it counts in the checkup's week."""
    db_path = tmp_path / "weeks.db"
    db.init_db(db_path)
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    with closing(db.connect(db_path)) as conn, conn:
        conn.execute("INSERT INTO nodes (node_id, barangay_id, registered_at) VALUES ('BRGY-01-MAIN', 'BRGY-01', ?)", (db.utc_now(),))
    pipeline = IngestPipeline(db_path)
    pipeline.start()
    record = make_record(disease_code="DEN", onset_date=(monday - timedelta(days=1)).isoformat(), consult_date=today.isoformat())
    pipeline.submit(SubmitBatch(**make_batch("BRGY-01-MAIN", records=[record])))
    pipeline.stop()

    Analytics(db_path, workers=1).run(today)

    this_year, this_week, _ = today.isocalendar()
    last_year, last_week, _ = (monday - timedelta(days=1)).isocalendar()
    count = lambda year, week: query_count(db_path, year, week)
    assert count(this_year, this_week) == 1
    assert count(last_year, last_week) == 0


def query_count(db_path, year, week):
    with closing(db.connect(db_path)) as conn:
        row = conn.execute(
            "SELECT case_count FROM weekly_stats WHERE barangay_id = 'BRGY-01' AND disease_code = 'DEN' AND iso_year = ? AND iso_week = ?",
            (year, week),
        ).fetchone()
    return row[0] if row else 0


def test_upgrading_replaces_the_index_on_the_old_date(tmp_path):
    db_path = tmp_path / "old.db"
    db.init_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.execute("DROP INDEX idx_cases_weekly")
        conn.execute("CREATE INDEX idx_cases_analytics ON cases(barangay_id, voided, disease_code, onset_date, batch_id)")

    db.init_db(db_path)

    with closing(sqlite3.connect(db_path)) as conn:
        names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert "idx_cases_weekly" in names
    assert "idx_cases_analytics" not in names


# ---- analytics run when reports arrive ---------------------------------------------------

@pytest.fixture
def live_server(tmp_path):
    """As it really runs, except that the safety-net interval can't be what starts a run."""
    live = create_app(tmp_path / "live.db", analytics_workers=1, analytics_interval=3600)
    live.dependency_overrides[auth.optional_session] = lambda: AllAccess("test", "TEST")
    with TestClient(live) as client:
        client.post("/api/nodes/register", json={"type": "REGISTER", "node_id": NODE, "barangay_id": "BRGY-08"})
        yield client


def weekly_total(client):
    return client.get("/api/stats/summary").json()["totals"]["this_week"]


def wait_until(condition, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return False


def test_a_report_starts_the_analytics_without_waiting_for_the_interval(live_server):
    today = date.today().isoformat()
    batch = make_batch(NODE, records=[make_record(disease_code="DEN", onset_date=today, consult_date=today)])
    live_server.post("/api/batches", json=batch)
    assert wait_until(lambda: weekly_total(live_server) == 1)


def test_test_data_does_not_start_the_analytics(live_server):
    assert wait_until(lambda: live_server.get("/api/analytics").json()["last_run"] is not None)
    before = live_server.get("/api/analytics").json()["last_run"]["finished_at"]

    batch = {**make_batch(NODE, count=5), "drill": True}
    live_server.post("/api/batches", json=batch)
    wait_for_stored(live_server, batch["batch_id"])
    time.sleep(config.ANALYTICS_GATHER_SECONDS + 1)

    assert live_server.get("/api/analytics").json()["last_run"]["finished_at"] == before


def test_reports_arriving_together_share_one_run(live_server):
    assert wait_until(lambda: live_server.get("/api/analytics").json()["last_run"] is not None)
    before = live_server.get("/api/analytics").json()["last_run"]["finished_at"]
    today = date.today().isoformat()
    for _ in range(5):
        live_server.post("/api/batches", json=make_batch(NODE, records=[make_record(disease_code="DEN", onset_date=today, consult_date=today)]))

    assert wait_until(lambda: weekly_total(live_server) == 5)
    # One run gathered them all, or at most a second caught the last ones.
    runs = []
    original = live_server.app.state.analytics.run
    live_server.app.state.analytics.run = lambda *a, **k: runs.append(1) or original(*a, **k)
    time.sleep(config.ANALYTICS_GATHER_SECONDS + 0.5)
    assert len(runs) <= 1
    assert live_server.get("/api/analytics").json()["last_run"]["finished_at"] != before


# ---- reduce writes only what changed -----------------------------------------------------

ROW = ("BRGY-01", "DEN", 2026, 45, 1, 2.5, 3.5)


@pytest.fixture
def stats_conn(tmp_path):
    db.init_db(tmp_path / "stats.db")
    with closing(db.connect(tmp_path / "stats.db")) as conn:
        yield conn


def write(conn, *rows):
    return _write_changed_rows(conn, set(rows), _stored_rows(conn))


def test_unchanged_rows_are_not_written_again(stats_conn):
    assert write(stats_conn, ROW) == 1
    assert write(stats_conn, ROW) == 0
    assert _stored_rows(stats_conn) == {ROW}


def test_a_changed_count_replaces_its_row(stats_conn):
    write(stats_conn, ROW)
    other_week = (*ROW[:3], 44, 7, None, None)
    higher = (*ROW[:4], 2, 2.5, 3.5)
    assert write(stats_conn, other_week, higher) == 2
    assert _stored_rows(stats_conn) == {other_week, higher}
    assert write(stats_conn, other_week, higher) == 0  # NULL thresholds compare as equal


def test_rows_that_no_longer_exist_are_removed(stats_conn):
    other_week = (*ROW[:3], 44, 7, None, None)
    write(stats_conn, other_week, ROW)
    assert write(stats_conn, ROW) == 1
    assert _stored_rows(stats_conn) == {ROW}


def test_a_new_server_picks_up_from_what_is_stored(tmp_path):
    """The memory of the last run is empty after a restart, so it compares with the table instead."""
    db_path = tmp_path / "restart.db"
    import_history(db_path, years=1)
    Analytics(db_path, workers=1).run()
    with closing(db.connect(db_path)) as conn:
        before = _stored_rows(conn)
        conn.execute("UPDATE weekly_stats SET case_count = case_count + 100 WHERE barangay_id = 'BRGY-01' AND disease_code = 'DEN'")
        conn.commit()

    Analytics(db_path, workers=1).run()

    with closing(db.connect(db_path)) as conn:
        assert _stored_rows(conn) == before


def test_writing_only_changes_gives_the_same_table_as_rewriting_everything(tmp_path):
    db_path = tmp_path / "same.db"
    import_history(db_path, years=1)
    analytics = Analytics(db_path, workers=1)
    analytics.run()
    with closing(db.connect(db_path)) as conn, conn:
        conn.execute("DELETE FROM cases WHERE rowid IN (SELECT rowid FROM cases LIMIT 50)")
    analytics.run()
    with closing(db.connect(db_path)) as conn:
        incremental = _stored_rows(conn)

    Analytics(db_path, workers=1, rewrite_all=True).run()

    with closing(db.connect(db_path)) as conn:
        assert _stored_rows(conn) == incremental


def test_a_voided_case_leaves_the_weekly_numbers(tmp_path):
    db_path = tmp_path / "void.db"
    db.init_db(db_path)
    with closing(db.connect(db_path)) as conn, conn:
        conn.execute("INSERT INTO nodes (node_id, barangay_id, registered_at) VALUES ('BRGY-01-MAIN', 'BRGY-01', ?)", (db.utc_now(),))
    today = date.today()
    record = make_record(disease_code="DEN", onset_date=today.isoformat(), consult_date=today.isoformat())
    year, week, _ = today.isocalendar()
    analytics = Analytics(db_path, workers=1)

    pipeline = IngestPipeline(db_path)
    pipeline.start()
    pipeline.submit(SubmitBatch(**make_batch("BRGY-01-MAIN", records=[record])))
    pipeline.stop()
    analytics.run(today)
    assert query_count(db_path, year, week) == 1

    pipeline = IngestPipeline(db_path)
    pipeline.start()
    pipeline.submit(SubmitBatch(**make_batch("BRGY-01-MAIN", records=[{**record, "revision": 1, "voided": True}])))
    pipeline.stop()
    analytics.run(today)
    assert query_count(db_path, year, week) == 0


def test_the_calendar_queries_use_the_analytics_index(tmp_path):
    """Reading the whole cases table for one day took seconds on a large database."""
    from rhu import stats
    db_path = tmp_path / "plan.db"
    db.init_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        where, params = stats._real_cases(["DEN", "ARI"])
        plan = conn.execute(f"EXPLAIN QUERY PLAN SELECT barangay_id, COUNT(*) FROM cases WHERE {where} AND consult_date = ? GROUP BY 1",
                            (*params, "2026-11-17")).fetchall()
    assert any("idx_cases_weekly" in row[3] for row in plan)


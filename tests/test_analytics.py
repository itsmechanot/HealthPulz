"""Tests for the parallel analytics (weekly counts and thresholds)."""

import sqlite3
from collections import Counter
from contextlib import closing
from datetime import date, timedelta

import pytest

from rhu import db
from rhu.analytics import Analytics, baseline_values
from rhu.history import import_history

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
    assert result.rows == 26 * 7 * weeks  # 6 tracked diseases + Other consultations
    assert result.cases == imported.cases

    rows = weekly_stats(db_path)
    assert sum(r[4] for r in rows) == imported.cases
    assert all(r[5] is None for r in rows if r[2] == 2024)      # only 1 past year
    assert all((r[5] is not None) == (r[1] != "OTH") for r in rows if r[2] == 2026)  # 3 past years; Other has none


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

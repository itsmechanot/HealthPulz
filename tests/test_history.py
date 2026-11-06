"""Tests for the synthetic generator and the history import."""

import random
import sqlite3
from contextlib import closing
from datetime import date, timedelta

import pytest

from rhu import db, rules
from rhu.history import import_history
from rhu.synthetic import Outbreak, generate_cases, iso_week_mondays, make_case, poisson

TODAY = date(2026, 10, 30)  # a Friday


def query(db_path, sql):
    with closing(sqlite3.connect(db_path)) as conn:
        return conn.execute(sql).fetchall()


def test_iso_week_mondays_stop_before_the_current_week():
    weeks = iso_week_mondays(date(2026, 10, 1), TODAY)
    assert weeks[0] == date(2026, 9, 28)
    assert weeks[-1] == date(2026, 10, 19)  # the current week (starting Oct 26) is left for live data
    assert all(w.weekday() == 0 for w in weeks)


@pytest.mark.parametrize("rate", [0.5, 4, 80])
def test_poisson_mean_matches_rate(rate):
    rng = random.Random(1)
    mean = sum(poisson(rng, rate) for _ in range(5000)) / 5000
    assert mean == pytest.approx(rate, rel=0.05)


def test_cases_follow_the_rules():
    rng = random.Random(1)
    for disease in rules.diseases():
        for _ in range(200):
            case = make_case(rng, disease, date(2026, 10, 19), latest=TODAY)
            onset, consult = date.fromisoformat(case["onset_date"]), date.fromisoformat(case["consult_date"])
            assert date(2026, 10, 19) <= onset <= consult <= TODAY
            assert any(b["min"] <= case["age_years"] <= b["max"] for b in disease["age_profile"])
            assert case["sex"] in ("M", "F") and 1 <= case["purok"] <= 7


def test_consult_date_is_never_after_latest():
    rng = random.Random(1)
    disease = rules.diseases()[0]
    sunday = TODAY - timedelta(days=1)
    for _ in range(200):
        case = make_case(rng, disease, TODAY - timedelta(days=TODAY.weekday()), latest=sunday)
        assert date.fromisoformat(case["consult_date"]) <= sunday


def test_outbreak_raises_cases_in_that_barangay_and_its_neighbors():
    barangays = [
        {"barangay_id": "BRGY-01", "population": 10_000, "neighbors": ["BRGY-02"]},
        {"barangay_id": "BRGY-02", "population": 10_000, "neighbors": ["BRGY-01"]},
        {"barangay_id": "BRGY-03", "population": 10_000, "neighbors": []},
    ]
    dengue = next(d for d in rules.diseases() if d["disease_code"] == "DEN")
    weeks = iso_week_mondays(date(2026, 1, 5), date(2026, 3, 2))  # 8 weeks, no dengue season
    outbreak = Outbreak("BRGY-01", "DEN", weeks[0], weeks=8, multiplier=5)

    counts = {"BRGY-01": 0, "BRGY-02": 0, "BRGY-03": 0}
    for barangay_id, _ in generate_cases(random.Random(1), barangays, [dengue], weeks, [outbreak], latest=TODAY):
        counts[barangay_id] += 1

    assert counts["BRGY-01"] > 3 * counts["BRGY-03"]      # outbreak barangay: about x5
    assert counts["BRGY-03"] < counts["BRGY-02"] < counts["BRGY-01"]  # neighbor: x1.5 spillover


def test_import_history(tmp_path):
    db_path = tmp_path / "test.db"
    result = import_history(db_path, years=1, today=TODAY)

    assert result.cases > 1000
    assert query(db_path, "SELECT COUNT(*) FROM cases")[0][0] == result.cases
    assert query(db_path, "SELECT COUNT(*) FROM nodes WHERE kind = 'HISTORY'")[0][0] == 26
    assert query(db_path, "SELECT SUM(accepted) FROM batches")[0][0] == result.cases
    assert query(db_path, "SELECT MIN(onset_date) FROM cases")[0][0] >= "2024-12-30"  # ISO week 1 of 2025
    assert query(db_path, "SELECT MAX(onset_date) FROM cases")[0][0] < "2026-10-26"   # nothing in the live week


def test_same_seed_gives_same_history(tmp_path):
    first = import_history(tmp_path / "a.db", years=1, seed=7, today=TODAY)
    second = import_history(tmp_path / "b.db", years=1, seed=7, today=TODAY)
    assert first.cases == second.cases
    assert first.outbreaks == second.outbreaks


def test_reimport_replaces_history_and_keeps_live_data(tmp_path):
    db_path = tmp_path / "test.db"
    import_history(db_path, years=1, seed=1, today=TODAY)

    with closing(db.connect(db_path)) as conn, conn:
        conn.execute("INSERT INTO nodes (node_id, barangay_id, registered_at) VALUES ('BRGY-08-LIVE', 'BRGY-08', 'now')")
        conn.execute(
            "INSERT INTO batches (batch_id, node_id, record_count, sent_at, received_at, status) "
            "VALUES ('b1', 'BRGY-08-LIVE', 1, 'now', 'now', 'STORED')"
        )
        conn.execute(
            "INSERT INTO cases VALUES ('live-1', 'b1', 'BRGY-08-LIVE', 'BRGY-08', 'DEN', '2026-10-27', "
            "'2026-10-28', 10, 'F', 1, 'SUSPECTED', 'now', 0, 0)"
        )

    result = import_history(db_path, years=1, seed=2, today=TODAY)

    assert query(db_path, "SELECT COUNT(*) FROM cases")[0][0] == result.cases + 1
    assert query(db_path, "SELECT COUNT(*) FROM cases WHERE record_id = 'live-1'")[0][0] == 1
    assert query(db_path, "SELECT COUNT(*) FROM nodes WHERE kind = 'HISTORY'")[0][0] == 26


def test_old_database_gets_the_kind_column(tmp_path):
    db_path = tmp_path / "old.db"
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            "CREATE TABLE nodes (node_id TEXT PRIMARY KEY, barangay_id TEXT NOT NULL, "
            "registered_at TEXT NOT NULL, last_seen_at TEXT, status TEXT NOT NULL DEFAULT 'OFFLINE')"
        )
        conn.execute("INSERT INTO nodes (node_id, barangay_id, registered_at) VALUES ('BRGY-08-K7QX', 'BRGY-08', 'now')")
        conn.commit()

    db.init_db(db_path)

    assert query(db_path, "SELECT kind FROM nodes") == [("LIVE",)]


def test_no_imported_checkup_falls_in_the_current_week(tmp_path):
    """A case counts in the week of its checkup, so this week must show only what the BHUs send."""
    db_path = tmp_path / "history.db"
    import_history(db_path, years=1, today=date(2026, 11, 6))
    assert query(db_path, "SELECT MAX(consult_date) FROM cases")[0][0] <= "2026-11-01"


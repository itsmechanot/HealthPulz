"""Tests for the read-only statistics endpoints."""

from contextlib import closing
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from rhu import db
from rhu.history import import_history
from rhu.weeks import week_dates
from tests.factories import NODE, make_batch, make_record, wait_for_stored, create_test_app


@pytest.fixture(scope="module")
def analyzed(tmp_path_factory):
    """One server shared by these read-only tests: 3 years of history,
    4 live dengue cases this week, analytics run."""
    db_path = tmp_path_factory.mktemp("stats") / "test.db"
    import_history(db_path, years=3)
    with TestClient(create_test_app(db_path)) as client:
        client.post("/api/nodes/register", json={"type": "REGISTER", "node_id": NODE, "barangay_id": "BRGY-08"})
        today = date.today().isoformat()
        batch = make_batch(NODE, records=[make_record(disease_code="DEN", onset_date=today, consult_date=today) for _ in range(4)])
        client.post("/api/batches", json=batch)
        wait_for_stored(client, batch["batch_id"])
        client.post("/api/analytics/run")
        yield client


def current_week():
    year, week, _ = date.today().isocalendar()
    return year, week


def test_summary_compares_this_week_with_last_week(analyzed):
    body = analyzed.get("/api/stats/summary").json()
    year, week = current_week()
    last_year, last_week = (date.today() - timedelta(weeks=1)).isocalendar()[:2]

    assert (body["week"]["iso_year"], body["week"]["iso_week"]) == (year, week)
    assert body["previous_week"]["label"] == f"{last_year}-W{last_week:02d}"
    assert body["week"]["dates"] == week_dates(year, week)
    assert len(body["diseases"]) == 19  # tracked diseases only
    assert body["other_consultations"]["this_week"] == 0
    dengue = next(d for d in body["diseases"] if d["disease_code"] == "DEN")
    assert dengue["this_week"] == 4  # only the live cases: history stops at last week
    assert body["totals"]["this_week"] == 4
    assert body["totals"]["last_week"] == sum(d["last_week"] for d in body["diseases"]) > 0
    assert body["analyzed_at"] is not None


def test_trend_for_one_barangay_and_disease_has_thresholds(analyzed):
    body = analyzed.get("/api/stats/trend?disease=DEN&barangay_id=BRGY-08&weeks=10").json()
    weeks = body["weeks"]

    assert len(weeks) == 10
    assert [w["start"] for w in weeks] == sorted(w["start"] for w in weeks)  # oldest first
    assert weeks[-1]["cases"] == 4  # current week, live cases
    assert all(w["alert_threshold"] is not None for w in weeks)


def test_municipal_trend_has_no_thresholds(analyzed):
    weeks = analyzed.get("/api/stats/trend?weeks=5").json()["weeks"]
    assert all(w["alert_threshold"] is None for w in weeks)


def test_all_three_views_agree_on_last_week(analyzed):
    last_year, last_week = (date.today() - timedelta(weeks=1)).isocalendar()[:2]
    label = f"{last_year}-W{last_week:02d}"

    summary_total = analyzed.get("/api/stats/summary").json()["totals"]["last_week"]
    grid_total = sum(b["total"] for b in analyzed.get(f"/api/stats/barangays?week={label}").json()["barangays"])
    trend_total = analyzed.get("/api/stats/trend?weeks=2").json()["weeks"][0]["cases"]

    assert summary_total == grid_total == trend_total


def test_barangay_grid_has_every_barangay_with_levels(analyzed):
    body = analyzed.get("/api/stats/barangays").json()
    assert body["week"]["iso_week"] == current_week()[1]
    assert len(body["barangays"]) == 26

    caraycaray = next(b for b in body["barangays"] if b["barangay_id"] == "BRGY-08")
    assert caraycaray["total"] == 4
    assert caraycaray["rate_per_10k"] == round(4 / 6800 * 10_000, 1)
    assert caraycaray["diseases"]["DEN"]["level"] in {"NORMAL", "ALERT", "EPIDEMIC"}


def test_grid_level_matches_raised_alerts(analyzed):
    last_year, last_week = (date.today() - timedelta(weeks=1)).isocalendar()[:2]
    grid = analyzed.get(f"/api/stats/barangays?week={last_year}-W{last_week:02d}").json()["barangays"]
    flagged = {(b["barangay_id"], code) for b in grid for code, c in b["diseases"].items() if c["level"] != "NORMAL"}
    alerted = {(a["barangay_id"], a["disease_code"]) for a in analyzed.get("/api/alerts").json() if a["iso_week"] == last_week}
    assert flagged == alerted


@pytest.mark.parametrize(
    ("url", "status"),
    [
        ("/api/stats/trend?disease=XYZ", 404),
        ("/api/stats/trend?barangay_id=BRGY-99", 404),
        ("/api/stats/trend?weeks=0", 400),
        ("/api/stats/barangays?week=2026-44", 400),
        ("/api/stats/barangays?week=2025-W53", 400),  # 2025 has only 52 ISO weeks
        ("/api/stats/day?date=2026-13-01", 400),
        ("/api/stats/day", 400),
        ("/api/stats/days?month=2026-13", 400),
        ("/api/stats/days?month=2026-1", 400),
    ],
)
def test_bad_parameters(client, url, status):
    assert client.get(url).status_code == status


def test_no_endpoint_exposes_individual_cases(analyzed):
    # Privacy: aggregated counts only. No response may contain a record_id or a case's age.
    today = date.today()
    for url in ("/api/stats/summary", "/api/stats/trend", "/api/stats/barangays",
                f"/api/stats/day?date={today.isoformat()}", f"/api/stats/days?month={today:%Y-%m}"):
        text = analyzed.get(url).text
        assert "record_id" not in text and "age_years" not in text


# ---- the map's calendar: one day ---------------------------------------------------------

def test_one_day_shows_that_days_cases_per_barangay(analyzed):
    body = analyzed.get(f"/api/stats/day?date={date.today().isoformat()}").json()
    caraycaray = next(b for b in body["barangays"] if b["barangay_id"] == "BRGY-08")
    assert body["date"] == date.today().isoformat()
    assert body["dates"] == f"{date.today():%b} {date.today().day}, {date.today().year}"
    assert len(body["barangays"]) == 26
    assert caraycaray["diseases"]["DEN"]["cases"] == 4
    assert body["total"] == 4  # history stops at last week: today has only the live cases


def test_a_past_day_matches_the_cases_checked_up_that_day(analyzed):
    with closing(db.connect(analyzed.app.state.db_path)) as conn:
        day, expected = conn.execute(
            "SELECT consult_date, COUNT(*) FROM cases WHERE disease_code = 'DEN' GROUP BY consult_date ORDER BY 2 DESC LIMIT 1"
        ).fetchone()
    body = analyzed.get(f"/api/stats/day?date={day}").json()
    assert sum(b["diseases"].get("DEN", {}).get("cases", 0) for b in body["barangays"]) == expected


def test_a_day_keeps_its_weeks_outbreak_levels(analyzed):
    """Day colors follow that day's cases; an outbreak that week stays marked as week_level."""
    with closing(db.connect(analyzed.app.state.db_path)) as conn:
        year, week, barangay, code = conn.execute(
            """SELECT a.iso_year, a.iso_week, a.barangay_id, a.disease_code FROM alerts a
               WHERE a.level IN ('ALERT', 'EPIDEMIC') ORDER BY a.iso_year DESC, a.iso_week DESC LIMIT 1"""
        ).fetchone()
    day = date.fromisocalendar(year, week, 1)  # Monday: never in the future
    body = analyzed.get(f"/api/stats/day?date={day.isoformat()}").json()
    cell = next(b for b in body["barangays"] if b["barangay_id"] == barangay)["diseases"][code]
    assert cell["level"] == "NORMAL"
    assert cell["week_level"] in ("ALERT", "EPIDEMIC")


def test_a_future_day_is_refused(analyzed):
    tomorrow = date.today() + timedelta(days=1)
    assert analyzed.get(f"/api/stats/day?date={tomorrow.isoformat()}").status_code == 400


def test_the_month_marks_days_with_cases(analyzed):
    today = date.today()
    days = analyzed.get(f"/api/stats/days?month={today:%Y-%m}").json()["days"]
    assert days[today.isoformat()] == 4
    assert all(count > 0 for count in days.values())
    last_year = analyzed.get(f"/api/stats/days?month={today.year - 1}-{today.month:02d}").json()["days"]
    assert len(last_year) >= 20  # history covers every day of a past month


def test_test_data_and_voided_checkups_stay_off_the_calendar(client):
    client.post("/api/nodes/register", json={"type": "REGISTER", "node_id": NODE, "barangay_id": "BRGY-08"})
    today = date.today().isoformat()
    real = make_record(disease_code="DEN", onset_date=today, consult_date=today)
    drill = {**make_batch(NODE, records=[make_record(disease_code="DEN", onset_date=today, consult_date=today)]), "drill": True}
    for batch in (make_batch(NODE, records=[real]), drill, make_batch(NODE, records=[{**real, "revision": 1, "voided": True}])):
        client.post("/api/batches", json=batch)
        wait_for_stored(client, batch["batch_id"])

    assert client.get(f"/api/stats/day?date={today}").json()["total"] == 0
    assert today not in client.get(f"/api/stats/days?month={today[:7]}").json()["days"]


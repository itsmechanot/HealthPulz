"""Tests for the read-only statistics endpoints."""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from rhu.history import import_history
from rhu.main import create_app
from tests.factories import NODE, make_batch, make_record, wait_for_stored


@pytest.fixture(scope="module")
def analyzed(tmp_path_factory):
    """One server shared by these read-only tests: 3 years of history,
    4 live dengue cases this week, analytics run."""
    db_path = tmp_path_factory.mktemp("stats") / "test.db"
    import_history(db_path, years=3)
    with TestClient(create_app(db_path, analytics_workers=1, analytics_interval=3600)) as client:
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
    assert len(body["diseases"]) == 6  # tracked diseases only
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
    ],
)
def test_bad_parameters(client, url, status):
    assert client.get(url).status_code == status


def test_no_endpoint_exposes_individual_cases(analyzed):
    # Privacy: aggregated counts only. No response may contain a record_id or a case's age.
    for url in ("/api/stats/summary", "/api/stats/trend", "/api/stats/barangays"):
        text = analyzed.get(url).text
        assert "record_id" not in text and "age_years" not in text

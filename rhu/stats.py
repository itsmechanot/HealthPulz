"""Read-only statistics for the RHU dashboard and the BHS nodes.

Everything here reads weekly_stats (the output of the parallel analytics) and
alerts. Only aggregated weekly counts are returned, never individual cases,
which is why barangays may see municipal data (Data Privacy Act).
"""

import sqlite3
from datetime import date

from rhu import rules
from rhu.alerts import classify, weeks_to_check


def week_info(year: int, week: int) -> dict:
    return {
        "iso_year": year,
        "iso_week": week,
        "label": f"{year}-W{week:02d}",
        "start": date.fromisocalendar(year, week, 1).isoformat(),
    }


def summary(conn: sqlite3.Connection, today: date) -> dict:
    """Municipality-wide totals per disease: this week (so far) vs last week."""
    (last_year, last_week), (this_year, this_week) = weeks_to_check(today)
    counts = {
        (r["disease_code"], r["iso_year"], r["iso_week"]): r["cases"]
        for r in conn.execute(
            """
            SELECT disease_code, iso_year, iso_week, SUM(case_count) AS cases FROM weekly_stats
            WHERE (iso_year = ? AND iso_week = ?) OR (iso_year = ? AND iso_week = ?)
            GROUP BY disease_code, iso_year, iso_week
            """,
            (last_year, last_week, this_year, this_week),
        )
    }
    on_alert = {
        r["disease_code"]: r["barangays"]
        for r in conn.execute(
            """
            SELECT disease_code, COUNT(DISTINCT barangay_id) AS barangays FROM alerts
            WHERE status != 'RESOLVED' AND ((iso_year = ? AND iso_week = ?) OR (iso_year = ? AND iso_week = ?))
            GROUP BY disease_code
            """,
            (last_year, last_week, this_year, this_week),
        )
    }

    other_now = other_before = 0
    for d in rules.diseases():
        if not d["tracked"]:
            other_now += counts.get((d["disease_code"], this_year, this_week), 0)
            other_before += counts.get((d["disease_code"], last_year, last_week), 0)

    diseases = []
    for d in rules.tracked_diseases():
        now = counts.get((d["disease_code"], this_year, this_week), 0)
        before = counts.get((d["disease_code"], last_year, last_week), 0)
        diseases.append({
            "disease_code": d["disease_code"],
            "name": d["name"],
            "group": d["group"],
            "this_week": now,
            "last_week": before,
            "change_pct": round((now - before) / before * 100, 1) if before else None,
            "barangays_on_alert": on_alert.get(d["disease_code"], 0),
        })

    open_alerts = conn.execute(
        """
        SELECT COUNT(*) FROM alerts
        WHERE status != 'RESOLVED' AND ((iso_year = ? AND iso_week = ?) OR (iso_year = ? AND iso_week = ?))
        """,
        (last_year, last_week, this_year, this_week),
    ).fetchone()[0]

    return {
        "week": week_info(this_year, this_week),
        "previous_week": week_info(last_year, last_week),
        "diseases": diseases,
        "totals": {"this_week": sum(d["this_week"] for d in diseases), "last_week": sum(d["last_week"] for d in diseases)},
        "other_consultations": {"this_week": other_now, "last_week": other_before},
        "open_alerts": open_alerts,
    }


def trend(conn: sqlite3.Connection, weeks: int, disease_code: str | None = None, barangay_id: str | None = None) -> list[dict]:
    """Weekly case counts, oldest first, up to the current week. Without a disease it
    sums the surveillance diseases ("Other consultations" must be asked for by code).

    Thresholds are included only for a single barangay and disease; summing
    thresholds across barangays or diseases would not be statistically valid.
    """
    single_cell = disease_code is not None and barangay_id is not None
    codes = [disease_code] if disease_code else [d["disease_code"] for d in rules.tracked_diseases()]
    rows = conn.execute(
        f"""
        SELECT iso_year, iso_week, SUM(case_count) AS cases,
               MAX(alert_threshold) AS alert_threshold, MAX(epidemic_threshold) AS epidemic_threshold
        FROM weekly_stats
        WHERE disease_code IN ({",".join("?" * len(codes))}) AND (? IS NULL OR barangay_id = ?)
        GROUP BY iso_year, iso_week
        ORDER BY iso_year DESC, iso_week DESC
        LIMIT ?
        """,
        (*codes, barangay_id, barangay_id, weeks),
    ).fetchall()
    return [
        {
            **week_info(r["iso_year"], r["iso_week"]),
            "cases": r["cases"],
            "alert_threshold": r["alert_threshold"] if single_cell else None,
            "epidemic_threshold": r["epidemic_threshold"] if single_cell else None,
        }
        for r in reversed(rows)
    ]


def barangay_grid(conn: sqlite3.Connection, year: int, week: int) -> dict:
    """Every barangay × disease for one week, with the same levels the alerts use."""
    diseases = {d["disease_code"]: d for d in rules.diseases()}
    cells: dict[str, dict] = {}
    other: dict[str, int] = {}
    for r in conn.execute(
        "SELECT * FROM weekly_stats WHERE iso_year = ? AND iso_week = ?", (year, week)
    ):
        if not diseases[r["disease_code"]]["tracked"]:
            other[r["barangay_id"]] = other.get(r["barangay_id"], 0) + r["case_count"]
            continue
        outcome = classify(
            r["case_count"], r["alert_threshold"], r["epidemic_threshold"], diseases[r["disease_code"]]["single_case_alert"]
        )
        cells.setdefault(r["barangay_id"], {})[r["disease_code"]] = {
            "cases": r["case_count"],
            "alert_threshold": r["alert_threshold"],
            "epidemic_threshold": r["epidemic_threshold"],
            "level": outcome[0] if outcome else "NORMAL",
        }

    barangays = []
    for b in rules.barangays():
        by_disease = cells.get(b["barangay_id"], {})
        total = sum(c["cases"] for c in by_disease.values())
        barangays.append({
            "barangay_id": b["barangay_id"],
            "name": b["name"],
            "population": b["population"],
            "total": total,
            "rate_per_10k": round(total / b["population"] * 10_000, 1),
            "other_consultations": other.get(b["barangay_id"], 0),
            "diseases": by_disease,
        })
    return {"week": week_info(year, week), "barangays": barangays}

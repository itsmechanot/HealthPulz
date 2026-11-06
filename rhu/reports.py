"""The newest de-identified reports the RHU received, for the dashboard's Latest reports card.

Kept in memory because the dashboard asks every second, and searching the cases table that often
would compete with the ingestion pipeline. The DB writer thread adds to the list while the
WebSocket feed reads it, so a lock protects it. The weekly numbers still come from the analytics.
"""

import sqlite3
import threading
from collections import deque
from collections.abc import Iterable
from contextlib import closing
from datetime import date
from pathlib import Path

from rhu import db
from rhu.weeks import week_dates

MAX_REPORTS = 20


def _report(
    record_id: str, barangay_id: str, disease_code: str, onset_date: str, consult_date: str, age_years: int,
    sex: str, purok: int, case_status: str, received_at: str, revision: int, voided: bool,
) -> dict:
    year, week, _ = date.fromisoformat(consult_date).isocalendar()
    return {
        "record_id": record_id, "barangay_id": barangay_id, "disease_code": disease_code,
        "onset_date": onset_date, "consult_date": consult_date, "age_years": age_years, "sex": sex, "purok": purok,
        "case_status": case_status, "received_at": received_at, "revision": revision, "voided": bool(voided),
        "counted_in_week": f"{year}-W{week:02d}",  # cases count in the week of the checkup
        "counted_week_dates": week_dates(year, week),
    }


class LatestReports:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._reports: deque[dict] = deque(maxlen=MAX_REPORTS)  # newest first

    def load(self, db_path: Path) -> None:
        """Called once at startup, so a restart doesn't empty the card. Test data and imported
        history are left out, as they are when reports arrive."""
        with closing(db.connect(db_path)) as conn:
            rows = _newest_rows(conn)
        with self._lock:
            self._reports.clear()
            self._reports.extend(_report(*row) for row in rows)

    def add(self, reports: Iterable[dict]) -> None:
        with self._lock:
            for report in reports:
                # A correction or void replaces the earlier entry for that case.
                earlier = [r for r in self._reports if r["record_id"] == report["record_id"]]
                for entry in earlier:
                    self._reports.remove(entry)
                self._reports.appendleft(report)

    def snapshot(self) -> list[dict]:
        with self._lock:
            return list(self._reports)


def _newest_rows(conn: sqlite3.Connection) -> list[tuple]:
    rows = conn.execute(
        """
        SELECT c.record_id, c.barangay_id, c.disease_code, c.onset_date, c.consult_date, c.age_years,
               c.sex, c.purok, c.case_status, c.received_at, c.revision, c.voided
        FROM cases c
        JOIN batches b USING (batch_id)
        JOIN nodes n ON n.node_id = c.node_id
        WHERE b.drill = 0 AND n.kind = 'LIVE'
        ORDER BY c.received_at DESC
        LIMIT ?
        """,
        (MAX_REPORTS,),
    )
    return [tuple(row) for row in rows]


def from_records(records: Iterable, barangay_id: str, received_at: str) -> list[dict]:
    """The reports of a batch that was just stored."""
    return [
        _report(
            str(r.record_id), barangay_id, r.disease_code, r.onset_date.isoformat(), r.consult_date.isoformat(),
            r.age_years, r.sex, r.purok, r.case_status, received_at, r.revision, r.voided,
        )
        for r in records
    ]

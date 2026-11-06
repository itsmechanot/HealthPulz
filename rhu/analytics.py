"""Parallel analytics: weekly case counts and outbreak thresholds, MapReduce style.

    map     each worker *process* takes one barangay, reads its cases over its own
            read-only connection, and computes, per disease and ISO week, the case
            count and the alert/epidemic thresholds
    reduce  the main process merges the workers' rows and writes weekly_stats in
            one transaction (still a single writer)

Thresholds compare a week with the same ISO week ±1 in up to 5 previous years
(the idea behind CDC's "historical limits" method):
    alert    = mean + 2·SD
    epidemic = mean + 3·SD
A threshold needs at least 2 previous years of data; otherwise it is NULL.
Voided cases and cases from drill (stress-test) batches are left out.

Separate processes (not threads) are used because the map step is CPU-bound
Python, and Python's GIL lets only one thread run Python code at a time.
"""

import asyncio
import logging
import sqlite3
import statistics
import threading
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from concurrent.futures import ProcessPoolExecutor
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path

from rhu import config, db, rules
from rhu.synthetic import iso_week_mondays

log = logging.getLogger(__name__)

BASELINE_YEARS = 5
BASELINE_WINDOW_WEEKS = 1  # weeks on each side of the same ISO week
MIN_BASELINE_YEARS = 2

ANALYTICS_QUERY = """
    SELECT disease_code, consult_date FROM cases
    WHERE barangay_id = ? AND voided = 0 AND batch_id NOT IN (SELECT batch_id FROM batches WHERE drill = 1)
"""


@dataclass
class AnalyticsResult:
    workers: int
    partitions: int
    cases: int
    rows: int
    map_seconds: float
    reduce_seconds: float
    finished_at: str

    @property
    def total_seconds(self) -> float:
        return self.map_seconds + self.reduce_seconds

    def to_dict(self) -> dict:
        return {**asdict(self), "total_seconds": round(self.total_seconds, 3)}


class Analytics:
    def __init__(self, db_path: Path, workers: int = config.ANALYTICS_WORKERS, *, rewrite_all: bool = False) -> None:
        """rewrite_all: write every row on every run. The benchmark uses it to measure the full job."""
        self.db_path = db_path
        self.workers = workers
        self._rewrite_all = rewrite_all
        self._written: set[tuple] | None = None  # what the last run left in weekly_stats
        # Created once and reused: starting processes is slow (especially on Windows).
        self._executor = ProcessPoolExecutor(max_workers=workers) if workers > 1 else None
        self._lock = threading.Lock()  # the scheduler and a manual run must not overlap
        self.last_result: AnalyticsResult | None = None

    def close(self) -> None:
        if self._executor is not None:
            self._executor.shutdown(cancel_futures=True)

    def run(self, today: date | None = None) -> AnalyticsResult:
        with self._lock:
            result = self._run(today or date.today())
        self.last_result = result
        return result

    def _run(self, today: date) -> AnalyticsResult:
        diseases = [d["disease_code"] for d in rules.diseases()]
        thresholded = {d["disease_code"] for d in rules.diseases() if d["threshold_alerts"]}
        barangay_ids = [b["barangay_id"] for b in rules.barangays()]
        first_case = self._first_checkup()
        # Every week from the first case up to and including the current week.
        weeks = iso_week_mondays(first_case, today + timedelta(weeks=1)) if first_case else []

        start = time.perf_counter()
        args = [(str(self.db_path), barangay_id, diseases, thresholded, weeks) for barangay_id in barangay_ids]
        if self._executor is None:
            parts = [_map_barangay(*a) for a in args]
        else:
            # chunksize=1: a worker that finishes a small barangay immediately takes
            # the next one, which balances the uneven barangay sizes (dynamic scheduling).
            parts = list(self._executor.map(_map_barangay, *zip(*args), chunksize=1))
        mapped = time.perf_counter()

        rows = [row for _, part_rows in parts for row in part_rows]
        new_rows = set(rows)
        with closing(db.connect(self.db_path)) as conn, conn:
            if self._rewrite_all:
                conn.execute("DELETE FROM weekly_stats")
                _write_changed_rows(conn, new_rows, set())
            else:
                stored = self._written if self._written is not None else _stored_rows(conn)
                _write_changed_rows(conn, new_rows, stored)
        self._written = new_rows  # only after the commit: a failed write is compared again next time
        reduced = time.perf_counter()

        return AnalyticsResult(
            workers=self.workers,
            partitions=len(args),
            cases=sum(count for count, _ in parts),
            rows=len(rows),
            map_seconds=round(mapped - start, 3),
            reduce_seconds=round(reduced - mapped, 3),
            finished_at=db.utc_now(),
        )

    def _first_checkup(self) -> date | None:
        with closing(db.connect(self.db_path)) as conn:
            first = conn.execute("SELECT MIN(consult_date) FROM cases").fetchone()[0]
        return date.fromisoformat(first) if first else None


def _stored_rows(conn: sqlite3.Connection) -> set[tuple]:
    rows = conn.execute(
        """SELECT barangay_id, disease_code, iso_year, iso_week, case_count, alert_threshold, epidemic_threshold
           FROM weekly_stats"""
    )
    return {tuple(row) for row in rows}


def _write_changed_rows(conn: sqlite3.Connection, rows: set[tuple], stored: set[tuple]) -> int:
    """Reduce: a new report changes only a few of the ~100,000 rows, so writing just those keeps the
    numbers close to real time and holds the write lock (shared with the DB writer) only briefly.
    Comparing sets is what makes this fast; comparing row by row took as long as writing them all.
    Returns how many rows were written or removed."""
    changed = rows - stored
    gone = {row[:4] for row in stored - rows} - {row[:4] for row in changed}
    conn.executemany(
        "DELETE FROM weekly_stats WHERE barangay_id = ? AND disease_code = ? AND iso_year = ? AND iso_week = ?", gone
    )
    conn.executemany(
        """
        INSERT OR REPLACE INTO weekly_stats (
          barangay_id, disease_code, iso_year, iso_week, case_count, alert_threshold, epidemic_threshold
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        changed,
    )
    return len(changed) + len(gone)


# ---- map step (runs inside the worker processes) ------------------------------
# Top-level functions, so they can be sent to other processes.

def _map_barangay(
    db_path: str, barangay_id: str, diseases: list[str], thresholded: set[str], weeks: list[date]
) -> tuple[int, list[tuple]]:
    """Returns (cases read, weekly_stats rows) for one barangay."""
    counts: Counter = Counter()
    cases = 0
    uri = Path(db_path).as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as conn:
        # A case counts in the week of its checkup, so what a BHU sends today shows in this week's numbers.
        for disease_code, consult in conn.execute(ANALYTICS_QUERY, (barangay_id,)):
            day = date.fromisoformat(consult)
            counts[disease_code, day - timedelta(days=day.weekday())] += 1
            cases += 1

    rows = []
    for disease in diseases:
        for monday in weeks:
            alert = epidemic = None
            # Only contagious diseases get thresholds. Non-communicable ones and "Other" are just
            # counted, and for the urgent diseases a single case is already enough.
            baseline = baseline_values(counts, disease, monday, first_week=weeks[0]) if disease in thresholded else None
            if baseline is not None:
                mean, sd = statistics.fmean(baseline), statistics.stdev(baseline)
                alert, epidemic = round(mean + 2 * sd, 2), round(mean + 3 * sd, 2)
            year, week, _ = monday.isocalendar()
            rows.append((barangay_id, disease, year, week, counts[disease, monday], alert, epidemic))
    return cases, rows


def baseline_values(counts: Counter, disease: str, monday: date, first_week: date) -> list[int] | None:
    """Counts for the same ISO week ±BASELINE_WINDOW_WEEKS in previous years,
    or None if fewer than MIN_BASELINE_YEARS years of history exist."""
    year, week, _ = monday.isocalendar()
    values: list[int] = []
    years_used = 0
    for back in range(1, BASELINE_YEARS + 1):
        past_year = year - back
        # Week 53 exists only in some years; fall back to that year's last week.
        centre = date.fromisocalendar(past_year, min(week, _weeks_in_year(past_year)), 1)
        window = [centre + timedelta(weeks=offset) for offset in range(-BASELINE_WINDOW_WEEKS, BASELINE_WINDOW_WEEKS + 1)]
        if window[0] < first_week:
            break  # no data that far back
        values.extend(counts[disease, w] for w in window)
        years_used += 1
    return values if years_used >= MIN_BASELINE_YEARS else None


def _weeks_in_year(year: int) -> int:
    return date(year, 12, 28).isocalendar().week  # Dec 28 is always in the last ISO week


# ---- scheduler (runs in the server) ---------------------------------------------

async def run_when_cases_change(
    analytics: Analytics,
    interval: float,
    after_run: Callable[[AnalyticsResult], Awaitable[object]] | None = None,
    new_reports: asyncio.Event | None = None,
) -> None:
    """Background task: re-run the analytics whenever cases were added or replaced, then call
    `after_run` (used to check for outbreak alerts). `new_reports` starts a run right away, so
    the dashboard's numbers follow what the BHUs send; `interval` is the safety net for changes
    nobody announced, such as a history import."""
    new_reports = new_reports or asyncio.Event()
    last_signature = None
    while True:
        new_reports.clear()  # before reading the data: a report after this point starts the next run
        try:
            signature = await asyncio.to_thread(_cases_signature, analytics.db_path)
            if signature != last_signature:
                result = await asyncio.to_thread(analytics.run)
                last_signature = signature
                log.info("Analytics: %d cases in %.2fs with %d worker(s)", result.cases, result.total_seconds, result.workers)
                if after_run is not None:
                    await after_run(result)
        except Exception:
            log.exception("Analytics run failed; will retry")
        try:
            await asyncio.wait_for(new_reports.wait(), timeout=interval)
            await asyncio.sleep(config.ANALYTICS_GATHER_SECONDS)
        except TimeoutError:
            pass


def _cases_signature(db_path: Path) -> tuple:
    """Cheap fingerprint of the case data: changes when cases are added, removed or re-imported,
    and when a batch corrects or voids existing ones (which leaves the case count unchanged)."""
    with closing(db.connect(db_path)) as conn:
        return tuple(conn.execute("SELECT (SELECT COUNT(*) FROM cases), (SELECT MAX(rowid) FROM cases), (SELECT COUNT(*) FROM batches)").fetchone())

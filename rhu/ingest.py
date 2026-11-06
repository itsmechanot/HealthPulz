"""Ingestion pipeline: how case batches from the BHS nodes reach the database.

    HTTP receiver ──► ingest queue ──► validator threads (N) ──► write queue ──► DB writer thread
                       (bounded)                                                 (group commit)

The threads share no state except the queues: every hand-off is a message
placed on a queue (producer–consumer). Only the single DB writer thread
writes cases, so SQLite never has competing writers.
"""

import logging
import queue
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass, field, fields
from datetime import date, timedelta
from pathlib import Path

from rhu import config, db, reports, rules
from rhu.models import CaseRecord, SubmitBatch, barangay_of

log = logging.getLogger(__name__)

# Tells a thread to finish. Queued before it, all real work is still processed (graceful drain).
_STOP = object()

StoredCallback = Callable[[str, dict], None]

_CASE_COLUMNS = """(
  record_id, batch_id, node_id, barangay_id, disease_code, onset_date, consult_date,
  age_years, sex, purok, case_status, received_at, revision, voided
)"""
_CASE_VALUES = "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"


class PipelineBusy(Exception):
    """The ingest queue is full; the node should retry later (backpressure)."""


@dataclass
class _ReceivedBatch:
    batch: SubmitBatch
    received_at: str


@dataclass
class _ValidatedBatch:
    batch: SubmitBatch
    received_at: str
    valid: list[CaseRecord]
    rejected: list[dict]


@dataclass
class PipelineStats:
    """Counters shown on the System Monitor. Updated from several threads, so guarded by a lock."""

    batches_received: int = 0
    batches_busy: int = 0  # refused with 503 because the queue was full
    batches_stored: int = 0
    batches_resent: int = 0  # a batch that was already stored arrived again
    # Once the queues are empty: records_received = accepted + updated + duplicate + rejected (nothing lost).
    records_received: int = 0
    records_accepted: int = 0
    records_updated: int = 0  # corrections and voids of earlier reports
    records_duplicate: int = 0
    records_rejected: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, **counts: int) -> None:
        with self._lock:
            for name, value in counts.items():
                setattr(self, name, getattr(self, name) + value)

    def snapshot(self) -> dict:
        with self._lock:
            return {f.name: getattr(self, f.name) for f in fields(self) if not f.name.startswith("_")}


class IngestPipeline:
    def __init__(
        self,
        db_path: Path,
        *,
        workers: int = config.INGEST_WORKERS,
        queue_size: int = config.INGEST_QUEUE_SIZE,
        on_stored: StoredCallback | None = None,
    ) -> None:
        self._db_path = db_path
        self._worker_count = workers
        self._on_stored = on_stored
        self._ingest_queue: queue.Queue = queue.Queue(maxsize=queue_size)
        self._write_queue: queue.Queue = queue.Queue(maxsize=queue_size)
        self._validators: list[threading.Thread] = []
        self._writer: threading.Thread | None = None
        self._disease_codes = {d["disease_code"] for d in rules.diseases()}
        self.stats = PipelineStats()
        self.latest_reports = reports.LatestReports()

    # ---- lifecycle -------------------------------------------------------

    def start(self) -> None:
        self.latest_reports.load(self._db_path)
        self._validators = [
            threading.Thread(target=self._validate_loop, name=f"validator-{i + 1}", daemon=True)
            for i in range(self._worker_count)
        ]
        self._writer = threading.Thread(target=self._write_loop, name="db-writer", daemon=True)
        for thread in [*self._validators, self._writer]:
            thread.start()

    def stop(self) -> None:
        """Finish every batch already queued, then stop the threads."""
        for _ in self._validators:
            self._ingest_queue.put(_STOP)
        for thread in self._validators:
            thread.join()
        if self._writer is not None:
            self._write_queue.put(_STOP)
            self._writer.join()

    # ---- receiver side (called by the HTTP endpoint) --------------------

    def submit(self, batch: SubmitBatch) -> int:
        """Queue a batch without waiting for it to be processed. Returns the queue depth."""
        try:
            self._ingest_queue.put_nowait(_ReceivedBatch(batch, db.utc_now()))
        except queue.Full:
            self.stats.add(batches_busy=1)
            raise PipelineBusy from None
        self.stats.add(batches_received=1, records_received=len(batch.records))
        return self._ingest_queue.qsize()

    def snapshot(self) -> dict:
        """Counters plus current queue depths, for the System Monitor."""
        return {
            **self.stats.snapshot(),
            "ingest_queue": self._ingest_queue.qsize(),
            "write_queue": self._write_queue.qsize(),
            "queue_capacity": self._ingest_queue.maxsize,
            "validator_threads": self._worker_count,
        }

    # ---- validator threads -----------------------------------------------

    def _validate_loop(self) -> None:
        while (item := self._ingest_queue.get()) is not _STOP:
            self._write_queue.put(self._validate(item))

    def _validate(self, item: _ReceivedBatch) -> _ValidatedBatch:
        # One day of slack so a node whose clock or timezone is slightly ahead isn't rejected.
        latest_allowed = date.today() + timedelta(days=1)
        valid: list[CaseRecord] = []
        rejected: list[dict] = []
        seen: set = set()

        for record in item.batch.records:
            reason = self._rejection_reason(record, seen, latest_allowed)
            if reason:
                rejected.append({"record_id": str(record.record_id), "reason": reason})
            else:
                valid.append(record)
            seen.add(record.record_id)

        return _ValidatedBatch(item.batch, item.received_at, valid, rejected)

    def _rejection_reason(self, record: CaseRecord, seen: set, latest_allowed: date) -> str | None:
        if record.record_id in seen:
            return "DUPLICATE_IN_BATCH"
        if record.disease_code not in self._disease_codes:
            return "INVALID_DISEASE_CODE"
        if record.consult_date < record.onset_date:
            return "CONSULT_BEFORE_ONSET"
        if record.consult_date > latest_allowed:
            return "FUTURE_DATE"
        return None

    # ---- DB writer thread --------------------------------------------------

    def _write_loop(self) -> None:
        conn = db.connect(self._db_path)
        try:
            stopping = False
            while not stopping:
                first = self._write_queue.get()
                if first is _STOP:
                    break
                # Group commit: take whatever else is already waiting (up to a limit)
                # and write it all in one transaction. Under a burst this is far
                # faster than one commit per batch.
                group = [first]
                while len(group) < config.WRITE_GROUP_MAX:
                    try:
                        item = self._write_queue.get_nowait()
                    except queue.Empty:
                        break
                    if item is _STOP:
                        stopping = True
                        break
                    group.append(item)
                self._write_group(conn, group)
        finally:
            conn.close()

    def _write_group(self, conn: sqlite3.Connection, group: list[_ValidatedBatch]) -> None:
        try:
            with conn:
                messages = [(item, *self._write_batch(conn, item)) for item in group]
        except sqlite3.Error:
            # One bad batch must not lose the others: retry them one at a time.
            log.exception("Group commit of %d batches failed; retrying individually", len(group))
            messages = []
            for item in group:
                try:
                    with conn:
                        messages.append((item, *self._write_batch(conn, item)))
                except sqlite3.Error:
                    log.exception("Could not store batch %s", item.batch.batch_id)

        # Count and notify only after the commit, so STORED always means the data is safe on disk.
        for item, node_id, message, resent in messages:
            if resent:
                # Every record of a resent batch is already stored, so all of them count as duplicates.
                batch_size = message["accepted"] + message["updated"] + message["duplicates"] + len(message["rejected"])
                self.stats.add(batches_resent=1, records_duplicate=batch_size)
            else:
                self.stats.add(
                    batches_stored=1,
                    records_accepted=message["accepted"],
                    records_updated=message["updated"],
                    records_duplicate=message["duplicates"],
                    records_rejected=len(message["rejected"]),
                )
                if not item.batch.drill and message["accepted"] + message["updated"]:
                    self.latest_reports.add(reports.from_records(item.valid, barangay_of(node_id), item.received_at))
            self._notify(node_id, message)

    def _write_batch(self, conn: sqlite3.Connection, item: _ValidatedBatch) -> tuple[str, dict, bool]:
        """Write one batch inside the caller's transaction. Returns (node_id, STORED message, was_resent)."""
        batch = item.batch
        batch_id = str(batch.batch_id)

        previous = conn.execute(
            "SELECT accepted, updated, duplicates FROM batches WHERE batch_id = ?", (batch_id,)
        ).fetchone()
        if previous is not None:
            # The node resent a batch we already stored (e.g. it missed the STORED message).
            message = _stored_message(batch_id, previous["accepted"], previous["updated"], previous["duplicates"], item.rejected)
            return batch.node_id, message, True

        conn.execute(
            """
            INSERT INTO batches (batch_id, node_id, record_count, sent_at, received_at, status, drill)
            VALUES (?, ?, ?, ?, ?, 'STORED', ?)
            """,
            (batch_id, batch.node_id, len(batch.records), batch.sent_at.isoformat(), item.received_at, int(batch.drill)),
        )
        barangay_id = barangay_of(batch.node_id)
        rows = lambda records: [
            (
                str(r.record_id), batch_id, batch.node_id, barangay_id, r.disease_code,
                r.onset_date.isoformat(), r.consult_date.isoformat(), r.age_years,
                r.sex, r.purok, r.case_status, item.received_at, r.revision, int(r.voided),
            )
            for r in records
        ]
        originals = [r for r in item.valid if r.revision == 0]
        revisions = [r for r in item.valid if r.revision > 0]
        # INSERT OR IGNORE skips record_ids we already have, so those are the duplicates.
        cursor = conn.executemany(f"INSERT OR IGNORE INTO cases {_CASE_COLUMNS} VALUES {_CASE_VALUES}", rows(originals))
        accepted = cursor.rowcount if originals else 0
        updated = 0
        if revisions:
            # Only a newer revision from the same barangay replaces what we have, so resent
            # or out-of-order messages can't undo a later correction.
            cursor = conn.executemany(
                f"""
                INSERT INTO cases {_CASE_COLUMNS} VALUES {_CASE_VALUES}
                ON CONFLICT (record_id) DO UPDATE SET
                  batch_id = excluded.batch_id, node_id = excluded.node_id,
                  disease_code = excluded.disease_code, onset_date = excluded.onset_date,
                  consult_date = excluded.consult_date, age_years = excluded.age_years,
                  sex = excluded.sex, purok = excluded.purok, case_status = excluded.case_status,
                  received_at = excluded.received_at, revision = excluded.revision, voided = excluded.voided
                WHERE excluded.revision > cases.revision AND excluded.barangay_id = cases.barangay_id
                """,
                rows(revisions),
            )
            updated = cursor.rowcount
        duplicates = len(item.valid) - accepted - updated
        conn.execute(
            "UPDATE batches SET accepted = ?, updated = ?, duplicates = ?, rejected = ?, stored_at = ? WHERE batch_id = ?",
            (accepted, updated, duplicates, len(item.rejected), db.utc_now(), batch_id),
        )
        return batch.node_id, _stored_message(batch_id, accepted, updated, duplicates, item.rejected), False

    def _notify(self, node_id: str, message: dict) -> None:
        if self._on_stored is None:
            return
        try:
            self._on_stored(node_id, message)
        except Exception:
            # A failed notification must never stop the writer thread.
            log.exception("on_stored callback failed for batch %s", message["batch_id"])


def _stored_message(batch_id: str, accepted: int, updated: int, duplicates: int, rejected: list[dict]) -> dict:
    return {
        "type": "STORED",
        "batch_id": batch_id,
        "accepted": accepted,
        "updated": updated,
        "duplicates": duplicates,
        "rejected": rejected,
    }

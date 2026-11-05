"""Each BHU's own records: one SQLite file per barangay (var/bhu/BRGY-08.db), separate from
the RHU's database. Names and clinical details live only here.

Reports reach the RHU through a transactional outbox: saving a checkup writes the record and
its de-identified report in one transaction, and OutboxRelay then feeds the report into the
ingestion pipeline. A report stays in the outbox until the RHU confirms it (STORED), so a
crash at any point can only cause a resend, which the RHU ignores by record_id and revision.
"""

import json
import logging
import sqlite3
import threading
import time
import uuid
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path

from pydantic import ValidationError

from rhu import db, rules
from rhu.config import PROTOCOL_VERSION
from rhu.ingest import IngestPipeline, PipelineBusy
from rhu.models import SubmitBatch

log = logging.getLogger(__name__)

BACKUP_FORMAT = "healthpulz-bhu-backup"
OTHER_CODE = "OTH"
MAX_AGE = 110
RELAY_POLL_SECONDS = 2.0
RESEND_AFTER_SECONDS = 60.0  # a report sent but not confirmed by then is sent again
BUSY_WAIT_SECONDS = 1.0

SYNC_WAITING = "WAITING"
SYNC_STORED = "STORED"
SYNC_REJECTED = "REJECTED"

_SCHEMA = """
PRAGMA journal_mode = WAL;
CREATE TABLE IF NOT EXISTS clients (
  client_id  TEXT PRIMARY KEY,
  data       TEXT NOT NULL                -- JSON: demographics
);
CREATE TABLE IF NOT EXISTS consultations (
  consultation_id TEXT PRIMARY KEY,
  client_id       TEXT NOT NULL REFERENCES clients(client_id),
  batch_id        TEXT NOT NULL,          -- the report's current batch
  data            TEXT NOT NULL           -- JSON: the checkup, its delivery status and change history
);
CREATE INDEX IF NOT EXISTS idx_consultations_client ON consultations(client_id);
CREATE INDEX IF NOT EXISTS idx_consultations_batch ON consultations(batch_id);
CREATE TABLE IF NOT EXISTS outbox (
  batch_id   TEXT PRIMARY KEY,
  payload    TEXT NOT NULL,               -- JSON: SUBMIT_BATCH message for the RHU
  created_at TEXT NOT NULL
);
"""


class RecordNotFound(LookupError):
    pass


class InvalidRecord(ValueError):
    """The record breaks a rule; the message is shown to the BHU worker."""


def station_id(barangay_id: str) -> str:
    """The barangay's one station: every device logged in as this barangay shares it."""
    return f"{barangay_id}-MAIN"


def age_on(birthdate: str, day: str) -> int:
    born, on = date.fromisoformat(birthdate), date.fromisoformat(day)
    return on.year - born.year - ((on.month, on.day) < (born.month, born.day))


def to_case_record(client: dict, consultation: dict) -> dict:
    """What the RHU receives for a checkup. Nothing here identifies the client."""
    return {
        "record_id": consultation["consultation_id"],
        "revision": consultation["revision"],
        "voided": consultation["voided"],
        "disease_code": consultation["diagnosis_code"],
        "onset_date": consultation.get("onset_date") or consultation["visit_date"],
        "consult_date": consultation["visit_date"],
        "age_years": age_on(client["birthdate"], consultation["visit_date"]),
        "sex": client["sex"],
        # Where the client lived at the visit; moving later doesn't change past reports.
        "purok": consultation.get("purok", client["purok"]),
        "case_status": consultation["case_status"],
    }


def _report_batch(barangay_id: str, batch_id: str, record: dict) -> dict:
    return {
        "type": "SUBMIT_BATCH",
        "protocol_version": PROTOCOL_VERSION,
        "node_id": station_id(barangay_id),
        "batch_id": batch_id,
        "sent_at": db.utc_now(),
        "records": [record],
        "drill": False,
    }


def _check_client(fields: dict) -> None:
    today = date.today().isoformat()
    if fields["birthdate"] > today:
        raise InvalidRecord("The birthdate can't be in the future.")
    if age_on(fields["birthdate"], today) > MAX_AGE:
        raise InvalidRecord(f"Check the year: age must be {MAX_AGE} or below.")


def _check_consultation(client: dict, fields: dict) -> None:
    disease = next((d for d in rules.diseases() if d["disease_code"] == fields["diagnosis_code"]), None)
    if disease is None:
        raise InvalidRecord("Unknown diagnosis.")
    # One day of slack, so a device whose clock or timezone is slightly ahead isn't refused.
    latest = (date.today() + timedelta(days=1)).isoformat()
    if fields["visit_date"] > latest:
        raise InvalidRecord("The visit date can't be in the future.")
    if fields["visit_date"] < client["birthdate"]:
        raise InvalidRecord("The visit can't be before the client's birthdate.")
    onset = fields.get("onset_date")
    if onset and onset > fields["visit_date"]:
        raise InvalidRecord("Symptom onset can't be after the visit date.")
    if not onset and (disease["threshold_alerts"] or disease["single_case_alert"]):
        raise InvalidRecord("Symptom onset is required for this diagnosis.")
    if fields["diagnosis_code"] == OTHER_CODE and not (fields.get("diagnosis_text") or "").strip():
        raise InvalidRecord("Please specify the diagnosis.")


def _normalized(fields: dict) -> dict:
    """"Other" visits aren't surveillance cases, but the protocol requires a status."""
    tracked = fields["diagnosis_code"] != OTHER_CODE
    return {
        **fields,
        "diagnosis_text": "" if tracked else (fields.get("diagnosis_text") or "").strip(),
        "case_status": fields.get("case_status") or "SUSPECTED" if tracked else "CONFIRMED",
    }


class BhuStores:
    """The records of all BHUs, one file each. A barangay's data is only ever read from its own file."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self._ready: set[str] = set()
        self._lock = threading.Lock()

    def path(self, barangay_id: str) -> Path:
        return self.directory / f"{barangay_id}.db"

    def connect(self, barangay_id: str) -> sqlite3.Connection:
        conn = None
        # The tables are created under the lock: a page loads several things at once, and the
        # other requests of a barangay's first use must wait until the tables exist.
        with self._lock:
            if barangay_id not in self._ready:
                self.directory.mkdir(parents=True, exist_ok=True)
                conn = sqlite3.connect(self.path(barangay_id), timeout=30, check_same_thread=False)
                conn.executescript(_SCHEMA)
                self._ready.add(barangay_id)
        conn = conn or sqlite3.connect(self.path(barangay_id), timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def barangays_with_records(self) -> list[str]:
        return [p.stem for p in self.directory.glob("BRGY-*.db")] if self.directory.exists() else []

    # ---- clients ---------------------------------------------------------------------

    def list_clients(self, barangay_id: str) -> list[dict]:
        with closing(self.connect(barangay_id)) as conn:
            return [json.loads(r["data"]) for r in conn.execute("SELECT data FROM clients")]

    def get_client(self, barangay_id: str, client_id: str) -> dict:
        with closing(self.connect(barangay_id)) as conn:
            return self._client(conn, client_id)

    @staticmethod
    def _client(conn: sqlite3.Connection, client_id: str) -> dict:
        row = conn.execute("SELECT data FROM clients WHERE client_id = ?", (client_id,)).fetchone()
        if row is None:
            raise RecordNotFound("No such client.")
        return json.loads(row["data"])

    def save_client(self, barangay_id: str, client_id: str, fields: dict) -> dict:
        """Creates or updates a client. If an existing client's birthdate or sex changes, the
        reports the RHU already holds are wrong too, so they are sent again in the same transaction."""
        _check_client(fields)
        now = db.utc_now()
        with closing(self.connect(barangay_id)) as conn, conn:
            row = conn.execute("SELECT data FROM clients WHERE client_id = ?", (client_id,)).fetchone()
            current = json.loads(row["data"]) if row else None
            client = {
                **(current or {"created_at": now}),
                **fields,
                "client_id": client_id,
                "barangay_id": barangay_id,
                "updated_at": now,
            }
            visits = self._consultations(conn, "client_id = ?", (client_id,))
            first_visit = min((v["visit_date"] for v in visits), default=None)
            if first_visit and client["birthdate"] > first_visit:
                raise InvalidRecord(f"This client has a checkup on {first_visit}, before this birthdate.")
            conn.execute("INSERT OR REPLACE INTO clients (client_id, data) VALUES (?, ?)", (client_id, json.dumps(client)))
            if current and (current["birthdate"], current["sex"]) != (client["birthdate"], client["sex"]):
                entry = {"action": "CLIENT_CORRECTED", "reason": "Client birthdate or sex corrected", "changes": []}
                for visit in visits:
                    self._write_revision(conn, barangay_id, client, visit, {}, entry)
        return client

    # ---- consultations ----------------------------------------------------------------

    @staticmethod
    def _consultations(conn: sqlite3.Connection, where: str = "1", params: tuple = ()) -> list[dict]:
        rows = conn.execute(f"SELECT data FROM consultations WHERE {where}", params)
        return [json.loads(r["data"]) for r in rows]

    def list_consultations(self, barangay_id: str, client_id: str | None = None) -> list[dict]:
        with closing(self.connect(barangay_id)) as conn:
            visits = (
                self._consultations(conn, "client_id = ?", (client_id,)) if client_id else self._consultations(conn)
            )
        return sorted(visits, key=lambda v: (v["visit_date"], v["created_at"]), reverse=True)

    def get_consultation(self, barangay_id: str, consultation_id: str) -> dict:
        with closing(self.connect(barangay_id)) as conn:
            return self._consultation(conn, consultation_id)

    def _consultation(self, conn: sqlite3.Connection, consultation_id: str) -> dict:
        found = self._consultations(conn, "consultation_id = ?", (consultation_id,))
        if not found:
            raise RecordNotFound("No such checkup.")
        return found[0]

    @staticmethod
    def _put_with_report(conn: sqlite3.Connection, barangay_id: str, client: dict, consultation: dict) -> None:
        """The record and its report are saved together: neither can exist without the other."""
        conn.execute(
            "INSERT OR REPLACE INTO consultations (consultation_id, client_id, batch_id, data) VALUES (?, ?, ?, ?)",
            (consultation["consultation_id"], consultation["client_id"], consultation["batch_id"], json.dumps(consultation)),
        )
        batch = _report_batch(barangay_id, consultation["batch_id"], to_case_record(client, consultation))
        conn.execute(
            "INSERT OR REPLACE INTO outbox (batch_id, payload, created_at) VALUES (?, ?, ?)",
            (batch["batch_id"], json.dumps(batch), db.utc_now()),
        )

    def save_consultation(self, barangay_id: str, client_id: str, consultation_id: str, fields: dict) -> dict:
        """Idempotent: the browser chooses the ID, so a retried request can't record a checkup twice."""
        with closing(self.connect(barangay_id)) as conn, conn:
            client = self._client(conn, client_id)
            existing = self._consultations(conn, "consultation_id = ?", (consultation_id,))
            if existing:
                return existing[0]
            _check_consultation(client, fields)
            consultation = {
                **_normalized(fields),
                "consultation_id": consultation_id,
                "client_id": client_id,
                "barangay_id": barangay_id,
                "purok": client["purok"],
                "revision": 0,
                "voided": False,
                "history": [],
                "batch_id": str(uuid.uuid4()),
                "sync": SYNC_WAITING,
                "sync_detail": None,
                "created_at": db.utc_now(),
            }
            self._put_with_report(conn, barangay_id, client, consultation)
        return consultation

    def _write_revision(self, conn, barangay_id: str, client: dict, current: dict, fields: dict, entry: dict) -> dict:
        """A new version: a higher revision, so the RHU replaces its copy. Time-based, so it stays
        higher than anything sent before, even after an older backup is restored."""
        revised = {
            **current,
            **fields,
            "revision": max(time.time_ns() // 1_000_000, current["revision"] + 1),
            "batch_id": str(uuid.uuid4()),
            "sync": SYNC_WAITING,
            "sync_detail": None,
            "history": [*current["history"], {"at": db.utc_now(), **entry}],
        }
        self._put_with_report(conn, barangay_id, client, revised)
        return revised

    def correct_consultation(self, barangay_id: str, consultation_id: str, fields: dict, reason: str, changes: list[dict]) -> dict:
        with closing(self.connect(barangay_id)) as conn, conn:
            current = self._consultation(conn, consultation_id)
            if current["voided"]:
                raise InvalidRecord("A voided checkup can't be corrected.")
            client = self._client(conn, current["client_id"])
            _check_consultation(client, fields)
            entry = {"action": "CORRECTED", "reason": reason, "changes": changes}
            return self._write_revision(conn, barangay_id, client, current, _normalized(fields), entry)

    def void_consultation(self, barangay_id: str, consultation_id: str, reason: str) -> dict:
        with closing(self.connect(barangay_id)) as conn, conn:
            current = self._consultation(conn, consultation_id)
            if current["voided"]:
                return current
            client = self._client(conn, current["client_id"])
            entry = {"action": "VOIDED", "reason": reason, "changes": []}
            return self._write_revision(conn, barangay_id, client, current, {"voided": True}, entry)

    # ---- delivery ------------------------------------------------------------------------

    def pending_reports(self, barangay_id: str) -> list[tuple[str, str]]:
        """(batch_id, payload), oldest first."""
        with closing(self.connect(barangay_id)) as conn:
            return [tuple(r) for r in conn.execute("SELECT batch_id, payload FROM outbox ORDER BY created_at")]

    def record_delivery(self, barangay_id: str, batch_id: str, sync: str, detail: str | None = None) -> None:
        """The RHU answered: the report leaves the outbox, and the checkup shows the result.
        A batch of an older revision matches no checkup, since a newer report has replaced it."""
        with closing(self.connect(barangay_id)) as conn, conn:
            conn.execute("DELETE FROM outbox WHERE batch_id = ?", (batch_id,))
            for visit in self._consultations(conn, "batch_id = ?", (batch_id,)):
                updated = {**visit, "sync": sync, "sync_detail": detail}
                conn.execute(
                    "UPDATE consultations SET data = ? WHERE consultation_id = ?",
                    (json.dumps(updated), visit["consultation_id"]),
                )

    # ---- backup -----------------------------------------------------------------------------

    def export_backup(self, barangay_id: str) -> dict:
        return {
            "format": BACKUP_FORMAT,
            "version": 1,
            "barangay_id": barangay_id,
            "exported_at": db.utc_now(),
            "clients": self.list_clients(barangay_id),
            "consultations": self.list_consultations(barangay_id),
        }

    def import_backup(self, barangay_id: str, backup: dict) -> dict:
        """Restores records, also those moved from a browser. A record already here wins if it is
        newer. Reports that weren't confirmed are queued again; the RHU ignores what it already has."""
        if backup.get("format") != BACKUP_FORMAT or not isinstance(backup.get("clients"), list) or not isinstance(backup.get("consultations"), list):
            raise InvalidRecord("This file is not a HealthPulz backup.")
        if backup.get("barangay_id") != barangay_id:
            raise InvalidRecord("This backup belongs to a different barangay.")
        with closing(self.connect(barangay_id)) as conn, conn:
            clients = {}
            for client in backup["clients"]:
                row = conn.execute("SELECT data FROM clients WHERE client_id = ?", (client["client_id"],)).fetchone()
                current = json.loads(row["data"]) if row else None
                if current is None or client.get("updated_at", "") >= current.get("updated_at", ""):
                    conn.execute("INSERT OR REPLACE INTO clients (client_id, data) VALUES (?, ?)", (client["client_id"], json.dumps(client)))
                    current = client
                clients[client["client_id"]] = current
            imported = 0
            for visit in backup["consultations"]:
                client = clients.get(visit.get("client_id"))
                if client is None:
                    continue
                visit = {"revision": 0, "voided": False, "history": [], "sync_detail": None, "purok": client["purok"], **visit}
                found = self._consultations(conn, "consultation_id = ?", (visit["consultation_id"],))
                if found and found[0]["revision"] >= visit["revision"]:
                    continue
                imported += 1
                if visit["sync"] == SYNC_WAITING:
                    self._put_with_report(conn, barangay_id, client, visit)
                else:
                    conn.execute(
                        "INSERT OR REPLACE INTO consultations (consultation_id, client_id, batch_id, data) VALUES (?, ?, ?, ?)",
                        (visit["consultation_id"], visit["client_id"], visit["batch_id"], json.dumps(visit)),
                    )
        return {"clients": len(clients), "consultations": imported}


class OutboxRelay:
    """Thread that moves reports from the BHU outboxes into the ingestion pipeline, as if each
    BHU were sending them itself. Reports stay in the outbox until STORED arrives."""

    def __init__(self, stores: BhuStores, pipeline: IngestPipeline) -> None:
        self._stores = stores
        self._pipeline = pipeline
        self._in_flight: dict[str, tuple[str, float]] = {}  # batch_id -> (barangay_id, time sent)
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="outbox-relay", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        self._thread.join()

    def wake(self) -> None:
        """Called after a save, so the report goes out right away."""
        self._wake.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(RELAY_POLL_SECONDS)
            self._wake.clear()
            try:
                self._send_pending()
            except Exception:
                log.exception("Outbox relay failed; will retry")

    def _send_pending(self) -> None:
        now = time.monotonic()
        for barangay_id in self._stores.barangays_with_records():
            for batch_id, payload in self._stores.pending_reports(barangay_id):
                with self._lock:
                    sent = self._in_flight.get(batch_id)
                    if sent and now - sent[1] < RESEND_AFTER_SECONDS:
                        continue
                    self._in_flight[batch_id] = (barangay_id, now)
                try:
                    self._pipeline.submit(SubmitBatch.model_validate_json(payload))
                except PipelineBusy:
                    with self._lock:
                        self._in_flight.pop(batch_id, None)
                    self._stop.wait(BUSY_WAIT_SECONDS)
                    return
                except ValidationError:
                    # Can't happen with reports built here; never retry forever if it does.
                    log.exception("Malformed report %s", batch_id)
                    with self._lock:
                        self._in_flight.pop(batch_id, None)
                    self._stores.record_delivery(barangay_id, batch_id, SYNC_REJECTED, "the report was malformed")

    def on_stored(self, message: dict) -> None:
        """Called by the pipeline's writer thread after each commit. Only reports sent by this
        relay touch a BHU file, so a stress test sent from the browser costs nothing here."""
        with self._lock:
            sent = self._in_flight.pop(message["batch_id"], None)
        if sent is None:
            return
        rejected = message["rejected"]
        self._stores.record_delivery(
            sent[0],
            message["batch_id"],
            SYNC_REJECTED if rejected else SYNC_STORED,
            rejected[0]["reason"] if rejected else None,
        )

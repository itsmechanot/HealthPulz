"""SQLite connection helpers and database setup.

A connection is never used by two threads at the same time: each HTTP request
and the DB writer thread open their own.
"""

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from rhu import rules
from rhu.config import SCHEMA_PATH


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def connect(db_path: Path) -> sqlite3.Connection:
    # check_same_thread=False: FastAPI may open a request's connection in one
    # worker thread and run the endpoint in another. The connection is still
    # used by only one request at a time, so this is safe.
    conn = sqlite3.connect(db_path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: Path) -> None:
    """Create the tables (if missing) and load the reference data."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(connect(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        add_missing_columns(conn)
        create_analytics_index(conn)
        seed_reference_data(conn)


# Columns added after the first schema version. CREATE TABLE IF NOT EXISTS
# doesn't change existing tables, so older databases get them here.
_ADDED_COLUMNS = [
    ("accounts", "password_set_at", "TEXT"),
    ("nodes", "kind", "TEXT NOT NULL DEFAULT 'LIVE'"),
    ("batches", "drill", "INTEGER NOT NULL DEFAULT 0"),
    ("batches", "updated", "INTEGER"),
    ("cases", "revision", "INTEGER NOT NULL DEFAULT 0"),
    ("cases", "voided", "INTEGER NOT NULL DEFAULT 0"),
]


def add_missing_columns(conn: sqlite3.Connection) -> None:
    for table, column, definition in _ADDED_COLUMNS:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def create_analytics_index(conn: sqlite3.Connection) -> None:
    """Holds every column the analytics read, so each worker scans one index in order instead
    of jumping to a table row per case. That keeps the map step CPU-bound, which is what
    extra processes can speed up. Created here because `voided` may have just been added."""
    conn.execute("DROP INDEX IF EXISTS idx_cases_brgy_disease_date")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_cases_analytics ON cases(barangay_id, voided, disease_code, onset_date, batch_id)"
    )


def seed_reference_data(conn: sqlite3.Connection) -> None:
    """Upsert barangays and diseases so edits to the rules files take effect on restart."""
    with conn:
        conn.executemany(
            """
            INSERT INTO barangays (barangay_id, name, population, neighbors)
            VALUES (:barangay_id, :name, :population, :neighbors)
            ON CONFLICT (barangay_id) DO UPDATE SET
              name = excluded.name,
              population = excluded.population,
              neighbors = excluded.neighbors
            """,
            [{**b, "neighbors": json.dumps(b["neighbors"])} for b in rules.barangays()],
        )
        conn.executemany(
            """
            INSERT INTO diseases (disease_code, name, category, single_case_alert)
            VALUES (:disease_code, :name, :category, :single_case_alert)
            ON CONFLICT (disease_code) DO UPDATE SET
              name = excluded.name,
              category = excluded.category,
              single_case_alert = excluded.single_case_alert
            """,
            [{**d, "single_case_alert": int(d["single_case_alert"])} for d in rules.diseases()],
        )

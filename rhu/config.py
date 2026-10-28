"""Server settings. Values can be overridden with environment variables."""

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
ROOT_DIR = PACKAGE_DIR.parent

DATA_DIR = PACKAGE_DIR / "data"
SCHEMA_PATH = PACKAGE_DIR / "schema.sql"
DB_PATH = Path(os.environ.get("HEALTHPULZ_DB", ROOT_DIR / "var" / "healthpulz.db"))

PROTOCOL_VERSION = 1
MAX_RECORDS_PER_BATCH = 100

# Ingestion pipeline
INGEST_WORKERS = int(os.environ.get("HEALTHPULZ_INGEST_WORKERS", 4))
INGEST_QUEUE_SIZE = int(os.environ.get("HEALTHPULZ_INGEST_QUEUE_SIZE", 1000))
WRITE_GROUP_MAX = 20  # max batches the DB writer commits in one transaction
RETRY_AFTER_SECONDS = 2  # sent to nodes with 503 when the ingest queue is full

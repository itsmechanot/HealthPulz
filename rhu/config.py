"""Server settings. Values can be overridden with environment variables."""

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
ROOT_DIR = PACKAGE_DIR.parent

PLACE = "naval"
# PLACE = "caibiran"
PLACE = os.environ.get("HEALTHPULZ_PLACE", PLACE)

BASE_DATA_DIR = PACKAGE_DIR / "data"
DATA_DIR = BASE_DATA_DIR if PLACE == "naval" else BASE_DATA_DIR / "places" / PLACE
SCHEMA_PATH = PACKAGE_DIR / "schema.sql"
WEB_DIR = ROOT_DIR / "web"
_DEFAULT_DB = ROOT_DIR / "var" / "healthpulz.db" if PLACE == "naval" else ROOT_DIR / "var" / PLACE / "healthpulz.db"
DB_PATH = Path(os.environ.get("HEALTHPULZ_DB", _DEFAULT_DB))

PROTOCOL_VERSION = 1
MAX_RECORDS_PER_BATCH = 100

# Ingestion pipeline
INGEST_WORKERS = int(os.environ.get("HEALTHPULZ_INGEST_WORKERS", 4))
INGEST_QUEUE_SIZE = int(os.environ.get("HEALTHPULZ_INGEST_QUEUE_SIZE", 1000))
WRITE_GROUP_MAX = 20
RETRY_AFTER_SECONDS = 2

# Analytics process pool
ANALYTICS_WORKERS = int(os.environ.get("HEALTHPULZ_ANALYTICS_WORKERS", min(4, os.cpu_count() or 1)))
ANALYTICS_INTERVAL_SECONDS = 10
ANALYTICS_GATHER_SECONDS = 0.2

# Alerts
MIN_ALERT_CASES = 3

MAX_BURST_RECORDS_PER_DEVICE = 5000

# WebSockets
HEARTBEAT_TIMEOUT_SECONDS = 30
NODE_RECENT_HOURS = 12
MONITOR_INTERVAL_SECONDS = 1.0

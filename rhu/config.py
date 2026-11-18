"""Server settings. Values can be overridden with environment variables."""

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
ROOT_DIR = PACKAGE_DIR.parent

# ---- Which municipality this server runs for ------------------------------------------------
# Naval is the default. To run the Caibiran demo instead: put a # in front of the first line,
# remove the # from the second, and restart the server. Each municipality keeps its own
# database (var/ for Naval, var/caibiran/ for Caibiran), so switching never mixes their data.
PLACE = "naval"
# PLACE = "caibiran"
PLACE = os.environ.get("HEALTHPULZ_PLACE", PLACE)

BASE_DATA_DIR = PACKAGE_DIR / "data"  # diseases.json here is shared by every municipality
# Naval's files are in rhu/data/; another municipality's in rhu/data/places/<name>/
# (municipality.json, barangays.json, map.json, and optionally seal.png and barangays/<id>.png).
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
WRITE_GROUP_MAX = 20  # max batches the DB writer commits in one transaction
RETRY_AFTER_SECONDS = 2  # sent to nodes with 503 when the ingest queue is full

# Analytics process pool
ANALYTICS_WORKERS = int(os.environ.get("HEALTHPULZ_ANALYTICS_WORKERS", min(4, os.cpu_count() or 1)))
ANALYTICS_INTERVAL_SECONDS = 10  # safety net: how often the server checks for changes no report announced
ANALYTICS_GATHER_SECONDS = 0.2  # after a report, wait this long so reports arriving together share one run

# Alerts
MIN_ALERT_CASES = 3  # fewer cases never raise a threshold alert (cuts false alarms; see docs/analytics.md)

# Synchronized burst (System Monitor)
MAX_BURST_RECORDS_PER_DEVICE = 5000

# WebSockets
HEARTBEAT_TIMEOUT_SECONDS = 30  # a node with no heartbeat for this long is OFFLINE
NODE_RECENT_HOURS = 12  # the System Monitor hides nodes that haven't been seen for this long
MONITOR_INTERVAL_SECONDS = 1.0  # how often the System Monitor receives a snapshot

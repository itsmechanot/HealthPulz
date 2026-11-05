-- HealthPulz — RHU database schema
-- Status: Draft v1 (living document, update as development goes on)
-- Engine: SQLite in WAL mode (can be migrated to PostgreSQL later)

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- Barangays of Naval, Biliran (26). Populations are synthetic.
CREATE TABLE IF NOT EXISTS barangays (
  barangay_id   TEXT PRIMARY KEY,                 -- 'BRGY-08'
  name          TEXT NOT NULL UNIQUE,             -- 'Caraycaray'
  population    INTEGER NOT NULL,
  neighbors     TEXT NOT NULL DEFAULT '[]'        -- JSON array of barangay_ids (for spillover alerts)
);

-- One barangay can have several nodes (main BHS + satellite stations),
-- because 30-40 classmates act as nodes for 26 barangays.
CREATE TABLE IF NOT EXISTS nodes (
  node_id       TEXT PRIMARY KEY,                 -- 'BRGY-08-K7QX'
  barangay_id   TEXT NOT NULL REFERENCES barangays(barangay_id),
  registered_at TEXT NOT NULL,
  last_seen_at  TEXT,
  status        TEXT NOT NULL DEFAULT 'OFFLINE',  -- ONLINE | OFFLINE
  kind          TEXT NOT NULL DEFAULT 'LIVE'      -- LIVE (a real node) | HISTORY (imported past data)
);

CREATE TABLE IF NOT EXISTS diseases (
  disease_code       TEXT PRIMARY KEY,            -- 'DEN'
  name               TEXT NOT NULL,
  category           TEXT NOT NULL,               -- VECTOR | RESPIRATORY | WATERBORNE | VACCINE_PREVENTABLE | OTHER
  single_case_alert  INTEGER NOT NULL DEFAULT 0   -- 1 = any case triggers an alert (e.g., measles)
);

-- Audit trail: proves zero data loss after the demo
-- (sent = accepted + duplicates + rejected).
CREATE TABLE IF NOT EXISTS batches (
  batch_id      TEXT PRIMARY KEY,
  node_id       TEXT NOT NULL REFERENCES nodes(node_id),
  record_count  INTEGER NOT NULL,
  accepted      INTEGER,
  duplicates    INTEGER,
  rejected      INTEGER,
  sent_at       TEXT NOT NULL,
  received_at   TEXT NOT NULL,
  stored_at     TEXT,
  status        TEXT NOT NULL,                    -- QUEUED | STORED | FAILED
  drill         INTEGER NOT NULL DEFAULT 0        -- 1 = stress-test data: stored, but not analyzed
);

-- record_id is the idempotency key: inserts use INSERT OR IGNORE.
CREATE TABLE IF NOT EXISTS cases (
  record_id     TEXT PRIMARY KEY,
  batch_id      TEXT NOT NULL REFERENCES batches(batch_id),
  node_id       TEXT NOT NULL REFERENCES nodes(node_id),
  barangay_id   TEXT NOT NULL REFERENCES barangays(barangay_id),
  disease_code  TEXT NOT NULL REFERENCES diseases(disease_code),
  onset_date    TEXT NOT NULL,
  consult_date  TEXT NOT NULL,
  age_years     INTEGER NOT NULL CHECK (age_years BETWEEN 0 AND 110),
  sex           TEXT NOT NULL CHECK (sex IN ('M', 'F')),
  purok         INTEGER,
  case_status   TEXT NOT NULL,                    -- SUSPECTED | CONFIRMED
  received_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cases_brgy_disease_date ON cases(barangay_id, disease_code, onset_date);
CREATE INDEX IF NOT EXISTS idx_cases_disease_date      ON cases(disease_code, onset_date);
CREATE INDEX IF NOT EXISTS idx_cases_node              ON cases(node_id);

-- Output of the parallel analytics (process pool). Dashboards read this
-- instead of scanning every case.
CREATE TABLE IF NOT EXISTS weekly_stats (
  barangay_id      TEXT NOT NULL REFERENCES barangays(barangay_id),
  disease_code     TEXT NOT NULL REFERENCES diseases(disease_code),
  iso_year         INTEGER NOT NULL,
  iso_week         INTEGER NOT NULL,
  case_count       INTEGER NOT NULL,
  alert_threshold  REAL,
  epidemic_threshold REAL,
  PRIMARY KEY (barangay_id, disease_code, iso_year, iso_week)
);

CREATE TABLE IF NOT EXISTS alerts (
  alert_id      TEXT PRIMARY KEY,
  disease_code  TEXT NOT NULL REFERENCES diseases(disease_code),
  barangay_id   TEXT REFERENCES barangays(barangay_id),  -- NULL = municipality-wide
  level         TEXT NOT NULL,                    -- ALERT | EPIDEMIC
  iso_year      INTEGER NOT NULL,
  iso_week      INTEGER NOT NULL,
  observed      INTEGER NOT NULL,
  threshold     REAL NOT NULL,
  message       TEXT NOT NULL,
  status        TEXT NOT NULL DEFAULT 'OPEN',     -- OPEN | ACKNOWLEDGED | RESOLVED
  created_at    TEXT NOT NULL
);
-- One alert per barangay × disease × week (escalations update it).
CREATE UNIQUE INDEX IF NOT EXISTS idx_alerts_cell ON alerts(barangay_id, disease_code, iso_year, iso_week);

CREATE TABLE IF NOT EXISTS alert_deliveries (
  alert_id      TEXT NOT NULL REFERENCES alerts(alert_id),
  node_id       TEXT NOT NULL REFERENCES nodes(node_id),
  delivered_at  TEXT,
  acked_at      TEXT,
  PRIMARY KEY (alert_id, node_id)
);

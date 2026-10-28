# HealthPulz

Distributed disease surveillance for **RHU Naval, Biliran** and its 26 Barangay Health Stations (BHS).
Each BHS acts as an independent node that sends case data to the RHU server. The RHU processes the data
in parallel to detect illness trends and pushes outbreak alerts back to the barangays.

> All case data and populations are **synthetic**.

## Project structure

```
docs/            Design documents (protocol, synthetic data rules)
rhu/             RHU server (FastAPI)
  data/          Shared rules files: diseases.json, barangays.json
  schema.sql     Database schema (SQLite, WAL mode)
  api.py         HTTP endpoints
  ingest.py      Ingestion pipeline (queues, validator threads, DB writer thread)
  db.py          Database connection and setup
  models.py      Protocol message models
  main.py        App entry point
tests/           Automated tests (pytest)
tools/           Developer tools (node simulator / load test)
```

## Setup (Windows, PowerShell)

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
```

## Run the RHU server

```powershell
uvicorn rhu.main:app --host 0.0.0.0 --port 8000 --reload
```

- API docs: http://localhost:8000/docs
- The database is created automatically at `var/healthpulz.db` (override with the `HEALTHPULZ_DB` environment variable).
- `--host 0.0.0.0` lets classmates' devices on the same network connect.

## Run the tests

```powershell
pytest
```

## Simulate many nodes (load test)

With the server running, simulate classmates' nodes sending data at the same time:

```powershell
python tools/simulate_nodes.py --nodes 40 --batches 5 --records 100
```

It reports how long it took until every batch was RECEIVED and STORED, records per second, how many
`503` retries happened, and whether every record arrived.

## Documentation

- [Message protocol](docs/protocol.md)
- [Synthetic data rules](docs/synthetic-data.md)
- [Database schema](rhu/schema.sql)

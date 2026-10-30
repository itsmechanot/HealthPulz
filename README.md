# HealthPulz: A Distributed Disease Monitoring System for Barangay Health Centers

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
  analytics.py   Parallel analytics: weekly counts and thresholds (process pool, MapReduce)
  alerts.py      Outbreak alert detection and delivery (store-and-forward)
  synthetic.py   Synthetic case generator (Python twin of the node page's generator)
  history.py     Imports synthetic past data (python -m rhu.history)
  hub.py         Live WebSocket connections (nodes, monitors) and node presence
  ws.py          WebSocket endpoints and the System Monitor feed
  db.py          Database connection and setup
  models.py      Protocol message models
  main.py        App entry point
web/             Web pages served by the RHU (plain HTML/CSS/JS, no build step)
  css/base.css   Shared styles
  js/common/     API calls and ID helpers shared by all pages
  node/          BHS Node page
  js/node/       BHS Node logic: generator, offline outbox, WebSocket, sender
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

- Home page: http://localhost:8000/
- BHS Node page: http://localhost:8000/node/ (classmates use `http://<your-laptop-IP>:8000/node/`)
- API docs: http://localhost:8000/docs
- The database is created automatically at `var/healthpulz.db` (override with the `HEALTHPULZ_DB` environment variable).
- `--host 0.0.0.0` lets classmates' devices on the same network connect.

## Load past (synthetic) data

Outbreak thresholds compare this week with the same week in past years, so load the history once:

```powershell
python -m rhu.history
```

Run it again any time to replace the history; live data from nodes is kept. See [synthetic data rules](docs/synthetic-data.md).

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

## Benchmark the parallel analytics

```powershell
python -m rhu.history --db var/bench.db --scale 50
python tools/benchmark_analytics.py --db var/bench.db --workers 1 2 4 8
```

Reports time, speedup and efficiency per worker count. See [parallel analytics](docs/analytics.md).

## Documentation

- [Message protocol](docs/protocol.md)
- [Synthetic data rules](docs/synthetic-data.md)
- [Parallel analytics and benchmark](docs/analytics.md)
- [Database schema](rhu/schema.sql)

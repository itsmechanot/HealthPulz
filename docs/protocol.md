# HealthPulz — Message Protocol

**Status:** Draft v1 · living document, update as development goes on
**Applies to:** BHS Node app (browser) ↔ RHU Server (FastAPI)

## Overview

| Channel | Used for | Why |
|---|---|---|
| **HTTP POST** | Submitting case batches | Reliable request/response; each request gets an immediate reply |
| **WebSocket** | Heartbeats, `STORED` confirmations, alerts | Live, two-way message passing |

Every message is JSON and carries a `type` field.

---

## Login and access

There is **one shared login per BHU, one for the RHU**, and one for the load-test tool. The username is the barangay ID (`BRGY-08`), `RHU` or `LOADTEST`.

**Only the RHU gives a BHU its password**, on the dashboard's BHU Accounts screen. Until then the BHU can't log in. A password is shown once, when it is given, and never written to a file. The database holds only salted scrypt hashes of passwords and SHA-256 hashes of login tokens.

The RHU's and LOADTEST's own passwords are generated on first start and listed in `var/accounts.txt`, which git ignores:

```powershell
python -m rhu.accounts              # show them
python -m rhu.accounts reset RHU    # new password; logs that account's devices out
```

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/auth/login` | `{"username": "BRGY-08", "password": "…"}`. Returns `{username, role, barangay_id, home}` and sets the `healthpulz_session` cookie (HttpOnly, SameSite=Lax, 12 hours). `401` if wrong; `429` with `Retry-After` for 60 seconds after 5 wrong tries from one device |
| `POST` | `/api/auth/logout` | Ends the session on the server and clears the cookie |
| `GET` | `/api/auth/me` | The logged-in account, or `401` |
| `GET` | `/api/accounts` | RHU: every BHU with `assigned` and `password_set_at`, never the password |
| `POST` | `/api/accounts/{barangay_id}/password` | RHU: `{"password": "…"}` (8+ characters), or `{}` to generate one. Returns it once and logs out that BHU's devices |
| `POST` | `/api/accounts/assign-missing` | RHU: generates a password for every BHU without one and returns the list once |

**Who can do what.** The server enforces this; hiding a link protects nothing.

| | BHU | RHU | LOADTEST | Not logged in |
|---|---|---|---|---|
| `/api/bhu/{barangay_id}/…` (names, clinical details) | own barangay only | **no** | no | no |
| `POST /api/nodes/register`, `POST /api/batches`, `WS /ws/node/{node_id}` | own barangay only | no | any barangay | no |
| `GET /api/batches/{id}`, `/api/stats/…` (weekly totals) | yes | yes | yes | no |
| `/api/accounts…` (give passwords), `POST /api/burst` | no | yes | no | no |
| `/api/alerts…`, `/api/analytics…`, `/api/monitor/stats`, `/api/server/addresses`, `WS /ws/monitor` | no | yes | no | no |
| `/node/` page | yes | no | no | no |
| `/dashboard/`, `/monitor/` pages | no | yes | no | no |
| `/`, `/api/health`, `/api/config`, `/api/barangays` | yes | yes | yes | yes |

Refusals are `401` (not logged in, so the page goes to the login page) or `403` (logged in, not allowed). A page opened by the wrong account redirects to that account's own side. WebSockets close with `4401` or `4403`.

## 1. BHU accounts and stations

Each barangay is **one account with one fixed station**: `{barangay_id}-MAIN` (e.g. `BRGY-08-MAIN`). Logging in with the barangay's password opens that account, so its records are the same on any device or address. Every connected device shares the station, and the System Monitor counts the devices.

`POST /api/nodes/register` makes sure the station exists:
```json
{ "type": "REGISTER", "node_id": "BRGY-08-MAIN", "barangay_id": "BRGY-08" }
```
Response:
```json
{ "type": "REGISTERED", "node_id": "BRGY-08-MAIN", "barangay_id": "BRGY-08", "barangay": "Caraycaray" }
```


### BHU records and the transactional outbox

Each barangay's records are in their own file, `var/bhu/{barangay_id}.db`, separate from the RHU's database. Endpoints are under `/api/bhu/{barangay_id}`:

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/clients`, `/clients/{id}` | List clients; one client |
| `PUT` | `/clients/{id}` | Create or update a client. Changing a birthdate or sex re-sends the client's reports |
| `GET` | `/consultations`, `?client_id=`, `/consultations/{id}` | Checkups, newest first |
| `PUT` | `/clients/{client_id}/consultations/{id}` | Save a checkup. The browser chooses the ID, so a retry can't record it twice |
| `POST` | `/consultations/{id}/correct` | `{ fields, reason, changes }` |
| `POST` | `/consultations/{id}/void` | `{ reason }` |
| `GET`, `POST` | `/backup` | Download or restore a barangay's records; a backup from another barangay is refused |

Saving a checkup writes the record and its report in **one transaction** in the barangay's file (the *transactional outbox* pattern). A relay thread (`OutboxRelay` in `rhu/bhu_store.py`) then sends each report through the ingestion pipeline below, in the same `SUBMIT_BATCH` format. The report stays in the outbox until `STORED` confirms it, so:
- a crash at any point can only cause a resend after the restart, and the RHU ignores what it already has
- if the queue is full, the relay waits and tries again
- a report not confirmed within 60 s is sent again

The BHU's checkup screens need a connection to the server. Without one, the form says nothing was saved and keeps what was typed. Only **Demo Tools** batches are sent from the browser, with their own offline queue.

---

## 2. Submitting cases

### Node → RHU: `SUBMIT_BATCH` (`POST /api/batches`)
```json
{
  "type": "SUBMIT_BATCH",
  "protocol_version": 1,
  "node_id": "BRGY-08-MAIN",
  "batch_id": "6f1c2e0a-8b7d-4c1e-9a55-1f2d3c4b5a60",
  "sent_at": "2026-11-18T09:15:02+08:00",
  "records": [
    {
      "record_id": "b3e1f7a2-0c4d-4e8a-9f11-2a7c5d9e8b10",
      "disease_code": "DEN",
      "onset_date": "2026-11-14",
      "consult_date": "2026-11-16",
      "age_years": 12,
      "sex": "F",
      "purok": 3,
      "case_status": "SUSPECTED"
    }
  ]
}
```

Rules:
- `batch_id` and `record_id` are UUID v4s created **on the node**. They make retries safe (idempotency). The node page builds them with `crypto.getRandomValues()`, because `crypto.randomUUID()` is unavailable on plain-HTTP LAN addresses such as `http://192.168.1.5:8000`.
- At most **100 records per batch**.
- **No names or addresses** are sent, only the purok number (Data Privacy Act of 2012). Names and clinical details stay in the barangay's own store; only this case report reaches the RHU's database.
- `disease_code` is one of the 19 tracked diseases in `rhu/data/diseases.json` (served at `GET /api/config`), or `OTH` for any other diagnosis. `OTH` is stored and counted as "Other consultations", but has no thresholds or alerts and stays out of the case totals. The specific diagnosis text stays at the BHU.
- Optional `"drill": true` marks **stress-test data**. It is validated, stored and counted like any batch, but **left out of the analytics**, so it can never raise an alert. The node page sets it in Stress test mode, and the load-test simulator always sets it.

### Corrections and voids

A BHU corrects a report by sending the **same `record_id`** in a new batch, with a higher `revision` (any increasing integer; the first report has 0). To void one, it sends `"voided": true` with a higher revision.

```json
{ "record_id": "b3e1f7a2-…", "revision": 1793512345678, "voided": false, "disease_code": "ILI", "…": "…" }
```

| The RHU already holds | What happens | Counted in `STORED` as |
|---|---|---|
| Nothing | Stored | `updated` (or `accepted` for revision 0) |
| A lower revision, same barangay | **Replaced** | `updated` |
| The same or a higher revision | Ignored, so late or resent messages can't undo a correction | `duplicates` |
| A case from another barangay | Ignored | `duplicates` |

Voided cases stay in the database for audit but are left out of the analytics. Corrections travel through the same queue, retries and offline outbox as new reports, and the next analytics run updates the counts. An alert raised before a correction stays open for RHU staff to review; `GET /api/alerts` includes `current_count` for this.

### Two-stage acknowledgement

**Stage 1: `RECEIVED`.** This is the HTTP response (`202 Accepted`), returned as soon as the batch is placed in the ingest queue.
```json
{ "type": "RECEIVED", "batch_id": "6f1c…", "queue_depth": 37 }
```

**Stage 2: `STORED`.** Sent over WebSocket after the DB writer thread commits the batch.
```json
{
  "type": "STORED",
  "batch_id": "6f1c…",
  "accepted": 98,
  "updated": 0,
  "duplicates": 1,
  "rejected": [{ "record_id": "…", "reason": "INVALID_DISEASE_CODE" }]
}
```

**Sender rule:** a batch stays in the sender's outbox until `STORED` arrives. If `STORED` doesn't arrive within **30 s**, the node can check `GET /api/batches/{batch_id}`, or simply resend. Resending is always safe: a batch that was already stored is not written again, and the server replies `STORED` with the original counts.

### Validation

| Checked by | What | Result |
|---|---|---|
| HTTP layer | Message structure (types, UUIDs, dates, 1–100 records, `protocol_version`) | Whole batch refused with `400` |
| Validator threads | `DUPLICATE_IN_BATCH`, `INVALID_DISEASE_CODE`, `CONSULT_BEFORE_ONSET`, `FUTURE_DATE` | Only that record is rejected and listed in `STORED.rejected` |

A record whose `record_id` is already in the database (sent before in another batch) is counted in `duplicates`, not stored again.

### Error responses

| Code | When | Node behavior |
|---|---|---|
| `400` | Malformed batch | Log it, don't retry |
| `404` | Unknown `node_id` (not registered) | Register again, then retry |
| `503` + `retry_after` | **Ingest queue full** (bounded, e.g. 1,000 batches) | Wait, then retry with exponential backoff |

The bounded queue and the `503` response are **backpressure**: the server protects itself under load instead of crashing.

---

## 3. WebSocket messages (`/ws/node/{node_id}`)

| Direction | Type | Payload |
|---|---|---|
| Node → RHU | `HELLO` (must be the first message) | `node_id`, `pending_batches` |
| RHU → Node | `WELCOME` | `node_id` |
| Node → RHU | `HEARTBEAT` (every 10 s) | `node_id`, `pending_batches` |
| RHU → Node | `STORED` | see above |
| RHU → Node | `ALERT` | `alert_id`, `scope`, `acknowledged`, `level`, `disease_code`, `barangay_id`, `iso_year`, `iso_week`, `observed`, `threshold`, `message`, `created_at` |
| Node → RHU | `ALERT_ACK` | `alert_id` |
| RHU → Node | `BURST` | `records_per_device`. Every connected device sends that many test records (`drill: true`) right away, using the normal `POST /api/batches`. Not kept for devices that connect later |
| RHU → Node | `ERROR` | `reason` (e.g. `UNKNOWN_TYPE`) |

**Handshake:** the node connects, sends `HELLO`, and waits for `WELCOME`. The RHU closes the connection with:
- `4400` if the first message isn't a `HELLO` for the `node_id` in the URL
- `4404` if the node isn't registered (register first, then reconnect)

A node with no heartbeat for **30 s**, or with no open connection, is shown as **OFFLINE** on the System Monitor. A station can have many connections open, one per device logged in as that barangay; each receives the messages.

**If the node is offline when its batch is stored**, the `STORED` message is not queued for later. The node recovers by checking `GET /api/batches/{batch_id}` or by resending (safe, see above).

### Alerts

Example `ALERT`:
```json
{
  "type": "ALERT",
  "alert_id": "a91e…",
  "scope": "LOCAL",
  "acknowledged": false,
  "level": "ALERT",
  "disease_code": "DEN",
  "barangay_id": "BRGY-08",
  "iso_year": 2026,
  "iso_week": 43,
  "observed": 9,
  "threshold": 8.58,
  "message": "Dengue cases in Brgy. Caraycaray in the week of Oct 19 – 25, 2026: 9, above the alert threshold of 8.6.",
  "created_at": "2026-10-30T08:04:12.511+00:00"
}
```

**When alerts are raised:** after every analytics run (moments after real reports are stored), the RHU checks the **previous and current ISO week**. A case counts in the week of its checkup, and a BHU can enter a checkup a few days late, so last week can still change.

| Level | Condition |
|---|---|
| `URGENT` | any single case of the RHU's 6 urgent diseases (AFP, diphtheria, measles, neonatal and non-neonatal tetanus, pertussis) |
| `EPIDEMIC` | count > epidemic threshold and count ≥ 3 |
| `ALERT` | count > alert threshold and count ≥ 3 |

Hypertension, arthritis, kidney diseases, wounds and `OTH` are counted but never raise alerts.

**One alert per barangay × disease × week.** If the count keeps rising at the same level, the alert's count is updated quietly. It is sent again only if it **escalates** to a higher level, with the same `alert_id`, so the node updates its card instead of adding a new one.

**Who receives it:** every live node of the affected barangay (`scope: "LOCAL"`) and of its neighboring barangays (`scope: "NEIGHBOR"`).

**Store-and-forward:** right after its `WELCOME`, a node receives every unresolved alert of the checked weeks that concerns it. Alerts it missed while offline come with `"acknowledged": false`; alerts it already acknowledged come again with `"acknowledged": true`, so a reloaded page can list them again **without** a new pop-up or vibration. Alerts the RHU marked `RESOLVED` are not sent. Each delivery and each `ALERT_ACK` is recorded in `alert_deliveries`, so the RHU can see which stations have seen an alert.

---

## 4. Other endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/config` | Disease rules + barangay list (shared by both generators and the dropdown) |
| `GET` | `/api/barangays` | Barangay list for the dropdown |
| `GET` | `/api/server/addresses` | This server's LAN IPv4 addresses and port, shown on the System Monitor as the join address, and `reachable`: `false` if the server only accepts connections from its own laptop |
| `GET` | `/api/batches/{batch_id}` | Batch status: `PENDING` (not stored yet) or `STORED` with counts |
| `GET` | `/api/monitor/stats` | Pipeline counters and queue depths (polling version of `/ws/monitor`) |
| `GET` | `/api/analytics` | Last analytics run: workers, cases read, map/reduce time |
| `POST` | `/api/analytics/run` | Run analytics + alert detection now; returns the run and `alerts_sent` |
| `GET` | `/api/alerts?status=OPEN&limit=100` | Alerts, newest first, with `nodes_delivered` and `nodes_acknowledged` |
| `POST` | `/api/alerts/{alert_id}/status` | RHU staff: `{"status": "ACKNOWLEDGED" \| "RESOLVED" \| "OPEN"}` |
| `POST` | `/api/burst` | RHU: `{"records_per_device": 500}` (1–5000) sends `BURST` to every connected device. Returns `{devices, records_per_device}` |
| `WS` | `/ws/monitor` | Live stats for the System Monitor (see below) |

### Statistics (RHU dashboard and BHS nodes)

These read `weekly_stats`, the output of the parallel analytics, so they return in ~10 ms. They return **aggregated weekly counts only, never individual cases**. That is why barangays may see municipal data without breaking the Data Privacy Act. Drill data is never included.

| Method | Path | Returns |
|---|---|---|
| `GET` | `/api/stats/summary` | Per tracked disease, with its `group`: cases **this week (so far)** vs **last week**, % change, barangays with an open alert; `other_consultations` (this week, last week); total open alerts; when the analytics last ran |
| `GET` | `/api/stats/trend?disease=DEN&barangay_id=BRGY-08&weeks=26` | Weekly counts, oldest first, up to the current week. Without `barangay_id` it covers the whole municipality; without `disease`, all tracked diseases (`disease=OTH` asks for Other consultations, which have no thresholds). Threshold lines are included only for one barangay **and** one disease with thresholds (summing thresholds isn't statistically valid). |
| `GET` | `/api/stats/barangays?week=2026-W44` | Every barangay × disease for one week (default: current): cases, thresholds, level (`NORMAL` / `ALERT` / `EPIDEMIC` / `URGENT`, same rule as the alerts), barangay total and **rate per 10,000 people** (fair comparison of barangays of different sizes), and `other_consultations` (not part of the total or rate) |

Example `GET /api/stats/summary` (shortened):
```json
{
  "week": { "iso_year": 2026, "iso_week": 44, "label": "2026-W44", "dates": "Oct 26 – Nov 1, 2026", "start": "2026-10-26" },
  "previous_week": { "iso_year": 2026, "iso_week": 43, "label": "2026-W43", "dates": "Oct 19 – 25, 2026", "start": "2026-10-19" },
  "diseases": [
    { "disease_code": "DEN", "name": "Dengue", "this_week": 4, "last_week": 26, "change_pct": -84.6, "barangays_on_alert": 0 }
  ],
  "totals": { "this_week": 4, "last_week": 85 },
  "open_alerts": 2,
  "analyzed_at": "2026-10-30T08:41:07.204+00:00"
}
```

### System Monitor feed (`/ws/monitor`)

The RHU sends one `MONITOR` message on connect, then one every second. The monitor page only listens.
```json
{
  "type": "MONITOR",
  "at": "2026-11-18T01:15:03.120+00:00",
  "pipeline": {
    "batches_received": 200, "batches_busy": 0, "batches_stored": 200, "batches_resent": 0, "records_received": 20000,
    "records_accepted": 20000, "records_duplicate": 0, "records_rejected": 0,
    "ingest_queue": 0, "write_queue": 0, "queue_capacity": 1000, "validator_threads": 4, "records_per_second": 12401.6
  },
  "analytics": { "workers": 4, "partitions": 26, "cases": 18472, "rows": 31200, "map_seconds": 0.39, "reduce_seconds": 0.21, "total_seconds": 0.6, "finished_at": "2026-10-30T09:18:36.410+00:00" },
  "nodes": [
    { "node_id": "BRGY-08-MAIN", "barangay_id": "BRGY-08", "status": "ONLINE", "pending_batches": 0, "connections": 2 }
  ]
}
```

When an analytics run finishes, the same socket also gets `{"type": "ANALYTICS_DONE", "finished_at": "…"}` right away, so dashboards can redraw without waiting for the next snapshot.

`latest_reports` is also in each `MONITOR` message: the 20 newest de-identified reports, newest first, for the RHU dashboard (`record_id`, `barangay_id`, `disease_code`, `onset_date`, `consult_date`, `age_years`, `sex`, `purok`, `case_status`, `received_at`, `revision`, `voided`, `counted_in_week`, `counted_week_dates`). Stress-test data (`drill`) and imported history are left out.

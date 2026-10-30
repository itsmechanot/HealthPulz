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

## 1. Node registration (dropdown login)

Classmates choose their barangay from a **searchable dropdown** (type to filter, e.g. "cara" → *Caraycaray*). Because there are more classmates (30–40) than Naval barangays (26), **several nodes can serve one barangay**, just as a large barangay can have a main BHS plus satellite stations.

- On first use, the browser creates a `node_id` in the form `{barangay_id}-{4 random chars}` (e.g. `BRGY-08-K7QX`) and keeps it in `localStorage`.
- The node registers itself with:

`POST /api/nodes/register`
```json
{ "type": "REGISTER", "node_id": "BRGY-08-K7QX", "barangay_id": "BRGY-08" }
```
Response:
```json
{ "type": "REGISTERED", "node_id": "BRGY-08-K7QX", "barangay_id": "BRGY-08", "barangay": "Caraycaray" }
```

> **Scope note:** dropdown login has no password. That is acceptable for the class demo. A production version would use per-station credentials.

---

## 2. Submitting cases

### Node → RHU: `SUBMIT_BATCH` (`POST /api/batches`)
```json
{
  "type": "SUBMIT_BATCH",
  "protocol_version": 1,
  "node_id": "BRGY-08-K7QX",
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
- **No names or addresses** are sent, only the purok number (Data Privacy Act of 2012).
- Optional `"drill": true` marks **stress-test data**. It is validated, stored and counted like any batch, but **left out of the analytics**, so it can never raise an alert. The node page sets it in Stress test mode, and the load-test simulator always sets it.

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
  "duplicates": 1,
  "rejected": [{ "record_id": "…", "reason": "INVALID_DISEASE_CODE" }]
}
```

**Node rule:** a batch stays in the node's local queue (IndexedDB) until `STORED` arrives. If `STORED` doesn't arrive within **30 s**, the node can check `GET /api/batches/{batch_id}`, or simply resend. Resending is always safe: a batch that was already stored is not written again, and the server replies `STORED` with the original counts.

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
| RHU → Node | `ALERT` | `alert_id`, `scope`, `level`, `disease_code`, `barangay_id`, `iso_year`, `iso_week`, `observed`, `threshold`, `message`, `created_at` |
| Node → RHU | `ALERT_ACK` | `alert_id` |
| RHU → Node | `ERROR` | `reason` (e.g. `UNKNOWN_TYPE`) |

**Handshake:** the node connects, sends `HELLO`, and waits for `WELCOME`. The RHU closes the connection with:
- `4400` if the first message isn't a `HELLO` for the `node_id` in the URL
- `4404` if the node isn't registered (register first, then reconnect)

A node with no heartbeat for **30 s**, or with no open connection, is shown as **OFFLINE** on the System Monitor. A node may have several connections open (e.g. two tabs); each receives the messages.

**If the node is offline when its batch is stored**, the `STORED` message is not queued for later. The node recovers by checking `GET /api/batches/{batch_id}` or by resending (safe, see above).

### Alerts

Example `ALERT`:
```json
{
  "type": "ALERT",
  "alert_id": "a91e…",
  "scope": "LOCAL",
  "level": "ALERT",
  "disease_code": "DEN",
  "barangay_id": "BRGY-08",
  "iso_year": 2026,
  "iso_week": 43,
  "observed": 9,
  "threshold": 8.58,
  "message": "Dengue cases in Brgy. Caraycaray in week 2026-W43: 9, above the alert threshold of 8.6.",
  "created_at": "2026-10-30T08:04:12.511+00:00"
}
```

**When alerts are raised:** after every analytics run (every 10 s when cases changed), the RHU checks the **previous and current ISO week**. Cases are often reported a few days after onset, so last week can still change.

| Level | Condition |
|---|---|
| `EPIDEMIC` | count > epidemic threshold and count ≥ 3 |
| `ALERT` | count > alert threshold and count ≥ 3 |
| `ALERT` (measles) | any single case |

**One alert per barangay × disease × week.** If the count keeps rising at the same level, the alert's count is updated quietly. It is sent again only if it **escalates** from `ALERT` to `EPIDEMIC`, with the same `alert_id`, so the node updates its card instead of adding a new one.

**Who receives it:** every live node of the affected barangay (`scope: "LOCAL"`) and of its neighboring barangays (`scope: "NEIGHBOR"`).

**Store-and-forward:** a node that is offline when an alert is raised receives it right after its next `WELCOME`, unless it already acknowledged it or the RHU marked it `RESOLVED`. Each delivery and each `ALERT_ACK` is recorded in `alert_deliveries`, so the RHU can see which stations have seen an alert.

---

## 4. Other endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/config` | Disease rules + barangay list (shared by both generators and the dropdown) |
| `GET` | `/api/barangays` | Barangay list for the dropdown |
| `GET` | `/api/batches/{batch_id}` | Batch status: `PENDING` (not stored yet) or `STORED` with counts |
| `GET` | `/api/monitor/stats` | Pipeline counters and queue depths (polling version of `/ws/monitor`) |
| `GET` | `/api/analytics` | Last analytics run: workers, cases read, map/reduce time |
| `POST` | `/api/analytics/run` | Run analytics + alert detection now; returns the run and `alerts_sent` |
| `GET` | `/api/alerts?status=OPEN&limit=100` | Alerts, newest first, with `nodes_delivered` and `nodes_acknowledged` |
| `POST` | `/api/alerts/{alert_id}/status` | RHU staff: `{"status": "ACKNOWLEDGED" \| "RESOLVED" \| "OPEN"}` |
| `WS` | `/ws/monitor` | Live stats for the System Monitor (see below) |

### System Monitor feed (`/ws/monitor`)

The RHU sends one `MONITOR` message on connect, then one every second. The monitor page only listens.
```json
{
  "type": "MONITOR",
  "at": "2026-11-18T01:15:03.120+00:00",
  "pipeline": {
    "batches_received": 200, "batches_busy": 0, "batches_stored": 200, "batches_resent": 0,
    "records_accepted": 20000, "records_duplicate": 0, "records_rejected": 0,
    "ingest_queue": 0, "write_queue": 0, "validator_threads": 4, "records_per_second": 12401.6
  },
  "nodes": [
    { "node_id": "BRGY-08-K7QX", "barangay_id": "BRGY-08", "status": "ONLINE", "pending_batches": 0 }
  ]
}
```

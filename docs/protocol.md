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
- `batch_id` and `record_id` are UUIDs created **on the node** (`crypto.randomUUID()`). They make retries safe (idempotency).
- At most **100 records per batch**.
- **No names or addresses** are sent, only the purok number (Data Privacy Act of 2012).

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
| Node → RHU | `HELLO` | `node_id`, `pending_batches` |
| Node → RHU | `HEARTBEAT` (every 10 s) | `node_id`, `pending_batches` |
| RHU → Node | `STORED` | see above |
| RHU → Node | `ALERT` | `alert_id`, `level`, `disease_code`, `barangay_id`, `message`, `observed`, `threshold` |
| Node → RHU | `ALERT_ACK` | `alert_id` |

A node with no heartbeat for **30 s** is marked **OFFLINE** on the System Monitor.

Example `ALERT`:
```json
{
  "type": "ALERT",
  "alert_id": "a91e…",
  "level": "ALERT",
  "disease_code": "DEN",
  "barangay_id": "BRGY-08",
  "message": "Dengue cases in Caraycaray this week (14) exceed the alert threshold (6.2).",
  "observed": 14,
  "threshold": 6.2
}
```

Alerts are sent to every node of the affected barangay **and** to the nodes of its neighboring barangays.

---

## 4. Other endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/config` | Disease rules + barangay list (shared by both generators and the dropdown) |
| `GET` | `/api/barangays` | Barangay list for the dropdown |
| `GET` | `/api/batches/{batch_id}` | Batch status: `PENDING` (not stored yet) or `STORED` with counts |
| `GET` | `/api/monitor/stats` | Pipeline counters and queue depths (polling version of `/ws/monitor`) |
| `WS` | `/ws/monitor` | Live stats for the System Monitor (queue depth, throughput, worker activity, node status) |

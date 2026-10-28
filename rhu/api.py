"""HTTP endpoints used by the BHS nodes and the RHU dashboard."""

import sqlite3
from collections.abc import Iterator

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request

from rhu import db, rules
from rhu.config import PROTOCOL_VERSION, RETRY_AFTER_SECONDS
from rhu.ingest import IngestPipeline, PipelineBusy
from rhu.models import RegisterRequest, RegisterResponse, SubmitBatch

router = APIRouter(prefix="/api")


def get_conn(request: Request) -> Iterator[sqlite3.Connection]:
    conn = db.connect(request.app.state.db_path)
    try:
        yield conn
    finally:
        conn.close()


def get_pipeline(request: Request) -> IngestPipeline:
    return request.app.state.pipeline


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/config")
def get_config() -> dict:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "diseases": rules.diseases(),
        "barangays": rules.barangays(),
    }


@router.get("/barangays")
def list_barangays() -> list[dict]:
    return [{"barangay_id": b["barangay_id"], "name": b["name"]} for b in rules.barangays()]


@router.post("/nodes/register")
def register_node(req: RegisterRequest, conn: sqlite3.Connection = Depends(get_conn)) -> RegisterResponse:
    barangay = rules.barangays_by_id().get(req.barangay_id)
    if barangay is None:
        raise HTTPException(status_code=404, detail="Unknown barangay_id")

    # INSERT OR IGNORE makes registration idempotent: a node that reloads its
    # page (or two simultaneous requests) never causes an error.
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO nodes (node_id, barangay_id, registered_at) VALUES (?, ?, ?)",
            (req.node_id, req.barangay_id, db.utc_now()),
        )
    return RegisterResponse(node_id=req.node_id, barangay_id=req.barangay_id, barangay=barangay["name"])


@router.post("/batches", status_code=202)
def submit_batch(
    batch: SubmitBatch,
    conn: sqlite3.Connection = Depends(get_conn),
    pipeline: IngestPipeline = Depends(get_pipeline),
) -> dict:
    """Stage 1 of the acknowledgement: queue the batch and reply RECEIVED right away.
    STORED follows once the DB writer thread has committed it."""
    if conn.execute("SELECT 1 FROM nodes WHERE node_id = ?", (batch.node_id,)).fetchone() is None:
        raise HTTPException(status_code=404, detail="Unknown node_id; register first")

    try:
        depth = pipeline.submit(batch)
    except PipelineBusy:
        raise HTTPException(
            status_code=503,
            detail={"type": "BUSY", "retry_after": RETRY_AFTER_SECONDS},
            headers={"Retry-After": str(RETRY_AFTER_SECONDS)},
        ) from None
    return {"type": "RECEIVED", "batch_id": str(batch.batch_id), "queue_depth": depth}


@router.get("/batches/{batch_id}")
def batch_status(batch_id: UUID, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """Lets a node check a batch when it missed the STORED message.
    PENDING means not stored yet: still queued, or lost, so resend after the timeout."""
    row = conn.execute(
        "SELECT accepted, duplicates, rejected, stored_at FROM batches WHERE batch_id = ?", (str(batch_id),)
    ).fetchone()
    if row is None:
        return {"type": "BATCH_STATUS", "batch_id": str(batch_id), "status": "PENDING"}
    return {"type": "BATCH_STATUS", "batch_id": str(batch_id), "status": "STORED", **dict(row)}


@router.get("/monitor/stats")
def monitor_stats(pipeline: IngestPipeline = Depends(get_pipeline)) -> dict:
    return {
        **pipeline.stats.snapshot(),
        **pipeline.queue_depths(),
        "validator_threads": pipeline.worker_count,
    }

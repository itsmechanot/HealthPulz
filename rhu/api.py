"""HTTP endpoints used by the BHS nodes and the RHU dashboard."""

import asyncio
import sqlite3
from collections.abc import Iterator
from datetime import date
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from rhu import accounts, auth, db, network, rules, stats
from rhu.accounts import Session
from rhu.config import PROTOCOL_VERSION, RETRY_AFTER_SECONDS
from rhu.ingest import IngestPipeline, PipelineBusy
from rhu.weeks import week_dates
from rhu.models import AlertStatusUpdate, BurstRequest, GivePassword, RegisterRequest, RegisterResponse, SubmitBatch, barangay_of

# Every route needs a login unless it is on public_router, so a new route can't be left open by accident.
public_router = APIRouter(prefix="/api")
router = APIRouter(prefix="/api", dependencies=[Depends(auth.current_session)])


def get_conn(request: Request) -> Iterator[sqlite3.Connection]:
    conn = db.connect(request.app.state.db_path)
    try:
        yield conn
    finally:
        conn.close()


def get_pipeline(request: Request) -> IngestPipeline:
    return request.app.state.pipeline


@public_router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/server/addresses", dependencies=[Depends(auth.require_rhu)])
def server_addresses(request: Request) -> dict:
    """LAN addresses for the join banner, and whether phones can reach them."""
    addresses = network.lan_addresses()
    port = request.url.port
    # Unknown is not closed: no false alarm without a port or address.
    reachable = not (port and addresses) or any(network.accepts_connections(a, port) for a in addresses)
    return {"addresses": addresses, "port": port, "reachable": reachable}


@public_router.get("/config")
def get_config() -> dict:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "municipality": rules.municipality(),
        "diseases": rules.diseases(),
        "barangays": rules.barangays(),
    }


@public_router.get("/barangays")
def list_barangays() -> list[dict]:
    return [{"barangay_id": b["barangay_id"], "name": b["name"]} for b in rules.barangays()]


@router.post("/nodes/register")
def register_node(
    req: RegisterRequest,
    conn: sqlite3.Connection = Depends(get_conn),
    session: Session = Depends(auth.current_session),
) -> RegisterResponse:
    auth.require_may_send_for(session, req.barangay_id)
    barangay = rules.barangays_by_id().get(req.barangay_id)
    if barangay is None:
        raise HTTPException(status_code=404, detail="Unknown barangay_id")

    # INSERT OR IGNORE: registering again is harmless.
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
    session: Session = Depends(auth.current_session),
) -> dict:
    """Stage 1: queue the batch and reply RECEIVED; STORED follows over the WebSocket once committed."""
    auth.require_may_send_for(session, barangay_of(batch.node_id))
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
    """For a node that missed STORED. PENDING means resend after the timeout."""
    row = conn.execute(
        "SELECT accepted, updated, duplicates, rejected, stored_at FROM batches WHERE batch_id = ?", (str(batch_id),)
    ).fetchone()
    if row is None:
        return {"type": "BATCH_STATUS", "batch_id": str(batch_id), "status": "PENDING"}
    return {"type": "BATCH_STATUS", "batch_id": str(batch_id), "status": "STORED", **dict(row)}


@router.post("/burst", dependencies=[Depends(auth.require_rhu)])
async def start_burst(burst: BurstRequest, request: Request) -> dict:
    """Tell every connected BHU device to send test records at the same moment."""
    devices = await request.app.state.hub.broadcast_to_nodes(
        {"type": "BURST", "records_per_device": burst.records_per_device}
    )
    return {"devices": devices, "records_per_device": burst.records_per_device}


@router.get("/accounts", dependencies=[Depends(auth.require_rhu)])
def list_accounts(request: Request) -> list[dict]:
    return accounts.bhu_accounts(request.app.state.db_path)


@router.post("/accounts/assign-missing", dependencies=[Depends(auth.require_rhu)])
def assign_missing_passwords(request: Request) -> list[dict]:
    """Generates a password for every BHU without one. This is the only time they can be read."""
    return accounts.assign_missing(request.app.state.db_path)


@router.post("/accounts/{barangay_id}/password", dependencies=[Depends(auth.require_rhu)])
def give_password(barangay_id: str, give: GivePassword, request: Request) -> dict:
    """Gives one BHU a password (generated if none is sent) and logs out its devices."""
    barangay = rules.barangays_by_id().get(barangay_id)
    if barangay is None:  # also keeps this away from the RHU's own account and LOADTEST
        raise HTTPException(status_code=404, detail="Unknown barangay")
    password = accounts.set_password(request.app.state.db_path, barangay_id, give.password)
    return {"barangay_id": barangay_id, "name": barangay["name"], "password": password}


@router.get("/analytics", dependencies=[Depends(auth.require_rhu)])
def analytics_status(request: Request) -> dict:
    """The last analytics run (workers used, cases read, map/reduce time)."""
    result = request.app.state.analytics.last_result
    return {"last_run": result.to_dict() if result else None}


@router.post("/analytics/run", dependencies=[Depends(auth.require_rhu)])
async def run_analytics(request: Request) -> dict:
    """Run the analytics (and alert detection) now instead of waiting for the scheduler."""
    result = await asyncio.to_thread(request.app.state.analytics.run)
    alerts = await request.app.state.alerts.check_and_publish()
    return {"last_run": result.to_dict(), "alerts_sent": len(alerts)}


@router.get("/alerts", dependencies=[Depends(auth.require_rhu)])
def list_alerts(
    status: Literal["OPEN", "ACKNOWLEDGED", "RESOLVED"] | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    conn: sqlite3.Connection = Depends(get_conn),
) -> list[dict]:
    """Newest first, with delivery counts and the current count (corrections can lower it)."""
    rows = conn.execute(
        """
        SELECT a.*,
               COUNT(d.delivered_at) AS nodes_delivered,
               COUNT(d.acked_at) AS nodes_acknowledged,
               ws.case_count AS current_count
        FROM alerts a
        LEFT JOIN alert_deliveries d ON d.alert_id = a.alert_id
        LEFT JOIN weekly_stats ws ON ws.barangay_id = a.barangay_id AND ws.disease_code = a.disease_code
                                 AND ws.iso_year = a.iso_year AND ws.iso_week = a.iso_week
        WHERE ? IS NULL OR a.status = ?
        GROUP BY a.alert_id
        ORDER BY a.created_at DESC
        LIMIT ?
        """,
        (status, status, limit),
    ).fetchall()
    return [{**dict(r), "week_dates": week_dates(r["iso_year"], r["iso_week"])} for r in rows]


@router.post("/alerts/{alert_id}/status", dependencies=[Depends(auth.require_rhu)])
def update_alert_status(alert_id: str, update: AlertStatusUpdate, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """RHU staff: acknowledge or resolve an alert."""
    with conn:
        changed = conn.execute("UPDATE alerts SET status = ? WHERE alert_id = ?", (update.status, alert_id)).rowcount
    if not changed:
        raise HTTPException(status_code=404, detail="Unknown alert_id")
    return {"alert_id": alert_id, "status": update.status}


@router.get("/stats/summary")
def stats_summary(request: Request, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """Municipal totals per disease, this week (so far) vs last week. Shared with every BHS node."""
    analytics = request.app.state.analytics.last_result
    return {**stats.summary(conn, date.today()), "analyzed_at": analytics.finished_at if analytics else None}


@router.get("/stats/trend")
def stats_trend(
    disease: str | None = None,
    barangay_id: str | None = None,
    weeks: int = Query(default=26, ge=1, le=260),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """Weekly counts for charts; whole municipality and/or all diseases when not given."""
    if disease is not None and disease not in {d["disease_code"] for d in rules.diseases()}:
        raise HTTPException(status_code=404, detail="Unknown disease")
    if barangay_id is not None and barangay_id not in rules.barangays_by_id():
        raise HTTPException(status_code=404, detail="Unknown barangay_id")
    return {
        "disease": disease,
        "barangay_id": barangay_id,
        "weeks": stats.trend(conn, weeks, disease, barangay_id),
    }


@router.get("/stats/barangays")
def stats_barangays(
    week: str | None = Query(default=None, pattern=r"^\d{4}-W\d{2}$", description="e.g. 2026-W44; default: current week"),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """Every barangay × disease for one week, with levels (for the RHU heatmap table)."""
    if week is None:
        year, number = date.today().isocalendar()[:2]
    else:
        year, number = int(week[:4]), int(week[6:])
        try:
            date.fromisocalendar(year, number, 1)
        except ValueError:
            raise HTTPException(status_code=400, detail="No such ISO week") from None
    return stats.barangay_grid(conn, year, number)


@router.get("/stats/day")
def stats_day(
    day: date = Query(alias="date", description="e.g. 2026-11-17"),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """Every barangay's cases on one day, for the map's calendar."""
    if day > date.today():
        raise HTTPException(status_code=400, detail="That day hasn't happened yet")
    return stats.day_grid(conn, day)


@router.get("/stats/days")
def stats_days(
    month: str = Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$", description="e.g. 2026-11"),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """Cases per day in one month, so the calendar can mark days that have data."""
    return stats.month_days(conn, int(month[:4]), int(month[5:]))


@router.get("/monitor/stats", dependencies=[Depends(auth.require_rhu)])
def monitor_stats(pipeline: IngestPipeline = Depends(get_pipeline)) -> dict:
    return pipeline.snapshot()

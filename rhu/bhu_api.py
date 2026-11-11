"""A BHU's own records. Only that BHU's login can reach them; the RHU gets de-identified reports."""

import sqlite3
from collections.abc import Callable
from contextlib import closing
from functools import wraps
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from rhu import auth, db, rules
from rhu.bhu_store import BhuStores, InvalidRecord, OutboxRelay, RecordNotFound, station_id
from rhu.models import ClientFields, ConsultationCorrection, ConsultationFields, VoidRequest

router = APIRouter(prefix="/api/bhu/{barangay_id}", dependencies=[Depends(auth.require_own_records)])


def _guarded(handler: Callable) -> Callable:
    """Turns record errors into HTTP errors, with a message the BHU worker can read."""

    @wraps(handler)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if kwargs["barangay_id"] not in rules.barangays_by_id():
            raise HTTPException(status_code=404, detail="Unknown barangay.")
        try:
            return handler(*args, **kwargs)
        except RecordNotFound as error:
            raise HTTPException(status_code=404, detail=str(error)) from None
        except InvalidRecord as error:
            raise HTTPException(status_code=400, detail=str(error)) from None

    return wrapper


def _stores(request: Request) -> BhuStores:
    return request.app.state.bhu_stores


def _send_reports(request: Request, barangay_id: str) -> None:
    """Makes sure the barangay's station exists at the RHU, then wakes the relay."""
    with closing(db.connect(request.app.state.db_path)) as conn, conn:
        conn.execute(
            "INSERT OR IGNORE INTO nodes (node_id, barangay_id, registered_at) VALUES (?, ?, ?)",
            (station_id(barangay_id), barangay_id, db.utc_now()),
        )
    relay: OutboxRelay = request.app.state.outbox_relay
    relay.wake()


@router.get("/clients")
@_guarded
def list_clients(request: Request, barangay_id: str) -> list[dict]:
    return _stores(request).list_clients(barangay_id)


@router.get("/clients/{client_id}")
@_guarded
def get_client(request: Request, barangay_id: str, client_id: str) -> dict:
    return _stores(request).get_client(barangay_id, client_id)


@router.put("/clients/{client_id}")
@_guarded
def save_client(request: Request, barangay_id: str, client_id: UUID, fields: ClientFields) -> dict:
    client = _stores(request).save_client(barangay_id, str(client_id), fields.model_dump(mode="json"))
    _send_reports(request, barangay_id)  # correcting a birthdate or sex re-sends reports
    return client


@router.get("/consultations")
@_guarded
def list_consultations(request: Request, barangay_id: str, client_id: str | None = None) -> list[dict]:
    return _stores(request).list_consultations(barangay_id, client_id)


@router.get("/consultations/{consultation_id}")
@_guarded
def get_consultation(request: Request, barangay_id: str, consultation_id: str) -> dict:
    return _stores(request).get_consultation(barangay_id, consultation_id)


@router.put("/clients/{client_id}/consultations/{consultation_id}")
@_guarded
def save_consultation(request: Request, barangay_id: str, client_id: str, consultation_id: UUID, fields: ConsultationFields) -> dict:
    saved = _stores(request).save_consultation(barangay_id, client_id, str(consultation_id), fields.model_dump(mode="json"))
    _send_reports(request, barangay_id)
    return saved


@router.post("/consultations/{consultation_id}/correct")
@_guarded
def correct_consultation(request: Request, barangay_id: str, consultation_id: str, correction: ConsultationCorrection) -> dict:
    saved = _stores(request).correct_consultation(
        barangay_id,
        consultation_id,
        correction.fields.model_dump(mode="json"),
        correction.reason,
        [change.model_dump() for change in correction.changes],
    )
    _send_reports(request, barangay_id)
    return saved


@router.post("/consultations/{consultation_id}/void")
@_guarded
def void_consultation(request: Request, barangay_id: str, consultation_id: str, body: VoidRequest) -> dict:
    saved = _stores(request).void_consultation(barangay_id, consultation_id, body.reason)
    _send_reports(request, barangay_id)
    return saved


@router.get("/backup")
@_guarded
def export_backup(request: Request, barangay_id: str) -> dict:
    return _stores(request).export_backup(barangay_id)


@router.post("/backup")
@_guarded
def import_backup(request: Request, barangay_id: str, backup: dict = Body(...)) -> dict:
    try:
        counts = _stores(request).import_backup(barangay_id, backup)
    except (KeyError, TypeError, sqlite3.IntegrityError):
        raise InvalidRecord("This backup file is damaged.") from None
    _send_reports(request, barangay_id)
    return counts

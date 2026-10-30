"""Protocol messages (see docs/protocol.md)."""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from rhu.config import MAX_RECORDS_PER_BATCH, PROTOCOL_VERSION

BARANGAY_ID_PATTERN = r"^BRGY-\d{2}$"
NODE_ID_PATTERN = r"^BRGY-\d{2}-[A-Z0-9]{4}$"


def barangay_of(node_id: str) -> str:
    """'BRGY-08-K7QX' -> 'BRGY-08'. Registration guarantees the prefix is valid."""
    return node_id.rsplit("-", 1)[0]


class RegisterRequest(BaseModel):
    type: Literal["REGISTER"]
    node_id: str = Field(pattern=NODE_ID_PATTERN)
    barangay_id: str = Field(pattern=BARANGAY_ID_PATTERN)

    @model_validator(mode="after")
    def node_belongs_to_barangay(self) -> "RegisterRequest":
        if not self.node_id.startswith(f"{self.barangay_id}-"):
            raise ValueError("node_id must start with its barangay_id")
        return self


class RegisterResponse(BaseModel):
    type: Literal["REGISTERED"] = "REGISTERED"
    node_id: str
    barangay_id: str
    barangay: str


class CaseRecord(BaseModel):
    """One de-identified case. Structure is checked here; rules that need
    reference data (e.g. known disease codes) are checked by the validator threads."""

    record_id: UUID
    disease_code: str = Field(min_length=1, max_length=8)
    onset_date: date
    consult_date: date
    age_years: int = Field(ge=0, le=110)
    sex: Literal["M", "F"]
    purok: int | None = Field(default=None, ge=1, le=20)
    case_status: Literal["SUSPECTED", "CONFIRMED"]


class SubmitBatch(BaseModel):
    type: Literal["SUBMIT_BATCH"]
    protocol_version: Literal[PROTOCOL_VERSION]
    node_id: str = Field(pattern=NODE_ID_PATTERN)
    batch_id: UUID
    sent_at: datetime
    records: list[CaseRecord] = Field(min_length=1, max_length=MAX_RECORDS_PER_BATCH)
    drill: bool = False  # stress-test data: stored and counted, but left out of analytics and alerts


class AlertStatusUpdate(BaseModel):
    status: Literal["OPEN", "ACKNOWLEDGED", "RESOLVED"]

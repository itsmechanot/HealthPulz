"""Protocol messages (see docs/protocol.md)."""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from rhu.accounts import MIN_PASSWORD_LENGTH
from rhu.config import MAX_BURST_RECORDS_PER_DEVICE
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
    """One de-identified case. Rules that need reference data are checked by the validator threads."""

    record_id: UUID
    disease_code: str = Field(min_length=1, max_length=8)
    onset_date: date
    consult_date: date
    age_years: int = Field(ge=0, le=110)
    sex: Literal["M", "F"]
    purok: int | None = Field(default=None, ge=1, le=20)
    case_status: Literal["SUSPECTED", "CONFIRMED"]
    revision: int = Field(default=0, ge=0)  # a higher revision of a record_id replaces the stored one
    voided: bool = False  # entered by mistake: kept for audit, left out of the analytics


class SubmitBatch(BaseModel):
    type: Literal["SUBMIT_BATCH"]
    protocol_version: Literal[PROTOCOL_VERSION]
    node_id: str = Field(pattern=NODE_ID_PATTERN)
    batch_id: UUID
    sent_at: datetime
    records: list[CaseRecord] = Field(min_length=1, max_length=MAX_RECORDS_PER_BATCH)
    drill: bool = False  # stress-test data: stored and counted, but left out of analytics and alerts


class GivePassword(BaseModel):
    password: str | None = Field(default=None, min_length=MIN_PASSWORD_LENGTH, max_length=200)  # None = generate one


class BurstRequest(BaseModel):
    records_per_device: int = Field(ge=1, le=MAX_BURST_RECORDS_PER_DEVICE)


class AlertStatusUpdate(BaseModel):
    status: Literal["OPEN", "ACKNOWLEDGED", "RESOLVED"]


class _FormFields(BaseModel):
    """Fields from a web form: an empty input arrives as "" and means "not given"."""

    @field_validator("*", mode="before")
    @classmethod
    def blank_is_none(cls, value):
        return None if value == "" else value


class ClientFields(_FormFields):
    last_name: str = Field(min_length=1, max_length=100)
    first_name: str = Field(min_length=1, max_length=100)
    middle_name: str | None = Field(default=None, max_length=100)
    birthdate: date
    sex: Literal["M", "F"]
    civil_status: str | None = Field(default=None, max_length=30)
    purok: int = Field(ge=1, le=20)
    address: str | None = Field(default=None, max_length=200)
    contact: str | None = Field(default=None, max_length=30)
    philhealth: str | None = Field(default=None, max_length=30)


class ConsultationFields(_FormFields):
    visit_date: date
    onset_date: date | None = None
    chief_complaint: str = Field(min_length=1, max_length=500)
    temperature: float | None = Field(default=None, ge=30, le=45)
    bp_systolic: int | None = Field(default=None, ge=50, le=260)
    bp_diastolic: int | None = Field(default=None, ge=30, le=160)
    pulse: int | None = Field(default=None, ge=20, le=250)
    respiratory_rate: int | None = Field(default=None, ge=5, le=80)
    weight: float | None = Field(default=None, ge=0.5, le=300)
    height: float | None = Field(default=None, ge=30, le=250)
    findings: str | None = Field(default=None, max_length=2000)
    diagnosis_code: str = Field(min_length=1, max_length=8)
    diagnosis_text: str | None = Field(default=None, max_length=200)
    case_status: Literal["SUSPECTED", "CONFIRMED"] | None = None
    treatment: str | None = Field(default=None, max_length=2000)
    disposition: str | None = Field(default=None, max_length=50)
    follow_up_date: date | None = None
    attended_by: str | None = Field(default=None, max_length=100)


class FieldChange(BaseModel):
    field: str
    to: str
    # "from" is a Python keyword
    previous: str = Field(alias="from", serialization_alias="from")

    def model_dump(self, **kwargs) -> dict:
        return super().model_dump(by_alias=True, **kwargs)


class ConsultationCorrection(BaseModel):
    fields: ConsultationFields
    reason: str = Field(min_length=1, max_length=300)
    changes: list[FieldChange] = Field(min_length=1)


class VoidRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=300)

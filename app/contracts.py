"""Data contracts for every external interface (D1). Pydantic enforces schema,
type, range, category; the model validator enforces cross-field rules. The JSON
Schemas are exported to contracts/ by scripts/export_contracts.py."""
import re
from datetime import date
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

COMMENCEMENT = date(2025, 11, 21)


class EmploymentType(str, Enum):
    permanent = "permanent"
    fixed_term = "fixed_term"
    probationary = "probationary"
    contract_labour = "contract_labour"


class EstablishmentType(str, Enum):
    factory = "factory"
    shop_establishment = "shop_establishment"
    it_services = "it_services"
    mine = "mine"
    plantation = "plantation"
    other = "other"


class SeparationReason(str, Enum):
    ongoing = "ongoing"
    contract_expiry = "contract_expiry"
    resignation = "resignation"
    retirement = "retirement"
    superannuation = "superannuation"
    death = "death"
    disablement = "disablement"
    termination = "termination"


PII_PATTERNS = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "email address"),
    (re.compile(r"(?<!\d)(?:\+?91[- ]?)?[6-9]\d{9}(?!\d)"), "phone number"),
    (re.compile(r"(?<!\d)\d{4}\s?\d{4}\s?\d{4}(?!\d)"), "12-digit id number"),
]


def _no_pii(v: Optional[str]):
    if v is None:
        return v
    for pat, label in PII_PATTERNS:
        if pat.search(v):
            raise ValueError(f"free text appears to contain a {label}; only synthetic data is allowed")
    return v


class Establishment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    employee_count: Optional[int] = Field(None, ge=1, le=1_000_000, description="Employees in the establishment")
    establishment_type: Optional[EstablishmentType] = None
    has_internal_committee: Optional[bool] = None


class Employment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    employment_type: Optional[EmploymentType] = None
    start_date: Optional[date] = Field(None, ge=date(1950, 1, 1), le=date(2100, 1, 1))
    end_date: Optional[date] = Field(None, ge=date(1950, 1, 1), le=date(2100, 1, 1))
    contract_months: Optional[int] = Field(None, ge=1, le=600)
    years_of_service: Optional[float] = Field(None, ge=0, le=60)
    days_worked_last_12_months: Optional[int] = Field(None, ge=0, le=366)
    separation_reason: Optional[SeparationReason] = None


class Options(BaseModel):
    model_config = ConfigDict(extra="forbid")
    no_cache: bool = False
    prompt_version: Optional[str] = Field(None, pattern=r"^v\d{1,2}$")


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_id: str = Field(..., pattern=r"^[a-z0-9_-]{3,64}$")
    question: str = Field(..., min_length=10, max_length=2000)
    establishment: Optional[Establishment] = None
    employment: Optional[Employment] = None
    as_of_date: Optional[date] = Field(None, ge=date(2020, 1, 1), le=date(2100, 1, 1))
    notes: Optional[str] = Field(None, max_length=500)
    options: Optional[Options] = None

    @field_validator("question", "notes")
    @classmethod
    def pii(cls, v):
        return _no_pii(v)

    @model_validator(mode="after")
    def cross_field(self):
        e = self.employment
        if e is None:
            return self
        if e.start_date and e.end_date and e.end_date < e.start_date:
            raise ValueError("employment.end_date must not be before employment.start_date")
        if e.start_date and self.as_of_date and self.as_of_date < e.start_date:
            raise ValueError("as_of_date must not be before employment.start_date")
        if e.employment_type == EmploymentType.fixed_term and not (e.contract_months or (e.start_date and e.end_date)):
            raise ValueError("fixed_term employment requires contract_months or both start_date and end_date")
        if e.separation_reason == SeparationReason.contract_expiry and e.employment_type not in (None, EmploymentType.fixed_term):
            raise ValueError("separation_reason contract_expiry is only valid for fixed_term employment")
        if e.start_date and e.end_date and e.contract_months:
            span = (e.end_date.year - e.start_date.year) * 12 + (e.end_date.month - e.start_date.month)
            if abs(span - e.contract_months) > 1:
                raise ValueError(f"contract_months ({e.contract_months}) disagrees with start_date/end_date span ({span} months)")
        if e.start_date and e.years_of_service is not None:
            ref = e.end_date or self.as_of_date or date.today()
            max_years = max((ref - e.start_date).days / 365.25, 0) + 0.1
            if e.years_of_service > max_years:
                raise ValueError("years_of_service exceeds the time between start_date and end_date/as_of_date")
        return self


class Citation(BaseModel):
    chunk_id: str
    label: str
    status: str
    text: str


class Assertion(BaseModel):
    text: str
    citations: List[Citation]


class RunResult(BaseModel):
    run_id: str
    status: str
    route: Optional[str] = None
    route_basis: Optional[str] = None
    summary: Optional[str] = None
    assertions: List[Assertion] = []
    escalation: Optional[dict] = None
    missing_fields: List[str] = []
    prompt_version: Optional[str] = None
    index_version: Optional[str] = None
    degraded: bool = False
    cached: bool = False
    verification: Optional[dict] = None
    disclaimer: str = "Advisory only; not a legal opinion. A qualified professional decides."


class FeedbackIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str = Field(..., min_length=8, max_length=64)
    rating: str = Field(..., pattern=r"^(satisfactory|unsatisfactory)$")
    reason: str = Field(..., min_length=3, max_length=500)

    @field_validator("reason")
    @classmethod
    def pii(cls, v):
        return _no_pii(v)


def field_errors(exc_errors) -> list:
    out = []
    for e in exc_errors:
        loc = ".".join(str(x) for x in e.get("loc", []) if x != "body")
        out.append({"field": loc or "(request)", "error": e.get("msg", ""), "type": e.get("type", "")})
    return out

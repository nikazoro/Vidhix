from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CaseStepSubmit(BaseModel):
    step_number: int = Field(..., ge=1)
    action: str = Field(..., min_length=5, max_length=2000)
    outcome: str | None = Field(None, max_length=2000)
    deadline_days: int | None = Field(None, ge=0)
    was_effective: bool | None = None
    documents_used: list[str] = Field(default_factory=list)


class CaseSubmit(BaseModel):
    jurisdiction: str = Field(..., min_length=2, max_length=10)
    legal_domain: str = Field(..., min_length=2, max_length=50)
    case_type: str | None = Field(None, max_length=100)
    situation_summary: str = Field(..., min_length=50, max_length=5000)
    key_facts: list[str] = Field(default_factory=list)
    timeline: list[dict] = Field(default_factory=list)
    steps: list[CaseStepSubmit] = Field(default_factory=list)
    outcome: str = Field(...)
    outcome_summary: str | None = Field(None, max_length=2000)
    outcome_date: date | None = None
    settlement_amount: Decimal | None = None
    source_type: str | None = Field(None, max_length=50)
    consent_to_anonymize: bool = Field(..., description="Must be True to submit")

    @field_validator("outcome")
    @classmethod
    def validate_outcome(cls, v: str) -> str:
        allowed = {"won", "settled", "lost", "dismissed", "ongoing"}
        if v not in allowed:
            raise ValueError(f"outcome must be one of {allowed}")
        return v

    @field_validator("consent_to_anonymize")
    @classmethod
    def consent_required(cls, v: bool) -> bool:
        if not v:
            raise ValueError("Consent to anonymize is required to submit a case.")
        return v


class CaseStepResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    step_number: int
    action: str
    outcome: str | None
    deadline_days: int | None
    was_effective: bool | None
    documents_used: list[str]


class CaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    jurisdiction: str
    legal_domain: str
    case_type: str | None
    situation_summary: str
    outcome: str
    outcome_summary: str | None
    outcome_date: date | None
    quality_score: float
    anonymized: bool
    verified: bool
    created_at: datetime
    steps: list[CaseStepResponse] = Field(default_factory=list)


class CaseSubmitResponse(BaseModel):
    case_id: str
    status: str
    message: str


class SimilarCasesRequest(BaseModel):
    situation: str = Field(..., min_length=10, max_length=2000)
    jurisdiction: str = Field(..., min_length=2, max_length=10)
    legal_domain: str | None = None
    top_k: int = Field(default=3, ge=1, le=10)
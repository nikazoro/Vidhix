from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SessionCreate(BaseModel):
    jurisdiction: str | None = Field(None, max_length=10)
    legal_domain: str | None = Field(None, max_length=50)
    session_metadata: dict[str, Any] = Field(default_factory=dict)


class SessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    created_at: datetime
    jurisdiction: str | None
    legal_domain: str | None
    is_active: bool


class MessageCreate(BaseModel):
    content: str = Field(..., min_length=1, max_length=8000)
    document_ids: list[str] = Field(default_factory=list)


class SourceReference(BaseModel):
    source_id: str
    title: str
    jurisdiction: str | None
    legal_domain: str | None
    chunk_text: str
    score: float
    document_type: str | None = None
    source_authority: str | None = None


class EscalationInfo(BaseModel):
    escalation_needed: bool
    severity: str  # LOW | MEDIUM | HIGH
    reason: str
    escalation_message: str
    resources: list[str] = Field(default_factory=list)


class WorkflowPositionInfo(BaseModel):
    procedure_id: str
    procedure_title: str
    current_step_id: str
    current_step_title: str
    current_phase: str
    completed_steps: list[str] = Field(default_factory=list)


class SimilarCaseInfo(BaseModel):
    case_id: str
    situation_summary: str
    steps_taken: str  # formatted numbered list
    outcome: str
    outcome_summary: str | None
    similarity_score: float
    outcome_badge: str


class MessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    created_at: datetime
    session_id: str
    role: str
    content: str
    confidence_score: float | None
    escalation_triggered: bool
    sources: list[SourceReference] = Field(default_factory=list)
    similar_cases: list[SimilarCaseInfo] = Field(default_factory=list)
    escalation_result: EscalationInfo | None = None
    workflow_position: WorkflowPositionInfo | None = None


class ChatHistoryResponse(BaseModel):
    session_id: str
    messages: list[MessageResponse]
    total: int


class StreamEvent(BaseModel):
    type: str  # thinking | retrieval_complete | escalation_check | token | complete | error
    content: Any = None
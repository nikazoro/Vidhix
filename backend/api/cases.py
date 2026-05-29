import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.query_analyzer import QueryAnalysis, QueryEntities
from core.case_retriever import retrieve_similar_cases
from database import get_db
from ingestion.tasks import ingest_case_task
from models.case import Case
from models.case_step import CaseStep
from schemas.case import (
    CaseResponse,
    CaseSubmit,
    CaseSubmitResponse,
    SimilarCasesRequest,
)
from schemas.chat import SimilarCaseInfo
from celery import Task
from typing import cast

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/cases", tags=["cases"])


# ---------------------------------------------------------------------------
# POST /cases — submit a case
# ---------------------------------------------------------------------------

@router.post(
    "",
    response_model=CaseSubmitResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit a case",
    description="Submit an anonymized case to the case learning corpus. Consent to anonymize is required.",
)
async def submit_case(
    body: CaseSubmit,
    db: AsyncSession = Depends(get_db),
) -> CaseSubmitResponse:
    case = Case(
        jurisdiction=body.jurisdiction,
        legal_domain=body.legal_domain,
        case_type=body.case_type,
        situation_summary=body.situation_summary,
        key_facts=body.key_facts,
        timeline=body.timeline,
        steps_taken=[s.model_dump() for s in body.steps],
        outcome=body.outcome,
        outcome_summary=body.outcome_summary,
        outcome_date=body.outcome_date,
        settlement_amount=body.settlement_amount,
        source_type=body.source_type or "user_submission",
        anonymized=False,  # will be set True after anonymization task
        verified=False,
        quality_score=0.5,
    )
    db.add(case)
    await db.flush()
    await db.refresh(case)

    # Persist steps
    for step_data in body.steps:
        step = CaseStep(
            case_id=case.id,
            step_number=step_data.step_number,
            action=step_data.action,
            outcome=step_data.outcome,
            deadline_days=step_data.deadline_days,
            was_effective=step_data.was_effective,
            documents_used=step_data.documents_used,
        )
        db.add(step)

    await db.flush()
    await db.commit()
    # Queue anonymization + embedding task
    cast(Task, ingest_case_task).delay(case.id)

    log.info(
        "case_submitted",
        case_id=case.id,
        jurisdiction=body.jurisdiction,
        legal_domain=body.legal_domain,
        outcome=body.outcome,
    )

    return CaseSubmitResponse(
        case_id=case.id,
        status="processing",
        message=(
            "Your case has been received. It will be anonymized (all names, locations, "
            "and identifying information removed) and added to the knowledge base to help others."
        ),
    )


# ---------------------------------------------------------------------------
# GET /cases/similar — find similar cases
# ---------------------------------------------------------------------------

@router.post(
    "/similar",
    response_model=list[SimilarCaseInfo],
    status_code=status.HTTP_200_OK,
    summary="Find similar cases",
    description="Search for similar past cases given a situation description.",
)
async def find_similar_cases(
    body: SimilarCasesRequest,
) -> list[SimilarCaseInfo]:
    # Build a minimal QueryAnalysis for the retriever
    analysis = QueryAnalysis(
        intent="rights_inquiry",
        jurisdiction=body.jurisdiction,
        legal_domain=body.legal_domain or "other",
        procedure_position="unknown",
        entities=QueryEntities(),
        is_multi_hop=False,
        sub_queries=[],
        expanded_query=body.situation,
        original_query=body.situation,
    )
    cases = await retrieve_similar_cases(analysis, top_k=body.top_k)
    return [
        SimilarCaseInfo(
            case_id=c.case_id,
            situation_summary=c.situation_summary,
            steps_taken=c.steps_taken,
            outcome=c.outcome,
            outcome_summary=c.outcome_summary,
            similarity_score=round(c.similarity_score, 4),
            outcome_badge=c.outcome_badge,
        )
        for c in cases
    ]


# ---------------------------------------------------------------------------
# GET /cases/{case_id}
# ---------------------------------------------------------------------------

@router.get(
    "/{case_id}",
    response_model=CaseResponse,
    status_code=status.HTTP_200_OK,
    summary="Get case by ID",
    description="Retrieve a single anonymized case record.",
)
async def get_case(
    case_id: str,
    db: AsyncSession = Depends(get_db),
) -> CaseResponse:
    result = await db.execute(
        select(Case).where(Case.id == case_id, Case.anonymized == True)
    )
    case = result.scalar_one_or_none()
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found or not yet anonymized.")
    return CaseResponse.model_validate(case)
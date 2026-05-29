import structlog
from fastapi import APIRouter, HTTPException, Query, status

from core.workflow_engine import get_workflow_engine

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/workflows", tags=["workflows"])


@router.get(
    "",
    status_code=status.HTTP_200_OK,
    summary="List procedures",
    description="List all available legal procedure workflows, optionally filtered by jurisdiction or domain.",
)
async def list_procedures(
    jurisdiction: str | None = Query(None, description="Filter by jurisdiction (e.g. CA, federal)"),
    legal_domain: str | None = Query(None, description="Filter by legal domain (e.g. tenant-rights)"),
) -> list[dict]:
    engine = get_workflow_engine()
    procedures = engine.list_procedures()

    if jurisdiction:
        procedures = [p for p in procedures if p["jurisdiction"] == jurisdiction]
    if legal_domain:
        procedures = [p for p in procedures if p["legal_domain"] == legal_domain]

    return procedures


@router.get(
    "/{procedure_id}",
    status_code=status.HTTP_200_OK,
    summary="Get procedure detail",
    description="Get full procedure detail including all steps and edges.",
)
async def get_procedure(procedure_id: str) -> dict:
    engine = get_workflow_engine()
    try:
        proc = engine.load_procedure(procedure_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Procedure {procedure_id!r} not found.")

    return {
        "id": proc.id,
        "title": proc.title,
        "jurisdiction": proc.jurisdiction,
        "legal_domain": proc.legal_domain,
        "description": proc.description,
        "steps": [
            {
                "id": s.id,
                "step_number": s.step_number,
                "phase": s.phase,
                "title": s.title,
                "description": s.description,
                "actions": s.actions,
                "deadlines": {
                    "response_days": s.deadlines.response_days,
                    "deadline_type": s.deadlines.deadline_type,
                    "excludes_weekends": s.deadlines.excludes_weekends,
                    "notes": s.deadlines.notes,
                },
                "forms": s.forms,
                "agency": s.agency,
                "self_help_possible": s.self_help_possible,
                "escalation_triggers": s.escalation_triggers,
            }
            for s in proc.steps_ordered
        ],
        "edges": [
            {
                "from": e.from_step,
                "to": e.to_step,
                "condition": e.condition,
            }
            for e in proc.edges
        ],
    }


@router.get(
    "/{procedure_id}/steps/{step_id}",
    status_code=status.HTTP_200_OK,
    summary="Get single step",
    description="Get detail for a single step including next steps.",
)
async def get_step(procedure_id: str, step_id: str) -> dict:
    engine = get_workflow_engine()
    try:
        step = engine.get_step(procedure_id, step_id)
        next_steps = engine.get_next_steps(procedure_id, step_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    return {
        "id": step.id,
        "step_number": step.step_number,
        "phase": step.phase,
        "title": step.title,
        "description": step.description,
        "actions": step.actions,
        "deadlines": {
            "response_days": step.deadlines.response_days,
            "deadline_type": step.deadlines.deadline_type,
            "excludes_weekends": step.deadlines.excludes_weekends,
            "notes": step.deadlines.notes,
        },
        "forms": step.forms,
        "agency": step.agency,
        "self_help_possible": step.self_help_possible,
        "escalation_triggers": step.escalation_triggers,
        "next_steps": [{"id": s.id, "title": s.title, "phase": s.phase} for s in next_steps],
    }
# import structlog
# from fastapi import APIRouter, Depends, status
# from sqlalchemy.ext.asyncio import AsyncSession

# from database import get_db
# from ingestion.tasks import update_case_quality_task
# from models.response_feedback import ResponseFeedback
# from schemas.feedback import FeedbackResponse, FeedbackSubmit

# log = structlog.get_logger(__name__)
# router = APIRouter(prefix="/feedback", tags=["feedback"])


# @router.post(
#     "",
#     response_model=FeedbackResponse,
#     status_code=status.HTTP_201_CREATED,
#     summary="Submit feedback",
#     description="Submit a 1-5 star rating for an assistant message. Triggers quality score updates for referenced cases.",
# )
# async def submit_feedback(
#     body: FeedbackSubmit,
#     db: AsyncSession = Depends(get_db),
# ) -> FeedbackResponse:
#     feedback = ResponseFeedback(
#         session_id=body.session_id,
#         message_id=body.message_id,
#         rating=body.rating,
#         feedback_type=body.feedback_type,
#         contributed_case_ids=body.contributed_case_ids,
#         contributed_source_ids=body.contributed_source_ids,
#     )
#     db.add(feedback)
#     await db.flush()
#     await db.refresh(feedback)
#     # Queue case quality updates if cases were contributed
#     if body.contributed_case_ids:
#         update_case_quality_task.delay(body.contributed_case_ids, float(body.rating))

#     log.info(
#         "feedback_submitted",
#         feedback_id=feedback.id,
#         message_id=body.message_id,
#         rating=body.rating,
#         cases_updated=len(body.contributed_case_ids),
#     )

#     return FeedbackResponse(
#         id=feedback.id,
#         message_id=feedback.message_id,
#         rating=feedback.rating,
#         feedback_type=feedback.feedback_type,
#         success=True,
#     )

import structlog
from typing import cast

from celery import Task
from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from ingestion.tasks import update_case_quality_task
from models.response_feedback import ResponseFeedback
from schemas.feedback import FeedbackResponse, FeedbackSubmit

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/feedback", tags=["feedback"])


@router.post(
    "",
    response_model=FeedbackResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit feedback",
    description="Submit a 1-5 star rating for an assistant message. Triggers quality score updates for referenced cases.",
)
async def submit_feedback(
    body: FeedbackSubmit,
    db: AsyncSession = Depends(get_db),
) -> FeedbackResponse:

    feedback = ResponseFeedback(
        session_id=body.session_id,
        message_id=body.message_id,
        rating=body.rating,
        feedback_type=body.feedback_type,
        contributed_case_ids=body.contributed_case_ids,
        contributed_source_ids=body.contributed_source_ids,
    )

    db.add(feedback)

    await db.flush()
    await db.refresh(feedback)

    # Queue case quality updates if cases were contributed
    if body.contributed_case_ids:
        cast(Task, update_case_quality_task).delay(
            body.contributed_case_ids,
            float(body.rating),
        )

    log.info(
        "feedback_submitted",
        feedback_id=feedback.id,
        message_id=body.message_id,
        rating=body.rating,
        cases_updated=len(body.contributed_case_ids),
    )

    return FeedbackResponse(
        id=feedback.id,
        message_id=feedback.message_id,
        rating=feedback.rating,
        feedback_type=feedback.feedback_type,
        success=True,
    )
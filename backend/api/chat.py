import json
from collections.abc import AsyncGenerator

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.rag_pipeline import RAGState, run_pipeline_streaming
from database import get_db
from models.message import Message
from models.session import Session
from schemas.chat import (
    ChatHistoryResponse,
    MessageCreate,
    MessageResponse,
    SessionCreate,
    SessionResponse,
)

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/chat", tags=["chat"])


# ---------------------------------------------------------------------------
# POST /chat/sessions — create a new session
# ---------------------------------------------------------------------------

@router.post(
    "/sessions",
    response_model=SessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create chat session",
    description="Create a new conversation session with optional jurisdiction and legal domain.",
)
async def create_session(
    body: SessionCreate,
    db: AsyncSession = Depends(get_db),
) -> SessionResponse:
    session = Session(
        jurisdiction=body.jurisdiction,
        legal_domain=body.legal_domain,
        session_metadata=body.session_metadata,
    )
    db.add(session)
    await db.flush()
    await db.refresh(session)
    log.info("session_created", session_id=session.id, jurisdiction=session.jurisdiction)
    return SessionResponse.model_validate(session)


# ---------------------------------------------------------------------------
# POST /chat/sessions/{session_id}/messages — send message (SSE stream)
# ---------------------------------------------------------------------------

@router.post(
    "/sessions/{session_id}/messages",
    summary="Send message (streaming)",
    description="Run the full RAG pipeline and stream the response as Server-Sent Events.",
    status_code=status.HTTP_200_OK,
)
async def send_message(
    session_id: str,
    body: MessageCreate,
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    # Validate session exists
    result = await db.execute(select(Session).where(Session.id == session_id, Session.is_active == True))
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found or inactive.")

    # Load history
    hist_result = await db.execute(
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.created_at.asc())
    )
    history_rows = hist_result.scalars().all()
    session_history = [{"role": m.role, "content": m.content} for m in history_rows]

    # Persist user message
    user_msg = Message(
        session_id=session_id,
        role="user",
        content=body.content,
    )
    db.add(user_msg)
    await db.flush()
    await db.refresh(user_msg)

    initial_state: RAGState = {
        "session_id": session_id,
        "user_message": body.content,
        "session_history": session_history,
        "jurisdiction": session.jurisdiction or "federal",
        "query_analysis": None,
        "workflow_position": None,
        "retrieval_result": None,
        "similar_cases": [],
        "escalation_result": None,
        "context": None,
        "response": None,
        "sources": [],
        "confidence_score": 0.5,
        "error": None,
    }

    async def _event_stream() -> AsyncGenerator[str, None]:
        final_response: str = ""
        final_metadata: dict = {}
        escalation_triggered = False

        async for event in run_pipeline_streaming(initial_state):
            event_type = event.get("type", "token")
            payload = json.dumps({"type": event_type, "content": event.get("content")})
            yield f"data: {payload}\n\n"

            if event_type == "complete":
                content = event.get("content", {})
                final_response = content.get("response", "")
                final_metadata = content
                esc = content.get("escalation_result") or {}
                escalation_triggered = esc.get("escalation_needed", False)

            if event_type == "error":
                break

        # Persist assistant message after stream completes
        if final_response:
            try:
                async with db.begin_nested():
                    assistant_msg = Message(
                        session_id=session_id,
                        role="assistant",
                        content=final_response,
                        retrieval_metadata={
                            "sources": final_metadata.get("sources", []),
                            "similar_cases": final_metadata.get("similar_cases", []),
                            "workflow_position": final_metadata.get("workflow_position"),
                        },
                        confidence_score=final_metadata.get("confidence_score", 0.5),
                        escalation_triggered=escalation_triggered,
                    )
                    db.add(assistant_msg)
                await db.commit()
                log.info(
                    "assistant_message_saved",
                    session_id=session_id,
                    confidence=final_metadata.get("confidence_score"),
                    escalation=escalation_triggered,
                )
            except Exception as exc:
                log.error("assistant_message_save_failed", error=str(exc))

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ---------------------------------------------------------------------------
# GET /chat/sessions/{session_id}/history
# ---------------------------------------------------------------------------

@router.get(
    "/sessions/{session_id}/history",
    response_model=ChatHistoryResponse,
    summary="Get session history",
    description="Retrieve all messages in a session with metadata.",
)
async def get_history(
    session_id: str,
    db: AsyncSession = Depends(get_db),
) -> ChatHistoryResponse:
    result = await db.execute(select(Session).where(Session.id == session_id))
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found.")

    msg_result = await db.execute(
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.created_at.asc())
    )
    messages = msg_result.scalars().all()

    return ChatHistoryResponse(
        session_id=session_id,
        messages=[MessageResponse.model_validate(m) for m in messages],
        total=len(messages),
    )


# ---------------------------------------------------------------------------
# DELETE /chat/sessions/{session_id}
# ---------------------------------------------------------------------------

@router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete session",
    description="Soft-delete a session and mark it as inactive.",
)
async def delete_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
) -> None:
    result = await db.execute(select(Session).where(Session.id == session_id))
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    session.is_active = False
    log.info("session_deleted", session_id=session_id)
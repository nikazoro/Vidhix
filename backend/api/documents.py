from pathlib import Path
from uuid import uuid4

import aiofiles
import structlog
from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from celery import Task
from typing import cast
from datetime import datetime, timezone
from config import get_settings
from database import get_db
from ingestion.tasks import ingest_document_task
from models.document import Document
from schemas.document import DocumentAnalysis, DocumentResponse, DocumentUploadResponse

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/documents", tags=["documents"])
settings = get_settings()

_ALLOWED_MIME_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "image/png",
    "image/jpeg",
}


# ---------------------------------------------------------------------------
# POST /documents/upload
# ---------------------------------------------------------------------------

@router.post(
    "/upload",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload document",
    description="Upload a legal document (PDF, DOCX, PNG, JPEG) for OCR and analysis.",
)
async def upload_document(
    file: UploadFile,
    session_id: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> DocumentUploadResponse:
    # Validate MIME type
    if file.content_type not in _ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type: {file.content_type!r}. Allowed: PDF, DOCX, PNG, JPEG.",
        )

    # Read file content and validate size
    content = await file.read()
    size_bytes = len(content)
    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    if size_bytes > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File size {size_bytes / 1024 / 1024:.1f} MB exceeds limit of {settings.max_upload_size_mb} MB.",
        )

    # Save to local storage
    doc_id = str(uuid4())
    ext_map = {
        "application/pdf": ".pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        "image/png": ".png",
        "image/jpeg": ".jpg",
    }
    ext = ext_map.get(file.content_type, "")
    storage_path = settings.upload_path / f"{doc_id}{ext}"

    async with aiofiles.open(storage_path, "wb") as f:
        await f.write(content)

    # Create DB record
    doc = Document(
        id=doc_id,
        session_id=session_id,
        original_filename=file.filename or "unknown",
        storage_path=str(storage_path),
        mime_type=file.content_type,
        size_bytes=size_bytes,
        ocr_status="pending",
    )
    db.add(doc)
    await db.flush()
    await db.refresh(doc)
    await db.commit()
    # Queue Celery task
    cast(Task, ingest_document_task).delay(doc_id)

    log.info(
        "document_uploaded",
        document_id=doc_id,
        filename=file.filename,
        size_bytes=size_bytes,
        mime_type=file.content_type,
    )

    return DocumentUploadResponse(
        document_id=doc_id,
        status="processing",
        polling_url=f"/api/v1/documents/{doc_id}/analysis",
        original_filename=file.filename or "unknown",
        size_bytes=size_bytes,
    )


# ---------------------------------------------------------------------------
# GET /documents/{document_id}/analysis
# ---------------------------------------------------------------------------

@router.get(
    "/{document_id}/analysis",
    response_model=DocumentAnalysis,
    summary="Get document analysis",
    description="Poll for document analysis result. Returns status 'processing' until complete.",
)
async def get_document_analysis(
    document_id: str,
    db: AsyncSession = Depends(get_db),
) -> DocumentAnalysis:
    result = await db.execute(
        select(Document).where(Document.id == document_id, Document.deleted_at == None)
    )
    doc = result.scalar_one_or_none()
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found.")

    entities = doc.extracted_entities or {}

    return DocumentAnalysis(
        document_id=doc.id,
        original_filename=doc.original_filename,
        ocr_status=doc.ocr_status,
        document_type=doc.document_type,
        jurisdiction=entities.get("jurisdiction"),
        legal_domain=entities.get("legal_domain"),
        key_dates=[{"date": d} for d in entities.get("dates", [])],
        parties=entities.get("parties", []),
        amounts=[{"amount": a} for a in entities.get("amounts", [])],
        extracted_entities=entities,
        summary=None,
        created_at=cast(datetime, doc.created_at),
    )


# ---------------------------------------------------------------------------
# DELETE /documents/{document_id}
# ---------------------------------------------------------------------------

@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete document",
    description="Soft-delete a document and remove from storage.",
)
async def delete_document(
    document_id: str,
    db: AsyncSession = Depends(get_db),
) -> None:
    from datetime import datetime, timezone

    result = await db.execute(
        select(Document).where(Document.id == document_id, Document.deleted_at == None)
    )
    doc = result.scalar_one_or_none()
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found.")

    # Remove from storage
    storage_path = Path(doc.storage_path)
    if storage_path.exists():
        storage_path.unlink(missing_ok=True)

    doc.deleted_at = datetime.now(timezone.utc)
    await db.commit()
    log.info("document_deleted", document_id=document_id)
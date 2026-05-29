"""
Celery async tasks for document ingestion, case ingestion,
quality score updates, and BM25 index rebuilds.
"""
import asyncio
from pathlib import Path

import structlog
from celery import Celery

from config import get_settings

log = structlog.get_logger(__name__)
settings = get_settings()

celery_app = Celery(
    "lexara",
    broker=settings.redis_url,
    backend=settings.redis_url,
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)


# ---------------------------------------------------------------------------
# Helper: run async code from sync Celery task
# ---------------------------------------------------------------------------

def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Task 1: Ingest a user-uploaded document
# ---------------------------------------------------------------------------

@celery_app.task(bind=True, max_retries=3, default_retry_delay=10)
def ingest_document_task(self, document_id: str) -> dict:
    """
    Full pipeline for a user-uploaded Document:
      OCR → clean → classify → extract entities → chunk → embed → Qdrant upsert
      → BM25 update → update DB status
    """
    async def _run_pipeline():
        from database import AsyncSessionLocal
        from models.document import Document
        from ingestion.ocr import extract_text_from_file
        from ingestion.chunker import chunk_document
        from ingestion.classifier import classify_document
        from ingestion.entity_extractor import extract_entities
        from ingestion.embedder import embed_and_store_chunks
        from storage.bm25_index import get_bm25_index
        from sqlalchemy import select

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Document).where(Document.id == document_id)
            )
            doc = result.scalar_one_or_none()
            if doc is None:
                log.error("ingest_document_not_found", document_id=document_id)
                return {"status": "error", "reason": "document not found"}

            doc.ocr_status = "processing"
            await session.commit()

            file_path = Path(doc.storage_path)
            try:
                # OCR
                text = await extract_text_from_file(file_path, doc.mime_type)

                # Classify
                classification = classify_document(text)

                # Entity extraction
                entities = extract_entities(text)

                # Update document record
                doc.extracted_text = text
                doc.document_type = classification.get("document_type") or "unknown"
                doc.extracted_entities = {**classification, **entities}

                # Chunk
                doc_metadata = {
                    "title": doc.original_filename,
                    "jurisdiction": classification.get("jurisdiction", "unknown"),
                    "legal_domain": classification.get("legal_domain", "other"),
                    "document_type": classification.get("document_type", "unknown"),
                    "source_authority": "user_upload",
                    "procedure_id": None,
                    "procedure_phase": None,
                    "step_number": None,
                }
                chunks = chunk_document(text, doc_metadata)

                # Embed + upsert Qdrant
                count = await embed_and_store_chunks(
                    chunks,
                    corpus_document_id=document_id,
                    doc_metadata=doc_metadata,
                )

                # Update BM25
                bm25 = get_bm25_index()
                bm25.add_documents(
                    [{"chunk_id": c.chunk_id, "text": c.text} for c in chunks]
                )

                doc.ocr_status = "completed"
                await session.commit()

                log.info(
                    "ingest_document_complete",
                    document_id=document_id,
                    chunks=count,
                )
                return {"status": "completed", "chunks": count}

            except Exception as exc:
                doc.ocr_status = "failed"
                await session.commit()
                log.error("ingest_document_failed", document_id=document_id, error=str(exc))
                raise

    try:
        return _run(_run_pipeline())
    except Exception as exc:
        raise self.retry(exc=exc)


# ---------------------------------------------------------------------------
# Task 2: Ingest a submitted case
# ---------------------------------------------------------------------------

@celery_app.task
def ingest_case_task(case_id: str) -> dict:
    """
    Anonymize case summary → embed → upsert to legal_cases Qdrant collection.
    """
    async def _run_pipeline():
        from database import AsyncSessionLocal
        from models.case import Case
        from ingestion.anonymizer import anonymize_text
        from core.embeddings import embed_text
        from storage.qdrant_client import CasePayload, upsert_case
        from sqlalchemy import select

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Case).where(Case.id == case_id)
            )
            case = result.scalar_one_or_none()
            if case is None:
                log.error("ingest_case_not_found", case_id=case_id)
                return {"status": "error"}

            # Anonymize
            anon_summary = anonymize_text(case.situation_summary)
            case.situation_summary = anon_summary
            case.anonymized = True
            await session.commit()

            # Embed
            vector = await embed_text(anon_summary)

            # Upsert
            payload = CasePayload(
                case_id=case.id,
                jurisdiction=case.jurisdiction,
                legal_domain=case.legal_domain,
                case_type=case.case_type,
                outcome=case.outcome,
                quality_score=case.quality_score,
                situation_summary=anon_summary,
                vector=vector,
            )
            await upsert_case(payload)

            log.info("ingest_case_complete", case_id=case_id)
            return {"status": "completed"}

    return _run(_run_pipeline())


# ---------------------------------------------------------------------------
# Task 3: Update case quality scores via EMA
# ---------------------------------------------------------------------------

@celery_app.task
def update_case_quality_task(case_ids: list[str], rating: float) -> dict:
    """
    Exponential moving average quality score update:
      new_score = 0.9 * old_score + 0.1 * normalised_rating
    Rating is 1-5 stars → normalised to 0.0-1.0.
    """
    async def _run_update():
        from database import AsyncSessionLocal
        from models.case import Case
        from sqlalchemy import select

        normalised = (rating - 1) / 4.0  # 1→0.0, 5→1.0

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Case).where(Case.id.in_(case_ids))
            )
            cases = result.scalars().all()
            updated = 0
            for case in cases:
                case.quality_score = 0.9 * case.quality_score + 0.1 * normalised
                updated += 1
            await session.commit()

        log.info(
            "case_quality_updated",
            case_ids=case_ids,
            normalised_rating=normalised,
            updated=updated,
        )
        return {"status": "completed", "updated": updated}

    return _run(_run_update())


# ---------------------------------------------------------------------------
# Task 4: Rebuild BM25 index from PostgreSQL corpus
# ---------------------------------------------------------------------------

@celery_app.task
def rebuild_bm25_index_task() -> dict:
    """
    Rebuild the BM25 index from scratch using all completed corpus documents.
    Fetches chunk texts from Qdrant since that is the source of truth.
    """
    async def _run_rebuild():
        from storage.qdrant_client import get_qdrant_client, CORPUS_COLLECTION
        from storage.bm25_index import get_bm25_index
        from qdrant_client.http import models as qmodels

        client = get_qdrant_client()
        bm25 = get_bm25_index()

        documents: list[dict] = []
        offset = None
        batch_size = 256

        while True:
            results, next_offset = await client.scroll(
                collection_name=CORPUS_COLLECTION,
                limit=batch_size,
                offset=offset,
                with_payload=["text"],
                with_vectors=False,
            )
            for point in results:
                text = (point.payload or {}).get("text", "")
                if text:
                    documents.append({"chunk_id": str(point.id), "text": text})

            if next_offset is None:
                break
            offset = next_offset

        bm25.build_index(documents)
        log.info("bm25_rebuild_complete", total_docs=len(documents))
        return {"status": "completed", "total_docs": len(documents)}

    return _run(_run_rebuild())
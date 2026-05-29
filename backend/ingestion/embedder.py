from uuid import uuid4

import structlog

from core.embeddings import embed_batch
from ingestion.chunker import Chunk
from storage.qdrant_client import ChunkPayload, upsert_corpus_chunks

log = structlog.get_logger(__name__)

_BATCH_SIZE = 32  # chunks per embedding batch


async def embed_and_store_chunks(
    chunks: list[Chunk],
    corpus_document_id: str,
    doc_metadata: dict,
) -> int:
    """
    Embed chunks in batches and upsert to Qdrant legal_corpus collection.

    Args:
        chunks:               List of Chunk objects from the chunker.
        corpus_document_id:   DB id of the parent CorpusDocument.
        doc_metadata:         Dict with keys: title, jurisdiction, legal_domain,
                              document_type, source_authority, procedure_id,
                              procedure_phase, step_number.

    Returns:
        Number of chunks successfully stored.
    """
    if not chunks:
        return 0

    stored = 0

    for batch_start in range(0, len(chunks), _BATCH_SIZE):
        batch = chunks[batch_start : batch_start + _BATCH_SIZE]
        texts = [c.text for c in batch]

        try:
            vectors = await embed_batch(texts)
        except Exception as exc:
            log.error(
                "embedder_batch_failed",
                batch_start=batch_start,
                error=str(exc),
            )
            raise

        payloads: list[ChunkPayload] = []
        for chunk, vector in zip(batch, vectors):
            payloads.append(
                ChunkPayload(
                    chunk_id=chunk.chunk_id,
                    corpus_document_id=corpus_document_id,
                    text=chunk.text,
                    title=doc_metadata.get("title", ""),
                    jurisdiction=doc_metadata.get("jurisdiction", "unknown"),
                    legal_domain=doc_metadata.get("legal_domain", "other"),
                    document_type=doc_metadata.get("document_type", ""),
                    source_authority=doc_metadata.get("source_authority", ""),
                    procedure_phase=doc_metadata.get("procedure_phase"),
                    procedure_id=doc_metadata.get("procedure_id"),
                    step_number=doc_metadata.get("step_number"),
                    chunk_index=chunk.chunk_index,
                    parent_section_id=chunk.parent_section_id,
                    vector=vector,
                )
            )

        await upsert_corpus_chunks(payloads)
        stored += len(payloads)

        log.info(
            "embedder_batch_stored",
            batch_start=batch_start,
            batch_size=len(batch),
            total_stored=stored,
        )

    log.info(
        "embedder_complete",
        corpus_document_id=corpus_document_id,
        total_chunks=stored,
    )
    return stored
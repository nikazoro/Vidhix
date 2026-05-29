from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import structlog
from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qmodels

from config import get_settings

log = structlog.get_logger(__name__)
settings = get_settings()

CORPUS_COLLECTION = "legal_corpus"
CASES_COLLECTION = "legal_cases"
VECTOR_SIZE = 768  # nomic-embed-text output dims


# ---------------------------------------------------------------------------
# Payload dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ChunkPayload:
    chunk_id: str
    corpus_document_id: str
    text: str
    title: str
    jurisdiction: str
    legal_domain: str
    document_type: str
    source_authority: str
    procedure_phase: str | None
    procedure_id: str | None
    step_number: int | None
    chunk_index: int
    parent_section_id: str | None
    vector: list[float]


@dataclass
class CasePayload:
    case_id: str
    jurisdiction: str
    legal_domain: str
    case_type: str | None
    outcome: str
    quality_score: float
    situation_summary: str
    vector: list[float]
    extra: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Client singleton
# ---------------------------------------------------------------------------

_client: AsyncQdrantClient | None = None


def get_qdrant_client() -> AsyncQdrantClient:
    global _client
    if _client is None:
        kwargs: dict[str, Any] = {"url": settings.qdrant_url}
        if settings.qdrant_api_key:
            kwargs["api_key"] = settings.qdrant_api_key
        _client = AsyncQdrantClient(**kwargs)
    return _client


# ---------------------------------------------------------------------------
# Collection bootstrap
# ---------------------------------------------------------------------------

async def ensure_collections_exist() -> None:
    client = get_qdrant_client()
    existing = {c.name for c in (await client.get_collections()).collections}

    if CORPUS_COLLECTION not in existing:
        await client.create_collection(
            collection_name=CORPUS_COLLECTION,
            vectors_config=qmodels.VectorParams(
                size=VECTOR_SIZE, distance=qmodels.Distance.COSINE
            ),
        )
        # Payload indexes for fast filtering
        for field_name, schema in [
            ("jurisdiction", qmodels.PayloadSchemaType.KEYWORD),
            ("legal_domain", qmodels.PayloadSchemaType.KEYWORD),
            ("document_type", qmodels.PayloadSchemaType.KEYWORD),
            ("procedure_phase", qmodels.PayloadSchemaType.KEYWORD),
            ("procedure_id", qmodels.PayloadSchemaType.KEYWORD),
        ]:
            await client.create_payload_index(
                collection_name=CORPUS_COLLECTION,
                field_name=field_name,
                field_schema=schema,
            )
        await client.create_payload_index(
            collection_name=CORPUS_COLLECTION,
            field_name="step_number",
            field_schema=qmodels.PayloadSchemaType.INTEGER,
        )
        log.info("qdrant_collection_created", collection=CORPUS_COLLECTION)

    if CASES_COLLECTION not in existing:
        await client.create_collection(
            collection_name=CASES_COLLECTION,
            vectors_config=qmodels.VectorParams(
                size=VECTOR_SIZE, distance=qmodels.Distance.COSINE
            ),
        )
        for field_name, schema in [
            ("jurisdiction", qmodels.PayloadSchemaType.KEYWORD),
            ("legal_domain", qmodels.PayloadSchemaType.KEYWORD),
            ("case_type", qmodels.PayloadSchemaType.KEYWORD),
            ("outcome", qmodels.PayloadSchemaType.KEYWORD),
        ]:
            await client.create_payload_index(
                collection_name=CASES_COLLECTION,
                field_name=field_name,
                field_schema=schema,
            )
        await client.create_payload_index(
            collection_name=CASES_COLLECTION,
            field_name="quality_score",
            field_schema=qmodels.PayloadSchemaType.FLOAT,
        )
        log.info("qdrant_collection_created", collection=CASES_COLLECTION)


# ---------------------------------------------------------------------------
# Corpus operations
# ---------------------------------------------------------------------------

async def upsert_corpus_chunks(chunks: list[ChunkPayload]) -> None:
    if not chunks:
        return
    client = get_qdrant_client()
    points = [
        qmodels.PointStruct(
            id=c.chunk_id,
            vector=c.vector,
            payload={
                "corpus_document_id": c.corpus_document_id,
                "text": c.text,
                "title": c.title,
                "jurisdiction": c.jurisdiction,
                "legal_domain": c.legal_domain,
                "document_type": c.document_type,
                "source_authority": c.source_authority,
                "procedure_phase": c.procedure_phase,
                "procedure_id": c.procedure_id,
                "step_number": c.step_number,
                "chunk_index": c.chunk_index,
                "parent_section_id": c.parent_section_id,
            },
        )
        for c in chunks
    ]
    await client.upsert(collection_name=CORPUS_COLLECTION, points=points)
    log.info("qdrant_corpus_upsert", count=len(points))


async def search_corpus(
    query_vector: list[float],
    jurisdiction: str,
    legal_domain: str,
    procedure_phase: str | None = None,
    limit: int = 50,
) -> list[qmodels.ScoredPoint]:

    client = get_qdrant_client()

    must: list[qmodels.Condition] = [
        qmodels.FieldCondition(
            key="jurisdiction",
            match=qmodels.MatchValue(value=jurisdiction),
        ),
        qmodels.FieldCondition(
            key="legal_domain",
            match=qmodels.MatchValue(value=legal_domain),
        ),
    ]

    if procedure_phase:
        must.append(
            qmodels.FieldCondition(
                key="procedure_phase",
                match=qmodels.MatchValue(value=procedure_phase),
            )
        )

    response = await client.query_points(
        collection_name=CORPUS_COLLECTION,
        query=query_vector,
        query_filter=qmodels.Filter(must=must),
        limit=limit,
        with_payload=True,
    )

    results = response.points

    log.debug(
        "qdrant_corpus_search",
        hits=len(results),
        jurisdiction=jurisdiction,
    )

    return results

# ---------------------------------------------------------------------------
# Cases operations
# ---------------------------------------------------------------------------

async def upsert_case(case_payload: CasePayload) -> None:
    client = get_qdrant_client()
    point = qmodels.PointStruct(
        id=case_payload.case_id,
        vector=case_payload.vector,
        payload={
            "case_id": case_payload.case_id,
            "jurisdiction": case_payload.jurisdiction,
            "legal_domain": case_payload.legal_domain,
            "case_type": case_payload.case_type,
            "outcome": case_payload.outcome,
            "quality_score": case_payload.quality_score,
            "situation_summary": case_payload.situation_summary,
            **case_payload.extra,
        },
    )
    await client.upsert(collection_name=CASES_COLLECTION, points=[point])
    log.info("qdrant_case_upsert", case_id=case_payload.case_id)


async def search_cases(
    query_vector: list[float],
    jurisdiction: str,
    legal_domain: str,
    limit: int = 10,
) -> list[qmodels.ScoredPoint]:

    client = get_qdrant_client()

    must: list[qmodels.Condition] = [
        qmodels.FieldCondition(
            key="jurisdiction",
            match=qmodels.MatchValue(value=jurisdiction),
        ),
        qmodels.FieldCondition(
            key="legal_domain",
            match=qmodels.MatchValue(value=legal_domain),
        ),
    ]

    response = await client.query_points(
        collection_name=CASES_COLLECTION,
        query=query_vector,
        query_filter=qmodels.Filter(must=must),
        limit=limit,
        with_payload=True,
    )

    results = response.points

    log.debug(
        "qdrant_cases_search",
        hits=len(results),
        jurisdiction=jurisdiction,
    )

    return results

async def delete_collection_items(
    collection: str,
    ids: list[qmodels.ExtendedPointId],
) -> None:

    if not ids:
        return

    client = get_qdrant_client()

    await client.delete(
        collection_name=collection,
        points_selector=qmodels.PointIdsList(points=ids),
    )

    log.info(
        "qdrant_items_deleted",
        collection=collection,
        count=len(ids),
    )
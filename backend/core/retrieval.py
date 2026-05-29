import asyncio
import time
from dataclasses import dataclass, field

import structlog

from core.embeddings import embed_text
from core.query_analyzer import QueryAnalysis
from core.workflow_engine import ProcedurePosition, get_workflow_engine
from storage.bm25_index import get_bm25_index
from storage.qdrant_client import search_corpus

log = structlog.get_logger(__name__)

_RRF_K = 60  # RRF constant
_CROSS_ENCODER_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"

_cross_encoder = None


def _get_cross_encoder():
    global _cross_encoder
    if _cross_encoder is None:
        from sentence_transformers import CrossEncoder  # type: ignore
        _cross_encoder = CrossEncoder(_CROSS_ENCODER_MODEL)
        log.info("cross_encoder_loaded", model=_CROSS_ENCODER_MODEL)
    return _cross_encoder


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class RetrievedChunk:
    chunk_id: str
    text: str
    source_title: str
    jurisdiction: str
    legal_domain: str
    document_type: str
    source_authority: str
    procedure_phase: str | None
    procedure_id: str | None
    step_number: int | None
    dense_score: float = 0.0
    sparse_score: float = 0.0
    rrf_score: float = 0.0
    final_score: float = 0.0


@dataclass
class WorkflowContext:
    procedure_id: str
    procedure_title: str
    current_step_title: str
    current_phase: str
    current_step_description: str
    actions: list[str]
    deadline_info: str
    forms: list[str]
    next_step_titles: list[str] = field(default_factory=list)


@dataclass
class RetrievalResult:
    chunks: list[RetrievedChunk]
    workflow_context: WorkflowContext | None
    retrieval_latency_ms: float


# ---------------------------------------------------------------------------
# RRF fusion
# ---------------------------------------------------------------------------

def _reciprocal_rank_fusion(
    dense_ranked: list[tuple[str, float]],
    sparse_ranked: list[tuple[str, float]],
    k: int = _RRF_K,
) -> dict[str, float]:
    """Merge two ranked lists using Reciprocal Rank Fusion."""
    scores: dict[str, float] = {}
    for rank, (chunk_id, _) in enumerate(dense_ranked):
        scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank + 1)
    for rank, (chunk_id, _) in enumerate(sparse_ranked):
        scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank + 1)
    return scores


# ---------------------------------------------------------------------------
# Workflow context builder
# ---------------------------------------------------------------------------

def _build_workflow_context(position: ProcedurePosition) -> WorkflowContext | None:
    try:
        engine = get_workflow_engine()
        proc = engine.load_procedure(position.procedure_id)
        step = engine.get_step(position.procedure_id, position.current_step_id)
        next_steps = engine.get_next_steps(position.procedure_id, position.current_step_id)

        dl = step.deadlines
        deadline_str = ""
        if dl.response_days is not None:
            day_type = "business" if dl.excludes_weekends else "calendar"
            deadline_str = f"{dl.response_days} {day_type} days"
            if dl.notes:
                deadline_str += f" — {dl.notes}"

        return WorkflowContext(
            procedure_id=position.procedure_id,
            procedure_title=proc.title,
            current_step_title=step.title,
            current_phase=step.phase,
            current_step_description=step.description,
            actions=step.actions,
            deadline_info=deadline_str,
            forms=step.forms,
            next_step_titles=[s.title for s in next_steps],
        )
    except Exception as exc:
        log.warning("workflow_context_build_failed", error=str(exc))
        return None


# ---------------------------------------------------------------------------
# Main retrieval function
# ---------------------------------------------------------------------------

async def hybrid_retrieve(
    query_analysis: QueryAnalysis,
    workflow_position: ProcedurePosition | None = None,
    top_k_final: int = 8,
) -> RetrievalResult:
    """
    Parallel hybrid retrieval:
      1. Dense (Qdrant)
      2. Sparse (BM25)
      3. Workflow step chunks (if position known)

    Fuse with RRF → rerank with cross-encoder → return top_k_final chunks.
    """
    t0 = time.monotonic()

    query_text = query_analysis.expanded_query
    jurisdiction = query_analysis.jurisdiction
    legal_domain = query_analysis.legal_domain

    # Determine procedure phase filter
    procedure_phase: str | None = None
    if workflow_position and query_analysis.procedure_position != "unknown":
        procedure_phase = query_analysis.procedure_position

    # ------------------------------------------------------------------
    # Phase 1: parallel dense + sparse retrieval
    # ------------------------------------------------------------------
    async def _dense() -> list[tuple[str, float, dict]]:
        try:
            vec = await embed_text(query_text)
            hits = await search_corpus(
                query_vector=vec,
                jurisdiction=jurisdiction,
                legal_domain=legal_domain,
                procedure_phase=procedure_phase,
                limit=50,
            )
            return [
                (str(h.id), h.score, h.payload or {})
                for h in hits
            ]
        except Exception as exc:
            log.error("dense_retrieval_failed", error=str(exc))
            return []

    def _sparse() -> list[tuple[str, float]]:
        try:
            bm25 = get_bm25_index()
            return bm25.search(query_text, top_k=50)
        except Exception as exc:
            log.error("sparse_retrieval_failed", error=str(exc))
            return []

    dense_results, sparse_results = await asyncio.gather(
        _dense(),
        asyncio.to_thread(_sparse),
    )

    # ------------------------------------------------------------------
    # Phase 2: workflow step chunk retrieval (synchronous, fast)
    # ------------------------------------------------------------------
    workflow_step_ids: set[str] = set()
    workflow_context: WorkflowContext | None = None

    if workflow_position:
        workflow_context = _build_workflow_context(workflow_position)
        try:
            engine = get_workflow_engine()
            current_step = engine.get_step(
                workflow_position.procedure_id, workflow_position.current_step_id
            )
            next_steps = engine.get_next_steps(
                workflow_position.procedure_id, workflow_position.current_step_id
            )
            # Collect step IDs for boosting: current + next 2
            all_steps = [current_step] + next_steps[:2]
            workflow_step_ids = {s.id for s in all_steps}
        except Exception as exc:
            log.warning("workflow_step_id_collection_failed", error=str(exc))

    # ------------------------------------------------------------------
    # Phase 3: build chunk payload lookup from dense results
    # ------------------------------------------------------------------
    payload_map: dict[str, dict] = {cid: payload for cid, _, payload in dense_results}
    dense_ranked: list[tuple[str, float]] = [(cid, score) for cid, score, _ in dense_results]

    # ------------------------------------------------------------------
    # Phase 4: RRF fusion
    # ------------------------------------------------------------------
    rrf_scores = _reciprocal_rank_fusion(dense_ranked, sparse_results)

    # Boost chunks that belong to relevant workflow steps
    for chunk_id in list(rrf_scores.keys()):
        payload = payload_map.get(chunk_id, {})
        if payload.get("procedure_id") in {workflow_position.procedure_id if workflow_position else None}:
            if payload.get("step_number") is not None:
                rrf_scores[chunk_id] *= 1.25  # 25% boost

    # Sort by RRF score, take top 30 for reranking
    top_30 = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)[:30]

    # ------------------------------------------------------------------
    # Phase 5: cross-encoder reranking
    # ------------------------------------------------------------------
    if not top_30:
        return RetrievalResult(
            chunks=[],
            workflow_context=workflow_context,
            retrieval_latency_ms=(time.monotonic() - t0) * 1000,
        )

    ce_pairs: list[tuple[str, str]] = []
    chunk_ids_ordered: list[str] = []
    for chunk_id, _ in top_30:
        payload = payload_map.get(chunk_id, {})
        chunk_text = payload.get("text", "")
        if chunk_text:
            ce_pairs.append((query_text, chunk_text))
            chunk_ids_ordered.append(chunk_id)

    try:
        ce = _get_cross_encoder()
        ce_scores: list[float] = ce.predict(ce_pairs).tolist()
    except Exception as exc:
        log.warning("cross_encoder_failed_falling_back", error=str(exc))
        ce_scores = [rrf_scores.get(cid, 0.0) for cid in chunk_ids_ordered]

    # Build final ranked list
    reranked = sorted(
        zip(chunk_ids_ordered, ce_scores),
        key=lambda x: x[1],
        reverse=True,
    )[:top_k_final]

    # ------------------------------------------------------------------
    # Phase 6: assemble RetrievedChunk objects
    # ------------------------------------------------------------------
    chunks: list[RetrievedChunk] = []
    for chunk_id, final_score in reranked:
        payload = payload_map.get(chunk_id, {})
        chunks.append(
            RetrievedChunk(
                chunk_id=chunk_id,
                text=payload.get("text", ""),
                source_title=payload.get("title", "Unknown"),
                jurisdiction=payload.get("jurisdiction", jurisdiction),
                legal_domain=payload.get("legal_domain", legal_domain),
                document_type=payload.get("document_type", ""),
                source_authority=payload.get("source_authority", ""),
                procedure_phase=payload.get("procedure_phase"),
                procedure_id=payload.get("procedure_id"),
                step_number=payload.get("step_number"),
                rrf_score=rrf_scores.get(chunk_id, 0.0),
                final_score=final_score,
            )
        )

    latency_ms = (time.monotonic() - t0) * 1000
    log.info(
        "retrieval_complete",
        dense_hits=len(dense_results),
        sparse_hits=len(sparse_results),
        after_rrf=len(top_30),
        after_rerank=len(chunks),
        latency_ms=round(latency_ms, 1),
    )

    return RetrievalResult(
        chunks=chunks,
        workflow_context=workflow_context,
        retrieval_latency_ms=latency_ms,
    )
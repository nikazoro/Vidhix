from dataclasses import dataclass

import structlog

from core.embeddings import embed_text
from core.query_analyzer import QueryAnalysis
from storage.qdrant_client import search_cases

log = structlog.get_logger(__name__)

_OUTCOME_SCORES: dict[str, float] = {
    "won": 1.0,
    "dismissed": 0.8,
    "settled": 0.7,
    "ongoing": 0.5,
    "lost": 0.2,
}

_OUTCOME_BADGES: dict[str, str] = {
    "won": "✅ Won",
    "dismissed": "🔵 Dismissed",
    "settled": "🟡 Settled",
    "ongoing": "⏳ Ongoing",
    "lost": "❌ Lost",
}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class SimilarCase:
    case_id: str
    situation_summary: str
    steps_taken: str          # formatted as numbered list
    outcome: str
    outcome_summary: str | None
    similarity_score: float
    outcome_badge: str
    quality_score: float
    final_score: float        # similarity * outcome_score * quality


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def retrieve_similar_cases(
    query_analysis: QueryAnalysis,
    top_k: int = 3,
) -> list[SimilarCase]:
    """
    1. Embed the expanded query.
    2. Search legal_cases Qdrant collection with jurisdiction + domain filter.
    3. Outcome-weighted reranking.
    4. Return top_k SimilarCase objects.
    """
    try:
        vector = await embed_text(query_analysis.expanded_query)
    except Exception as exc:
        log.error("case_retriever_embed_failed", error=str(exc))
        return []

    try:
        hits = await search_cases(
            query_vector=vector,
            jurisdiction=query_analysis.jurisdiction,
            legal_domain=query_analysis.legal_domain,
            limit=10,
        )
    except Exception as exc:
        log.error("case_retriever_search_failed", error=str(exc))
        return []

    cases: list[SimilarCase] = []
    for hit in hits:
        payload = hit.payload or {}
        outcome = payload.get("outcome", "ongoing")
        quality = float(payload.get("quality_score", 0.5))
        similarity = float(hit.score)
        outcome_weight = _OUTCOME_SCORES.get(outcome, 0.5)
        final_score = similarity * outcome_weight * quality

        # Format steps taken
        raw_steps = payload.get("steps_taken", [])
        if isinstance(raw_steps, list):
            steps_text = "\n".join(
                f"{i+1}. {step}" if isinstance(step, str) else f"{i+1}. {step.get('action', '')}"
                for i, step in enumerate(raw_steps)
            )
        else:
            steps_text = str(raw_steps)

        cases.append(
            SimilarCase(
                case_id=payload.get("case_id", str(hit.id)),
                situation_summary=payload.get("situation_summary", ""),
                steps_taken=steps_text or "No steps recorded.",
                outcome=outcome,
                outcome_summary=payload.get("outcome_summary"),
                similarity_score=similarity,
                outcome_badge=_OUTCOME_BADGES.get(outcome, "❓ Unknown"),
                quality_score=quality,
                final_score=final_score,
            )
        )

    # Sort by composite final_score descending
    cases.sort(key=lambda c: c.final_score, reverse=True)
    top = cases[:top_k]

    log.info(
        "case_retriever_complete",
        total_hits=len(hits),
        returned=len(top),
        jurisdiction=query_analysis.jurisdiction,
        legal_domain=query_analysis.legal_domain,
    )
    return top
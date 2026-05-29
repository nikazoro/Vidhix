import hashlib
import json
from dataclasses import dataclass, field

import structlog
from langchain_core.messages import HumanMessage, SystemMessage

from config import get_settings
from core.llm_provider import invoke_llm

log = structlog.get_logger(__name__)
settings = get_settings()

_CACHE_TTL = 60 * 10  # 10 minutes


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class QueryEntities:
    notice_type: str | None = None
    amount_disputed: float | None = None
    deadline_mentioned: str | None = None
    parties: list[str] = field(default_factory=list)


@dataclass
class QueryAnalysis:
    intent: str                     # procedure_guidance | rights_inquiry | document_analysis | general_info
    jurisdiction: str               # CA | NY | federal | ...
    legal_domain: str               # tenant-rights | employment | small-claims | consumer | immigration | other
    procedure_position: str         # pre-filing | filing | hearing | post-judgment | unknown
    entities: QueryEntities
    is_multi_hop: bool
    sub_queries: list[str]
    expanded_query: str
    original_query: str


# ---------------------------------------------------------------------------
# Redis cache helpers
# ---------------------------------------------------------------------------

def _cache_key(query: str, jurisdiction: str | None) -> str:
    raw = f"{query}|{jurisdiction}"
    return f"qry:{hashlib.sha256(raw.encode()).hexdigest()[:20]}"


async def _cache_get(key: str) -> QueryAnalysis | None:
    try:
        import redis.asyncio as aioredis  # type: ignore
        r = aioredis.from_url(settings.redis_url, decode_responses=True)
        raw = await r.get(key)
        await r.aclose()
        if raw:
            data = json.loads(raw)
            ents = data.pop("entities", {})
            return QueryAnalysis(
                entities=QueryEntities(**ents),
                **data,
            )
    except Exception as exc:
        log.warning("query_cache_get_failed", error=str(exc))
    return None


async def _cache_set(key: str, analysis: QueryAnalysis) -> None:
    try:
        import redis.asyncio as aioredis  # type: ignore
        r = aioredis.from_url(settings.redis_url, decode_responses=True)
        payload = {
            "intent": analysis.intent,
            "jurisdiction": analysis.jurisdiction,
            "legal_domain": analysis.legal_domain,
            "procedure_position": analysis.procedure_position,
            "entities": {
                "notice_type": analysis.entities.notice_type,
                "amount_disputed": analysis.entities.amount_disputed,
                "deadline_mentioned": analysis.entities.deadline_mentioned,
                "parties": analysis.entities.parties,
            },
            "is_multi_hop": analysis.is_multi_hop,
            "sub_queries": analysis.sub_queries,
            "expanded_query": analysis.expanded_query,
            "original_query": analysis.original_query,
        }
        await r.set(key, json.dumps(payload), ex=_CACHE_TTL)
        await r.aclose()
    except Exception as exc:
        log.warning("query_cache_set_failed", error=str(exc))


# ---------------------------------------------------------------------------
# Fallback analysis when LLM fails
# ---------------------------------------------------------------------------

def _fallback_analysis(user_message: str, jurisdiction: str | None) -> QueryAnalysis:
    return QueryAnalysis(
        intent="general_info",
        jurisdiction=jurisdiction or "federal",
        legal_domain="other",
        procedure_position="unknown",
        entities=QueryEntities(),
        is_multi_hop=False,
        sub_queries=[],
        expanded_query=user_message,
        original_query=user_message,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def analyze_query(
    user_message: str,
    session_history: list[dict],
    jurisdiction: str | None = None,
) -> QueryAnalysis:
    """
    Use LLM with JSON-mode output to extract structured query metadata.
    Results are cached in Redis for 10 minutes.
    """
    cache_key = _cache_key(user_message, jurisdiction)
    cached = await _cache_get(cache_key)
    if cached is not None:
        log.debug("query_analysis_cache_hit", key=cache_key)
        return cached

    history_text = "\n".join(
        f"{m['role'].upper()}: {m['content'][:300]}"
        for m in session_history[-6:]
    )

    prompt = f"""Analyze the following legal question and extract structured metadata.

CONVERSATION HISTORY:
{history_text}

CURRENT USER MESSAGE: {user_message}

SESSION JURISDICTION (if known): {jurisdiction or 'unknown'}

Return ONLY valid JSON with this exact structure:
{{
  "intent": "procedure_guidance" | "rights_inquiry" | "document_analysis" | "general_info",
  "jurisdiction": "CA" | "NY" | "TX" | "FL" | "federal" | "unknown",
  "legal_domain": "tenant-rights" | "employment" | "small-claims" | "consumer" | "immigration" | "family" | "other",
  "procedure_position": "pre-filing" | "filing" | "hearing" | "post-judgment" | "unknown",
  "entities": {{
    "notice_type": "<string or null>",
    "amount_disputed": <number or null>,
    "deadline_mentioned": "<string or null>",
    "parties": ["<party1>", ...]
  }},
  "is_multi_hop": true | false,
  "sub_queries": ["<sub-question 1>", "<sub-question 2>"],
  "expanded_query": "<rewritten query using full legal terminology>"
}}

Rules:
- intent=procedure_guidance: user is asking HOW to do something step-by-step
- intent=rights_inquiry: user is asking WHAT their rights are
- intent=document_analysis: user has uploaded or described a document they want analyzed
- intent=general_info: everything else
- expanded_query: rewrite the user's question with full legal terminology expanded (e.g. "notice to quit" instead of "kicked out notice", "unlawful detainer" instead of "eviction lawsuit")
- is_multi_hop: true if answering requires chaining multiple retrievals (e.g. "what should I do and how long do I have?")
- sub_queries: break multi-hop questions into atomic sub-questions; empty list if not multi-hop
- jurisdiction: infer from message text, city/state names, or use session jurisdiction as default"""

    try:
        response = await invoke_llm(
            [
                SystemMessage(content="You are a legal query analyzer. Respond only in valid JSON with no explanation."),
                HumanMessage(content=prompt),
            ],
            trace_name="analyze_query",
        )
        cleaned = response.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        data = json.loads(cleaned)

        ents_raw = data.get("entities", {})
        entities = QueryEntities(
            notice_type=ents_raw.get("notice_type"),
            amount_disputed=ents_raw.get("amount_disputed"),
            deadline_mentioned=ents_raw.get("deadline_mentioned"),
            parties=ents_raw.get("parties", []),
        )

        analysis = QueryAnalysis(
            intent=data.get("intent", "general_info"),
            jurisdiction=data.get("jurisdiction") or jurisdiction or "federal",
            legal_domain=data.get("legal_domain", "other"),
            procedure_position=data.get("procedure_position", "unknown"),
            entities=entities,
            is_multi_hop=data.get("is_multi_hop", False),
            sub_queries=data.get("sub_queries", []),
            expanded_query=data.get("expanded_query", user_message),
            original_query=user_message,
        )

        await _cache_set(cache_key, analysis)
        log.info(
            "query_analyzed",
            intent=analysis.intent,
            jurisdiction=analysis.jurisdiction,
            legal_domain=analysis.legal_domain,
            is_multi_hop=analysis.is_multi_hop,
        )
        return analysis

    except Exception as exc:
        log.error("query_analysis_failed", error=str(exc))
        return _fallback_analysis(user_message, jurisdiction)
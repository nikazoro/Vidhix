"""
Main RAG orchestration pipeline using LangGraph StateGraph.
"""
import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Any, cast

import structlog
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph
from typing_extensions import TypedDict

from core.case_retriever import SimilarCase, retrieve_similar_cases
from core.escalation import EscalationResult, get_escalation_detector
from core.llm_provider import invoke_llm, stream_llm
from core.query_analyzer import QueryAnalysis, analyze_query
from core.retrieval import RetrievalResult, WorkflowContext, hybrid_retrieve
from core.workflow_engine import ProcedurePosition, get_workflow_engine

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

LEGAL_SYSTEM_PROMPT = """\
You are LexAra, an AI legal information assistant.

STRICT RULES:
1. ONLY use information from the provided CONTEXT to answer. Never use your training knowledge for specific legal facts, deadlines, or procedures.
2. If the context does not contain enough information to answer, say: "I don't have reliable information about this specific situation in my current knowledge base."
3. Always structure your response as:
   - **Summary**: 2-3 sentence direct answer
   - **What You Can Do**: numbered steps based on context
   - **Important Deadlines**: if any found in context (if none, omit this section)
   - **What To Watch Out For**: key risks from context
4. After every response add: "⚠️ This is legal information, not legal advice. Nothing here creates an attorney-client relationship. Laws vary by jurisdiction and situation. Always verify current laws with official sources."
5. Never say "you should" or "you must" — say "based on the documents, the procedure is..." or "according to California law..."
6. Mark every factual claim with [SOURCE_N] referencing the provided sources (e.g. [SOURCE_1]).
7. If similar past cases are provided, reference them briefly as "In a similar past case..." without identifying anyone.
8. Never fabricate case citations, statute numbers, or deadlines not present in the context.

CONTEXT:
{context}

SIMILAR PAST CASES:
{cases_context}

WORKFLOW POSITION:
{workflow_context}
"""


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class RAGState(TypedDict):
    session_id: str
    user_message: str
    session_history: list[dict]
    jurisdiction: str
    query_analysis: QueryAnalysis | None
    workflow_position: ProcedurePosition | None
    retrieval_result: RetrievalResult | None
    similar_cases: list[SimilarCase]
    escalation_result: EscalationResult | None
    context: str | None
    response: str | None
    sources: list[dict]
    confidence_score: float
    error: str | None


# ---------------------------------------------------------------------------
# Graph nodes
# ---------------------------------------------------------------------------

async def node_analyze_query(state: RAGState) -> dict:
    try:
        analysis = await analyze_query(
            user_message=state["user_message"],
            session_history=state["session_history"],
            jurisdiction=state.get("jurisdiction"),
        )
        return {"query_analysis": analysis}
    except Exception as exc:
        log.error("node_analyze_query_failed", error=str(exc))
        return {"error": f"Query analysis failed: {exc}"}


async def node_detect_workflow_position(state: RAGState) -> dict:
    if state.get("error"):
        return {}
    try:
        engine = get_workflow_engine()
        position = await engine.detect_procedure_position(
            user_message=state["user_message"],
            session_history=state["session_history"],
        )
        return {"workflow_position": position}
    except Exception as exc:
        log.warning("node_detect_workflow_failed", error=str(exc))
        return {"workflow_position": None}


async def node_retrieve_context(state: RAGState) -> dict:
    if state.get("error"):
        return {}
    query_analysis = state.get("query_analysis")
    if not query_analysis:
        return {"retrieval_result": None, "similar_cases": []}

    try:
        retrieval_result, similar_cases = await asyncio.gather(
            hybrid_retrieve(
                query_analysis=query_analysis,
                workflow_position=state.get("workflow_position"),
                top_k_final=8,
            ),
            retrieve_similar_cases(query_analysis=query_analysis, top_k=3),
        )
        return {"retrieval_result": retrieval_result, "similar_cases": similar_cases}
    except Exception as exc:
        log.error("node_retrieve_context_failed", error=str(exc))
        return {"retrieval_result": None, "similar_cases": [], "error": f"Retrieval failed: {exc}"}


async def node_check_escalation(state: RAGState) -> dict:
    if state.get("error"):
        return {}
    try:
        retrieval_result = state.get("retrieval_result")
        context_chunks = []
        if retrieval_result:
            context_chunks = [c.text for c in retrieval_result.chunks[:5]]

        detector = get_escalation_detector()
        result = await detector.check_escalation(
            user_message=state["user_message"],
            context_chunks=context_chunks,
            session_history=state["session_history"],
        )
        return {"escalation_result": result}
    except Exception as exc:
        log.warning("node_escalation_check_failed", error=str(exc))
        return {"escalation_result": None}


def node_build_context(state: RAGState) -> dict:
    if state.get("error"):
        return {}

    retrieval_result = state.get("retrieval_result")
    similar_cases = state.get("similar_cases", [])
    workflow_position = state.get("workflow_position")
    escalation_result = state.get("escalation_result")

    # --- Build sources list ---
    sources: list[dict] = []
    context_parts: list[str] = []

    if retrieval_result and retrieval_result.chunks:
        for i, chunk in enumerate(retrieval_result.chunks, start=1):
            source_label = f"SOURCE_{i}"
            context_parts.append(
                f"[{source_label}] From: {chunk.source_title} "
                f"({chunk.jurisdiction}, {chunk.legal_domain})\n{chunk.text}"
            )
            sources.append({
                "source_id": chunk.chunk_id,
                "title": chunk.source_title,
                "jurisdiction": chunk.jurisdiction,
                "legal_domain": chunk.legal_domain,
                "chunk_text": chunk.text[:300],
                "score": round(chunk.final_score, 4),
                "document_type": chunk.document_type,
                "source_authority": chunk.source_authority,
            })

    context_str = "\n\n".join(context_parts) if context_parts else "No relevant legal documents found for this query."

    # --- Build cases context ---
    cases_parts: list[str] = []
    for i, case in enumerate(similar_cases, start=1):
        cases_parts.append(
            f"Case {i} [{case.outcome_badge}]\n"
            f"Situation: {case.situation_summary[:300]}\n"
            f"Steps taken:\n{case.steps_taken}\n"
            f"Outcome: {case.outcome_summary or case.outcome}"
        )
    cases_str = "\n\n---\n\n".join(cases_parts) if cases_parts else "No similar past cases found."

    # --- Build workflow context ---
    wf_str = "No active workflow procedure detected."
    if retrieval_result and retrieval_result.workflow_context:
        wc = retrieval_result.workflow_context
        actions_str = "\n".join(f"  - {a}" for a in wc.actions)
        wf_str = (
            f"Procedure: {wc.procedure_title}\n"
            f"Current Step: {wc.current_step_title} (Phase: {wc.current_phase})\n"
            f"Description: {wc.current_step_description}\n"
            f"Actions:\n{actions_str}\n"
            f"Deadline: {wc.deadline_info or 'Not specified'}\n"
            f"Required Forms: {', '.join(wc.forms) or 'None'}\n"
            f"Next Steps: {', '.join(wc.next_step_titles) or 'None'}"
        )

    # --- Append escalation warning if needed ---
    if escalation_result and escalation_result.escalation_needed:
        context_str += (
            f"\n\n⚠️ ESCALATION FLAG ({escalation_result.severity}): "
            f"{escalation_result.escalation_message}"
        )

    full_context = LEGAL_SYSTEM_PROMPT.format(
        context=context_str,
        cases_context=cases_str,
        workflow_context=wf_str,
    )

    # --- Confidence scoring ---
    confidence = 0.5
    if retrieval_result and retrieval_result.chunks:
        top_score = retrieval_result.chunks[0].final_score
        # Normalize cross-encoder score (typically -10 to +10) to 0-1
        normalized = max(0.0, min(1.0, (top_score + 5.0) / 10.0))
        chunk_count_bonus = min(0.2, len(retrieval_result.chunks) * 0.025)
        confidence = min(0.98, normalized + chunk_count_bonus)

    return {
        "context": full_context,
        "sources": sources,
        "confidence_score": round(confidence, 3),
    }


async def node_generate_response(state: RAGState) -> dict:
    if state.get("error"):
        return {"response": f"I'm sorry, I encountered an error: {state.get('error')}"}

    context = state.get("context", "")
    if not context:
        return {"response": "I was unable to retrieve relevant legal information for your question. Please try rephrasing."}

    messages = [
        SystemMessage(content=context),
        HumanMessage(content=state["user_message"]),
    ]

    try:
        response_text = await invoke_llm(messages, trace_name="generate_response")
        return {"response": response_text}
    except Exception as exc:
        log.error("node_generate_response_failed", error=str(exc))
        return {
            "response": "I'm sorry, I was unable to generate a response at this time. Please try again.",
            "error": str(exc),
        }


def node_post_process(state: RAGState) -> dict:
    """Final cleanup: ensure disclaimer is present, cap confidence if escalation triggered."""
    response = response = state.get("response") or ""
    escalation = state.get("escalation_result")
    confidence = state.get("confidence_score", 0.5)

    # Ensure disclaimer exists
    disclaimer = "⚠️ This is legal information, not legal advice."
    if disclaimer not in response:
        response = response.rstrip() + f"\n\n{disclaimer} Nothing here creates an attorney-client relationship."

    # Add escalation block if triggered
    if escalation and escalation.escalation_needed:
        esc_block = (
            f"\n\n---\n🚨 **Attorney Consultation Recommended** ({escalation.severity} priority)\n"
            f"{escalation.escalation_message}\n"
        )
        if escalation.resources:
            esc_block += "\n**Resources:**\n" + "\n".join(f"- {r}" for r in escalation.resources)
        if esc_block not in response:
            response += esc_block
        # Lower confidence when escalation is needed
        confidence = min(confidence, 0.6)

    return {"response": response, "confidence_score": confidence}


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------

def _build_graph() -> Any:
    g = StateGraph(RAGState)

    g.add_node("analyze_query", node_analyze_query)
    g.add_node("detect_workflow_position", node_detect_workflow_position)
    g.add_node("retrieve_context", node_retrieve_context)
    g.add_node("check_escalation", node_check_escalation)
    g.add_node("build_context", node_build_context)
    g.add_node("generate_response", node_generate_response)
    g.add_node("post_process", node_post_process)

    g.set_entry_point("analyze_query")
    g.add_edge("analyze_query", "detect_workflow_position")
    g.add_edge("detect_workflow_position", "retrieve_context")
    g.add_edge("retrieve_context", "check_escalation")
    g.add_edge("check_escalation", "build_context")
    g.add_edge("build_context", "generate_response")
    g.add_edge("generate_response", "post_process")
    g.add_edge("post_process", END)

    return g.compile()


_pipeline = None


def get_pipeline():
    global _pipeline
    if _pipeline is None:
        _pipeline = _build_graph()
    return _pipeline


# ---------------------------------------------------------------------------
# Synchronous (non-streaming) run
# ---------------------------------------------------------------------------

async def run_pipeline(initial_state: RAGState) -> RAGState:
    pipeline = get_pipeline()
    final_state = await pipeline.ainvoke(initial_state)
    return final_state


# ---------------------------------------------------------------------------
# Streaming run — yields SSE-style event dicts
# ---------------------------------------------------------------------------

async def run_pipeline_streaming(
    initial_state: RAGState,
) -> AsyncGenerator[dict, None]:
    """
    Yields dicts representing SSE events:
      {type: "thinking",            content: str}
      {type: "retrieval_complete",  content: {chunks_count, workflow_detected, cases_count}}
      {type: "escalation_check",    content: {escalation_needed, severity}}
      {type: "token",               content: str}
      {type: "complete",            content: {sources, similar_cases, confidence_score, escalation_result, workflow_position}}
      {type: "error",               content: str}
    """
    try:
        # --- Stage 1: analyze + detect workflow ---
        yield {"type": "thinking", "content": "Analyzing your question…"}
        # state: dict = dict(initial_state)
        from typing import cast
        state = cast(RAGState, dict(initial_state))

        analysis_result = await node_analyze_query(state)
        state.update(analysis_result)
        if state.get("error"):
            yield {"type": "error", "content": state["error"]}
            return

        workflow_result = await node_detect_workflow_position(state)
        state.update(workflow_result)

        # --- Stage 2: retrieval ---
        yield {"type": "thinking", "content": "Searching legal documents and past cases…"}
        retrieval_result = await node_retrieve_context(state)
        state.update(retrieval_result)

        # yield {
        #     "type": "retrieval_complete",
        #     "content": {
        #         "chunks_count": len((state.get("retrieval_result") or {}).get("chunks", [])) if state.get("retrieval_result") else 0,
        #         "workflow_detected": state.get("workflow_position") is not None,
        #         "cases_count": len(state.get("similar_cases", [])),
        #     },
        # }
        retrieval = state.get("retrieval_result")

        yield {
            "type": "retrieval_complete",
            "content": {
                "chunks_count": len(retrieval.chunks) if retrieval else 0,
                "workflow_detected": state.get("workflow_position") is not None,
                "cases_count": len(state.get("similar_cases", [])),
            },
        }

        # --- Stage 3: escalation ---
        yield {"type": "thinking", "content": "Checking for situations requiring an attorney…"}
        esc_result = await node_check_escalation(state)
        state.update(esc_result)

        esc = state.get("escalation_result")
        yield {
            "type": "escalation_check",
            "content": {
                "escalation_needed": esc.escalation_needed if esc else False,
                "severity": esc.severity if esc else "LOW",
            },
        }

        # --- Stage 4: build context ---
        context_result = node_build_context(state)
        state.update(context_result)

        # --- Stage 5: streaming LLM generation ---
        context = state.get("context", "")
        messages = [
            SystemMessage(content=context),
            HumanMessage(content=state["user_message"]),
        ]

        full_response = ""
        async for token in stream_llm(messages, trace_name="stream_response"):
            full_response += token
            yield {"type": "token", "content": token}

        state["response"] = full_response

        # --- Stage 6: post-process ---
        pp_result = node_post_process(state)
        state.update(pp_result)

        # Build serialisable similar cases
        serialised_cases = [
            {
                "case_id": c.case_id,
                "situation_summary": c.situation_summary,
                "steps_taken": c.steps_taken,
                "outcome": c.outcome,
                "outcome_summary": c.outcome_summary,
                "similarity_score": c.similarity_score,
                "outcome_badge": c.outcome_badge,
            }
            for c in state.get("similar_cases", [])
        ]

        # Build serialisable escalation result
        esc_obj = state.get("escalation_result")
        esc_serialised = None
        if esc_obj:
            esc_serialised = {
                "escalation_needed": esc_obj.escalation_needed,
                "severity": esc_obj.severity,
                "reason": esc_obj.reason,
                "escalation_message": esc_obj.escalation_message,
                "resources": esc_obj.resources,
            }

        # Build serialisable workflow position
        wf_pos = state.get("workflow_position")
        wf_serialised = None
        if wf_pos:
            wf_serialised = {
                "procedure_id": wf_pos.procedure_id,
                "procedure_title": wf_pos.procedure_title,
                "current_step_id": wf_pos.current_step_id,
                "current_step_title": wf_pos.current_step_title,
                "current_phase": wf_pos.current_phase,
                "completed_steps": wf_pos.completed_steps,
            }

        yield {
            "type": "complete",
            "content": {
                "response": state.get("response", ""),
                "sources": state.get("sources", []),
                "similar_cases": serialised_cases,
                "confidence_score": state.get("confidence_score", 0.5),
                "escalation_result": esc_serialised,
                "workflow_position": wf_serialised,
            },
        }

    except Exception as exc:
        log.error("pipeline_streaming_failed", error=str(exc))
        yield {"type": "error", "content": str(exc)}
"""
LexAra — Chat interface (Page 1)
Two-column layout: 65% chat | 35% sources + cases panel.
"""
import time

import streamlit as st

from components.cases_panel import render_cases_panel
from components.chat_message import render_assistant_message, render_user_message
from components.escalation_alert import render_escalation_alert
from components.sources_panel import render_sources_panel
from utils.api_client import LexaraAPIError, get_client
from utils.session_state import (
    add_message,
    ensure_backend_session,
    get_messages,
    get_workflow_position,
    init_session_state,
    set_workflow_position,
)

init_session_state()

st.title("💬 LexAra Legal Chat")
st.caption(
    "Ask any question about your legal rights, procedures, or documents. "
    "I'll guide you step by step."
)

# ---------------------------------------------------------------------------
# Workflow position banner
# ---------------------------------------------------------------------------
wf_pos = get_workflow_position()
if wf_pos:
    st.info(
        f"📍 **Detected procedure:** {wf_pos.get('procedure_title', '')} — "
        f"Step {wf_pos.get('current_step_id', '').split('_')[-1]}: "
        f"*{wf_pos.get('current_step_title', '')}* "
        f"(Phase: {wf_pos.get('current_phase', '')})"
    )

# ---------------------------------------------------------------------------
# Layout columns
# ---------------------------------------------------------------------------
chat_col, panel_col = st.columns([0.65, 0.35], gap="medium")

# ---------------------------------------------------------------------------
# Right panel — sources + cases
# ---------------------------------------------------------------------------
with panel_col:
    sources_tab, cases_tab = st.tabs(["📚 Sources", "🗂️ Similar Cases"])

    with sources_tab:
        render_sources_panel(st.session_state.get("last_sources", []))

    with cases_tab:
        render_cases_panel(st.session_state.get("last_similar_cases", []))

    # Escalation alert
    last_esc = st.session_state.get("last_escalation")
    if last_esc and last_esc.get("escalation_needed"):
        st.divider()
        render_escalation_alert(last_esc)

# ---------------------------------------------------------------------------
# Chat column
# ---------------------------------------------------------------------------
with chat_col:
    # Pending document notification
    if st.session_state.get("pending_document_id"):
        doc_name = st.session_state.get("pending_document_name", "document")
        st.success(
            f"📄 Document **{doc_name}** is ready. "
            "Your next message will reference it automatically."
        )

    # Render existing messages
    messages = get_messages()
    for msg in messages:
        if msg["role"] == "user":
            render_user_message(msg["content"])
        else:
            meta = msg.get("metadata", {})
            render_assistant_message(
                content=msg["content"],
                message_id=meta.get("message_id"),
                confidence_score=meta.get("confidence_score"),
                sources=meta.get("sources", []),
                escalation_triggered=meta.get("escalation_triggered", False),
                similar_cases=meta.get("similar_cases", []),
                show_feedback=True,
            )

    # ---------------------------------------------------------------------------
    # Chat input
    # ---------------------------------------------------------------------------
    user_input = st.chat_input(
        "Ask about your legal rights, situation, or uploaded document…",
        key="chat_input",
    )

    if user_input:
        # Ensure backend session
        session_id = ensure_backend_session(st.session_state.get("jurisdiction"))

        # Append user message locally
        add_message("user", user_input)
        render_user_message(user_input)

        # Determine if a document is pending
        doc_ids = []
        if st.session_state.get("pending_document_id"):
            doc_ids = [st.session_state["pending_document_id"]]

        # ---------------------------------------------------------------------------
        # Stream response
        # ---------------------------------------------------------------------------
        with st.chat_message("assistant", avatar="⚖️"):
            status_placeholder = st.empty()
            token_placeholder = st.empty()

            accumulated_tokens = ""
            final_event: dict = {}
            error_occurred = False

            try:
                for event in get_client().send_message_stream(
                    session_id=session_id,
                    content=user_input,
                    document_ids=doc_ids,
                ):
                    etype = event.get("type")
                    content = event.get("content")

                    if etype == "thinking":
                        status_placeholder.caption(f"⏳ {content}")

                    elif etype == "retrieval_complete":
                        chunks = content.get("chunks_count", 0) if content else 0
                        cases = content.get("cases_count", 0) if content else 0
                        wf = content.get("workflow_detected", False) if content else False
                        status_placeholder.caption(
                            f"🔍 Retrieved {chunks} document chunk(s), "
                            f"{cases} similar case(s)"
                            + (" — 📍 procedure detected" if wf else "")
                        )

                    elif etype == "escalation_check":
                        if content and content.get("escalation_needed"):
                            status_placeholder.caption(
                                f"⚠️ Escalation flag: {content.get('severity', 'MEDIUM')}"
                            )

                    elif etype == "token":
                        accumulated_tokens += content or ""
                        token_placeholder.markdown(accumulated_tokens + "▌")

                    elif etype == "complete":
                        final_event = content or {}
                        token_placeholder.markdown(final_event.get("response", accumulated_tokens))
                        status_placeholder.empty()

                    elif etype == "error":
                        error_occurred = True
                        st.error(f"⚠️ Error: {content}")
                        break

            except LexaraAPIError as exc:
                error_occurred = True
                st.error(
                    f"Could not reach the LexAra backend: {exc.detail}\n\n"
                    "Please ensure the backend server is running."
                )

            if not error_occurred and final_event:
                # Clear document pending state
                if doc_ids:
                    st.session_state["pending_document_id"] = None
                    st.session_state["pending_document_name"] = None

                # Update sidebar panels
                sources = final_event.get("sources", [])
                similar_cases = final_event.get("similar_cases", [])
                escalation = final_event.get("escalation_result")
                wf_position = final_event.get("workflow_position")
                confidence = final_event.get("confidence_score", 0.5)
                response_text = final_event.get("response", accumulated_tokens)

                st.session_state["last_sources"] = sources
                st.session_state["last_similar_cases"] = similar_cases
                st.session_state["last_escalation"] = escalation
                st.session_state["last_confidence"] = confidence
                if wf_position:
                    set_workflow_position(wf_position)

                # Persist to local message history
                add_message(
                    "assistant",
                    response_text,
                    metadata={
                        "confidence_score": confidence,
                        "sources": sources,
                        "similar_cases": similar_cases,
                        "escalation_triggered": bool(escalation and escalation.get("escalation_needed")),
                    },
                )

                st.rerun()
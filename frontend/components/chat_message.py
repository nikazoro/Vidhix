"""
Custom chat message renderer component.
"""
import streamlit as st


def _confidence_badge(score: float | None) -> str:
    if score is None:
        return ""
    if score >= 0.8:
        return f"🟢 {score:.0%}"
    if score >= 0.6:
        return f"🟡 {score:.0%}"
    return f"🔴 {score:.0%}"


def render_user_message(content: str) -> None:
    """Right-aligned user bubble."""
    with st.chat_message("user", avatar="🧑"):
        st.markdown(content)


def render_assistant_message(
    content: str,
    message_id: str | None = None,
    confidence_score: float | None = None,
    sources: list[dict] | None = None,
    escalation_triggered: bool = False,
    similar_cases: list[dict] | None = None,
    show_feedback: bool = True,
) -> None:
    """
    Left-aligned assistant card with:
    - Markdown-rendered text
    - Confidence badge
    - Expandable sources section
    - Escalation alert (if triggered)
    - Feedback bar (stars)
    """
    with st.chat_message("assistant", avatar="⚖️"):
        # Confidence badge inline
        badge = _confidence_badge(confidence_score)
        if badge:
            st.caption(f"Confidence: {badge}")

        # Escalation alert
        if escalation_triggered:
            st.error(
                "🚨 **This situation may require an attorney.** "
                "See the escalation notice at the end of this response.",
                icon="⚠️",
            )

        # Main content
        st.markdown(content)

        # Sources expander
        if sources:
            with st.expander(f"📚 Sources ({len(sources)})", expanded=False):
                for i, src in enumerate(sources, start=1):
                    st.markdown(
                        f"**[SOURCE_{i}]** {src.get('title', 'Unknown')} "
                        f"— {src.get('jurisdiction', '')} / {src.get('legal_domain', '')} "
                        f"*(score: {src.get('score', 0):.3f})*"
                    )
                    if src.get("source_authority"):
                        st.caption(f"Authority: {src['source_authority']}")
                    st.markdown(f"> {src.get('chunk_text', '')[:250]}…")
                    if i < len(sources):
                        st.divider()

        # Feedback bar
        if show_feedback and message_id:
            from components.feedback_bar import render_feedback_bar
            render_feedback_bar(message_id=message_id, sources=sources, similar_cases=similar_cases)
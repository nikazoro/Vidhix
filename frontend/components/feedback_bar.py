"""
Star rating feedback bar component.
"""
import streamlit as st

from utils.session_state import is_feedback_submitted, mark_feedback_submitted


def render_feedback_bar(
    message_id: str,
    sources: list[dict] | None = None,
    similar_cases: list[dict] | None = None,
) -> None:
    """
    Render a 1-5 star rating widget below an assistant message.
    Submits feedback to the backend and marks as submitted to prevent duplicates.
    """
    if is_feedback_submitted(message_id):
        st.caption("✅ Feedback submitted — thank you!")
        return

    st.markdown("---")
    st.caption("Was this response helpful?")
    col1, col2, col3 = st.columns([2, 1, 3])

    with col1:
        rating = st.feedback("stars", key=f"stars_{message_id}")

    with col2:
        submit = st.button("Submit", key=f"fb_submit_{message_id}", type="secondary")

    if submit and rating is not None:
        from utils.api_client import get_client, LexaraAPIError
        # st.feedback returns 0-4; convert to 1-5
        stars = rating + 1
        source_ids = [s.get("source_id", "") for s in (sources or [])]
        case_ids = [c.get("case_id", "") for c in (similar_cases or [])]
        session_id = st.session_state.get("session_id")

        try:
            get_client().submit_feedback(
                message_id=message_id,
                rating=stars,
                feedback_type="star_rating",
                session_id=session_id,
                contributed_case_ids=[cid for cid in case_ids if cid],
                contributed_source_ids=[sid for sid in source_ids if sid],
            )
            mark_feedback_submitted(message_id)
            st.success("Thank you for your feedback!")
            st.rerun()
        except LexaraAPIError as exc:
            st.error(f"Could not submit feedback: {exc.detail}")
    elif submit and rating is None:
        st.warning("Please select a star rating before submitting.")
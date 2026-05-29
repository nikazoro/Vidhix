"""
Helpers for managing Streamlit session state across pages.
"""
from uuid import uuid4

import streamlit as st


_DEFAULTS: dict = {
    "session_id": None,           # backend session UUID
    "messages": [],               # list of message dicts
    "jurisdiction": "CA",
    "legal_domain": None,
    "pending_document_id": None,  # document uploaded but not yet used in chat
    "pending_document_name": None,
    "workflow_position": None,
    "last_sources": [],
    "last_similar_cases": [],
    "last_confidence": None,
    "last_escalation": None,
    "feedback_submitted": set(),  # message_ids already rated
}


def init_session_state() -> None:
    """Initialize all session state keys with defaults if not already set."""
    for key, default in _DEFAULTS.items():
        if key not in st.session_state:
            st.session_state[key] = default


def ensure_backend_session(jurisdiction: str | None = None) -> str:
    """
    Ensure a backend session exists. Creates one via API if not.
    Returns the session_id string.
    """
    from utils.api_client import get_client, LexaraAPIError

    if st.session_state.get("session_id"):
        return st.session_state["session_id"]

    client = get_client()
    try:
        result = client.create_session(
            jurisdiction=jurisdiction or st.session_state.get("jurisdiction"),
            legal_domain=st.session_state.get("legal_domain"),
        )
        st.session_state["session_id"] = result["id"]
        return result["id"]
    except LexaraAPIError as exc:
        st.error(f"Could not create session: {exc.detail}")
        # Return a client-side placeholder so the UI doesn't crash
        placeholder = f"local-{uuid4()}"
        st.session_state["session_id"] = placeholder
        return placeholder


def reset_session() -> None:
    """Clear the current session and start fresh."""
    for key, default in _DEFAULTS.items():
        st.session_state[key] = default


def add_message(role: str, content: str, metadata: dict | None = None) -> None:
    st.session_state["messages"].append({
        "role": role,
        "content": content,
        "metadata": metadata or {},
    })


def get_messages() -> list[dict]:
    return st.session_state.get("messages", [])


def set_workflow_position(position: dict | None) -> None:
    st.session_state["workflow_position"] = position


def get_workflow_position() -> dict | None:
    return st.session_state.get("workflow_position")


def mark_feedback_submitted(message_id: str) -> None:
    st.session_state["feedback_submitted"].add(message_id)


def is_feedback_submitted(message_id: str) -> bool:
    return message_id in st.session_state.get("feedback_submitted", set())
"""
Escalation warning banner component.
"""
import streamlit as st

_SEVERITY_CONFIG = {
    "HIGH":   {"icon": "🚨", "color": "error",   "label": "HIGH PRIORITY"},
    "MEDIUM": {"icon": "⚠️",  "color": "warning", "label": "MEDIUM PRIORITY"},
    "LOW":    {"icon": "ℹ️",  "color": "info",    "label": "LOW PRIORITY"},
}


def render_escalation_alert(escalation: dict | None) -> None:
    """
    Render a prominent escalation warning if escalation is needed.
    `escalation` is a dict with keys: escalation_needed, severity, reason,
    escalation_message, resources.
    """
    if not escalation or not escalation.get("escalation_needed"):
        return

    severity = escalation.get("severity", "MEDIUM")
    cfg = _SEVERITY_CONFIG.get(severity, _SEVERITY_CONFIG["MEDIUM"])
    icon = cfg["icon"]
    label = cfg["label"]
    msg = escalation.get("escalation_message", "")
    reason = escalation.get("reason", "")
    resources = escalation.get("resources", [])

    container = st.error if cfg["color"] == "error" else (
        st.warning if cfg["color"] == "warning" else st.info
    )

    container(
        f"{icon} **Attorney Consultation Recommended — {label}**\n\n"
        f"{msg}\n\n"
        f"*Reason: {reason}*"
    )

    if resources:
        with st.expander("📞 Resources & Contacts", expanded=True):
            for r in resources:
                st.markdown(f"• {r}")
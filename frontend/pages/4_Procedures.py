"""
LexAra — Browse Legal Procedures (Page 4)
"""
import streamlit as st

from utils.api_client import LexaraAPIError, get_client
from utils.session_state import init_session_state

init_session_state()

st.title("🗺️ Legal Procedure Guides")
st.caption(
    "Browse step-by-step guides for common legal procedures. "
    "Click any procedure to see the full process."
)

JURISDICTIONS = ["(all)", "CA", "NY", "TX", "FL", "IL", "PA", "OH", "GA", "WA", "AZ", "federal"]
DOMAINS = [
    "(all)", "tenant-rights", "employment", "small-claims",
    "consumer", "immigration", "family", "other",
]

_PHASE_COLORS = {
    "pre-filing": "🔵",
    "filing": "🟠",
    "hearing": "🟣",
    "post-hearing": "🟢",
    "post-judgment": "⚫",
}

# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------
filter_col1, filter_col2 = st.columns(2)
with filter_col1:
    jur_filter = st.selectbox("Jurisdiction", JURISDICTIONS)
with filter_col2:
    dom_filter = st.selectbox("Legal Domain", DOMAINS)

jur_arg = None if jur_filter == "(all)" else jur_filter
dom_arg = None if dom_filter == "(all)" else dom_filter

# ---------------------------------------------------------------------------
# Load procedures
# ---------------------------------------------------------------------------
try:
    procedures = get_client().list_workflows(jurisdiction=jur_arg, legal_domain=dom_arg)
except LexaraAPIError as exc:
    st.error(
        f"Could not load procedures: {exc.detail}\n\n"
        "Please ensure the backend server is running."
    )
    st.stop()

if not procedures:
    st.info("No procedures found matching your filters.")
    st.stop()

st.markdown(f"**{len(procedures)} procedure(s) found**")
st.divider()

# ---------------------------------------------------------------------------
# Procedure cards
# ---------------------------------------------------------------------------
for proc in procedures:
    proc_id = proc["id"]
    title = proc["title"]
    jurisdiction = proc["jurisdiction"]
    domain = proc["legal_domain"].replace("-", " ").title()
    step_count = proc.get("step_count", "?")
    description = proc.get("description", "")

    with st.expander(
        f"⚖️ **{title}** — {jurisdiction} / {domain} ({step_count} steps)",
        expanded=False,
    ):
        st.markdown(description)
        st.divider()

        # Load full procedure on first expand
        detail_key = f"proc_detail_{proc_id}"
        if detail_key not in st.session_state:
            try:
                st.session_state[detail_key] = get_client().get_workflow(proc_id)
            except LexaraAPIError as exc:
                st.error(f"Could not load procedure detail: {exc.detail}")
                continue

        detail = st.session_state[detail_key]
        steps = detail.get("steps", [])

        # Timeline display
        for step in steps:
            phase = step.get("phase", "")
            phase_icon = _PHASE_COLORS.get(phase, "⚪")
            step_num = step.get("step_number", "?")
            step_title = step.get("title", "")
            step_desc = step.get("description", "")
            actions = step.get("actions", [])
            deadlines = step.get("deadlines", {})
            forms = step.get("forms", [])
            agency = step.get("agency", "")
            self_help = step.get("self_help_possible", True)

            with st.container():
                st.markdown(
                    f"{phase_icon} **Step {step_num}: {step_title}** "
                    f"&nbsp;&nbsp; `{phase.replace('-', ' ').upper()}`"
                )
                st.markdown(step_desc)

                meta_col1, meta_col2, meta_col3 = st.columns(3)
                with meta_col1:
                    deadline_days = deadlines.get("response_days")
                    if deadline_days is not None:
                        day_type = "business" if deadlines.get("excludes_weekends") else "calendar"
                        st.markdown(f"⏰ **Deadline:** {deadline_days} {day_type} days")
                    if deadlines.get("notes"):
                        st.caption(deadlines["notes"])
                with meta_col2:
                    if forms:
                        st.markdown("📋 **Forms:** " + ", ".join(f"`{f}`" for f in forms))
                with meta_col3:
                    if agency and agency != "N/A":
                        st.markdown(f"🏛️ **Agency:** {agency}")
                    sh_label = "✅ Self-help possible" if self_help else "⚠️ Attorney recommended"
                    st.markdown(sh_label)

                if actions:
                    with st.expander("Actions", expanded=False):
                        for action in actions:
                            st.markdown(f"→ {action}")

                st.markdown("---")

        # Start guided chat button
        if st.button(
            f"💬 Start guided chat about this procedure",
            key=f"start_chat_{proc_id}",
            type="primary",
        ):
            st.session_state["pending_procedure_id"] = proc_id
            st.session_state["pending_procedure_title"] = title
            # Pre-populate a first message
            opening = f"I need help with: {title}. Can you guide me through the process?"
            st.session_state["messages"] = []
            st.session_state["session_id"] = None
            st.switch_page("pages/1_Chat.py")
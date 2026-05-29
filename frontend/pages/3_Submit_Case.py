"""
LexAra — Submit a Case (Page 3)
Help others by contributing your anonymized legal case.
"""
import streamlit as st

from utils.api_client import LexaraAPIError, get_client
from utils.session_state import init_session_state

init_session_state()

st.title("📝 Submit a Case")
st.caption(
    "Help others by sharing your legal experience. "
    "All submissions are automatically anonymized before being used to assist other users."
)

# Privacy explanation
with st.expander("🔒 What gets anonymized? What gets kept?", expanded=False):
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**🚫 Removed (anonymized):**")
        st.markdown(
            "- Your name and all personal names\n"
            "- Specific addresses and locations\n"
            "- Employer/landlord names\n"
            "- Phone numbers and email addresses\n"
            "- Social Security / ID numbers\n"
            "- Case/docket numbers\n"
        )
    with col2:
        st.markdown("**✅ Kept (legally material):**")
        st.markdown(
            "- Dates and timelines\n"
            "- Dollar amounts\n"
            "- Legal procedures followed\n"
            "- Outcome type (won/settled/lost)\n"
            "- Jurisdiction and legal domain\n"
            "- Steps taken and effectiveness\n"
        )

st.divider()

JURISDICTIONS = ["CA", "NY", "TX", "FL", "IL", "PA", "OH", "GA", "WA", "AZ", "federal"]
LEGAL_DOMAINS = [
    "tenant-rights", "employment", "small-claims",
    "consumer", "immigration", "family", "other",
]
CASE_TYPES_BY_DOMAIN: dict[str, list[str]] = {
    "tenant-rights": ["eviction defense", "security deposit", "habitability", "lease dispute", "other"],
    "employment": ["wrongful termination", "wage theft", "overtime", "discrimination", "harassment", "other"],
    "small-claims": ["property damage", "unpaid debt", "contract breach", "consumer dispute", "other"],
    "consumer": ["debt collection", "warranty", "fraud", "refund dispute", "other"],
    "immigration": ["visa", "asylum", "DACA", "deportation defense", "other"],
    "family": ["divorce", "custody", "child support", "domestic violence", "other"],
    "other": ["general civil", "other"],
}

# ---------------------------------------------------------------------------
# Form
# ---------------------------------------------------------------------------
with st.form("case_submission_form", clear_on_submit=False):
    st.subheader("Case Details")

    col1, col2 = st.columns(2)
    with col1:
        jurisdiction = st.selectbox("Jurisdiction *", JURISDICTIONS)
    with col2:
        legal_domain = st.selectbox("Legal Domain *", LEGAL_DOMAINS)

    case_types = CASE_TYPES_BY_DOMAIN.get(legal_domain, ["other"])
    case_type = st.selectbox("Case Type", case_types)

    situation_summary = st.text_area(
        "Situation Summary * (100–2000 characters)",
        placeholder=(
            "Describe your situation in general terms. "
            "Names, addresses, and identifying info will be automatically removed. "
            "Example: 'My landlord gave me a 3-day pay or quit notice after I paid my rent. "
            "I had proof of payment...'"
        ),
        height=150,
        max_chars=2000,
    )

    st.divider()
    st.subheader("Steps Taken")
    st.caption("What actions did you take? Add each step separately.")

    # Dynamic step fields (up to 8)
    if "num_steps" not in st.session_state:
        st.session_state["num_steps"] = 1

    steps_data = []
    for i in range(st.session_state["num_steps"]):
        with st.expander(f"Step {i+1}", expanded=(i == 0)):
            action = st.text_area(
                "Action taken",
                key=f"step_action_{i}",
                placeholder="e.g. Sent certified letter to landlord demanding repairs",
            )
            effective = st.checkbox("Was this effective?", key=f"step_effective_{i}")
            steps_data.append({"step_number": i + 1, "action": action, "was_effective": effective})

    add_step = st.form_submit_button("➕ Add Another Step", type="secondary")
    if add_step:
        st.session_state["num_steps"] = min(8, st.session_state["num_steps"] + 1)
        st.rerun()

    st.divider()
    st.subheader("Outcome")

    outcome = st.radio(
        "What was the outcome? *",
        options=["won", "settled", "lost", "dismissed", "ongoing"],
        format_func=lambda x: {
            "won": "✅ Won",
            "settled": "🟡 Settled",
            "lost": "❌ Lost",
            "dismissed": "🔵 Dismissed",
            "ongoing": "⏳ Still ongoing",
        }[x],
        horizontal=True,
    )

    outcome_summary = st.text_area(
        "Outcome summary (optional)",
        placeholder="Brief description of the result. e.g. 'Judge ruled in my favor; landlord required to return full deposit.'",
        max_chars=1000,
    )

    st.divider()
    consent = st.checkbox(
        "✅ I consent to this case being anonymized and used to help others navigate similar legal situations. "
        "I understand that all personal identifying information will be removed.",
        value=False,
    )

    submitted = st.form_submit_button("📤 Submit Case", type="primary")

# ---------------------------------------------------------------------------
# Submission handling
# ---------------------------------------------------------------------------
if submitted:
    errors = []
    if len(situation_summary.strip()) < 100:
        errors.append("Situation summary must be at least 100 characters.")
    if not consent:
        errors.append("You must consent to anonymization to submit.")
    valid_steps = [s for s in steps_data if s["action"].strip()]
    if not valid_steps:
        errors.append("Please add at least one step you took.")

    if errors:
        for err in errors:
            st.error(err)
    else:
        case_payload = {
            "jurisdiction": jurisdiction,
            "legal_domain": legal_domain,
            "case_type": case_type,
            "situation_summary": situation_summary.strip(),
            "key_facts": [],
            "timeline": [],
            "steps": [
                {
                    "step_number": s["step_number"],
                    "action": s["action"].strip(),
                    "outcome": None,
                    "deadline_days": None,
                    "was_effective": s["was_effective"],
                    "documents_used": [],
                }
                for s in valid_steps
            ],
            "outcome": outcome,
            "outcome_summary": outcome_summary.strip() or None,
            "outcome_date": None,
            "settlement_amount": None,
            "source_type": "user_submission",
            "consent_to_anonymize": True,
        }

        with st.spinner("Submitting your case…"):
            try:
                result = get_client().submit_case(case_payload)
                st.success(
                    f"✅ **Thank you!** Your case has been submitted (ID: `{result.get('case_id')}`). "
                    f"\n\n{result.get('message', '')}"
                )
                st.session_state["num_steps"] = 1
            except LexaraAPIError as exc:
                st.error(f"Submission failed: {exc.detail}")
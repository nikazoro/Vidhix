"""
LexAra — AI Legal Assistant
Main Streamlit entry point (multipage).
"""
import streamlit as st

from utils.session_state import init_session_state

# ---------------------------------------------------------------------------
# Page config — must be the first Streamlit call
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="LexAra — Legal Assistant",
    page_icon="⚖️",
    layout="wide",
    initial_sidebar_state="expanded",
)

init_session_state()

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
JURISDICTIONS = [
    "CA", "NY", "TX", "FL", "IL", "PA", "OH", "GA", "WA", "AZ", "federal"
]

with st.sidebar:
    st.image("https://placehold.co/200x60?text=⚖️+LexAra", width=200)
    st.markdown("### Settings")

    jurisdiction = st.selectbox(
        "Jurisdiction",
        options=JURISDICTIONS,
        index=JURISDICTIONS.index(st.session_state.get("jurisdiction", "CA")),
        help="Select the US state or federal jurisdiction for your legal question.",
    )
    if jurisdiction != st.session_state.get("jurisdiction"):
        st.session_state["jurisdiction"] = jurisdiction
        # Reset backend session when jurisdiction changes
        st.session_state["session_id"] = None
        st.session_state["messages"] = []

    st.divider()
    st.markdown("### About LexAra")
    st.markdown(
        """
LexAra is an AI-powered legal information assistant that helps you:

- 📋 **Understand your rights** in plain language
- 🗺️ **Navigate legal procedures** step by step
- 📄 **Analyze legal documents** you upload
- 🔍 **Learn from similar past cases**
- ⚠️ **Know when to consult an attorney**

Select a page from the navigation above to get started.
        """
    )

    st.divider()
    st.warning(
        "⚠️ **Disclaimer**: LexAra provides legal *information*, "
        "not legal *advice*. Nothing on this platform creates an "
        "attorney-client relationship. Laws vary by jurisdiction. "
        "Always consult a licensed attorney for your specific situation.",
        icon="⚖️",
    )

    # Backend health indicator
    st.divider()
    with st.expander("🔧 System Status", expanded=False):
        if st.button("Check Status", key="health_btn"):
            from utils.api_client import get_client
            health = get_client().health_check()
            status_color = "🟢" if health.get("status") == "ok" else "🔴"
            st.write(f"{status_color} Overall: **{health.get('status', 'unknown')}**")
            for svc in ["db", "qdrant", "ollama", "redis"]:
                val = health.get(svc, "unknown")
                icon = "🟢" if val == "ok" else "🔴"
                st.write(f"{icon} {svc}: {val}")

# ---------------------------------------------------------------------------
# Page navigation
# ---------------------------------------------------------------------------
pages = [
    st.Page("pages/1_Chat.py", title="Chat", icon="💬"),
    st.Page("pages/2_Upload_Document.py", title="Upload Document", icon="📄"),
    st.Page("pages/3_Submit_Case.py", title="Submit Case", icon="📝"),
    st.Page("pages/4_Procedures.py", title="Browse Procedures", icon="🗺️"),
]

pg = st.navigation(pages)
pg.run()
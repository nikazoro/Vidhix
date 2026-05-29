"""
Right-panel source attribution display.
"""
import streamlit as st


def render_sources_panel(sources: list[dict]) -> None:
    """Render a list of retrieved sources in the sidebar panel."""
    if not sources:
        st.caption("No sources retrieved yet.")
        return

    st.markdown(f"**{len(sources)} source(s) used**")
    for i, src in enumerate(sources, start=1):
        with st.expander(f"[{i}] {src.get('title', 'Unknown')[:50]}", expanded=False):
            col1, col2 = st.columns(2)
            with col1:
                st.caption("Jurisdiction")
                st.write(src.get("jurisdiction", "—"))
            with col2:
                st.caption("Domain")
                st.write(src.get("legal_domain", "—"))
            if src.get("document_type"):
                st.caption("Document type")
                st.write(src["document_type"])
            if src.get("source_authority"):
                st.caption("Authority")
                st.write(src["source_authority"])
            st.caption("Relevance score")
            score = src.get("score", 0.0)
            st.progress(min(1.0, max(0.0, (score + 5) / 10)), text=f"{score:.3f}")
            st.caption("Excerpt")
            st.markdown(f"> {src.get('chunk_text', '')[:300]}…")
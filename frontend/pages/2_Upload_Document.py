"""
LexAra — Document Upload & Analysis (Page 2)
"""
import time

import streamlit as st

from utils.api_client import LexaraAPIError, get_client
from utils.session_state import ensure_backend_session, init_session_state

init_session_state()

st.title("📄 Upload Legal Document")
st.caption(
    "Upload a legal document (eviction notice, lease, pay stub, contract, etc.) "
    "for AI-powered analysis. Supported formats: PDF, DOCX, PNG, JPEG."
)

MAX_SIZE_MB = 20

# ---------------------------------------------------------------------------
# Upload widget
# ---------------------------------------------------------------------------
uploaded_file = st.file_uploader(
    "Choose a document",
    type=["pdf", "docx", "png", "jpg", "jpeg"],
    help=f"Maximum file size: {MAX_SIZE_MB} MB",
)

if uploaded_file is not None:
    file_size_mb = len(uploaded_file.getvalue()) / 1024 / 1024

    # Size guard
    if file_size_mb > MAX_SIZE_MB:
        st.error(f"File is too large ({file_size_mb:.1f} MB). Maximum allowed: {MAX_SIZE_MB} MB.")
        st.stop()

    col1, col2, col3 = st.columns(3)
    col1.metric("File name", uploaded_file.name)
    col2.metric("Size", f"{file_size_mb:.2f} MB")
    col3.metric("Type", uploaded_file.type or "unknown")

    session_id = ensure_backend_session()

    if st.button("🔍 Analyze Document", type="primary"):
        mime_map = {
            "pdf": "application/pdf",
            "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "png": "image/png",
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
        }
        ext = uploaded_file.name.rsplit(".", 1)[-1].lower()
        mime_type = mime_map.get(ext, uploaded_file.type or "application/octet-stream")

        with st.spinner("Uploading document…"):
            try:
                upload_result = get_client().upload_document(
                    file_bytes=uploaded_file.getvalue(),
                    filename=uploaded_file.name,
                    mime_type=mime_type,
                    session_id=session_id,
                )
            except LexaraAPIError as exc:
                st.error(f"Upload failed: {exc.detail}")
                st.stop()

        doc_id = upload_result.get("document_id")
        if not doc_id:
            st.error("Upload succeeded but no document ID was returned.")
            st.stop()
        st.info(f"✅ Document uploaded (ID: `{doc_id}`). Analyzing…")

        # Poll for analysis
        progress = st.progress(0, text="Processing…")
        analysis = None
        max_polls = 30  # 60 seconds max

        for i in range(max_polls):
            time.sleep(2)
            progress.progress(
                min(0.95, (i + 1) / max_polls),
                text=f"Processing… ({(i + 1) * 2}s)",
            )
            try:
                result = get_client().poll_document_analysis(doc_id)
                if result.get("ocr_status") == "completed":
                    analysis = result
                    break
                elif result.get("ocr_status") == "failed":
                    st.error("Document processing failed. Please try again or use a clearer scan.")
                    st.stop()
            except LexaraAPIError as exc:
                st.error(f"Could not retrieve analysis: {exc.detail}")
                st.stop()

        progress.empty()

        if not analysis:
            st.warning(
                "Analysis is taking longer than expected. "
                f"You can poll manually at: `/api/v1/documents/{doc_id}/analysis`"
            )
            st.stop()

        # ---------------------------------------------------------------------------
        # Display analysis results
        # ---------------------------------------------------------------------------
        st.success("✅ Analysis complete!")
        st.divider()

        # Document type badge
        doc_type = analysis.get("document_type") or "Unknown"
        jurisdiction = analysis.get("jurisdiction") or "—"
        legal_domain = analysis.get("legal_domain") or "—"

        badge_col, jur_col, dom_col = st.columns(3)
        badge_col.metric("Document Type", doc_type.replace("_", " ").title())
        jur_col.metric("Jurisdiction", jurisdiction)
        dom_col.metric("Legal Domain", legal_domain.replace("-", " ").title())

        st.divider()

        # Key dates table
        key_dates = analysis.get("key_dates", [])
        parties = analysis.get("parties", [])
        amounts = analysis.get("amounts", [])

        col_l, col_r = st.columns(2)

        with col_l:
            st.subheader("📅 Key Dates")
            if key_dates:
                for d in key_dates[:10]:
                    st.markdown(f"• {d.get('date', d)}")
            else:
                st.caption("No dates extracted.")

            st.subheader("💰 Amounts")
            if amounts:
                for a in amounts[:10]:
                    st.markdown(f"• {a.get('amount', a)}")
            else:
                st.caption("No amounts extracted.")

        with col_r:
            st.subheader("👥 Parties")
            if parties:
                for p in parties[:10]:
                    st.markdown(f"• {p}")
            else:
                st.caption("No parties extracted.")

            entities = analysis.get("extracted_entities", {})
            statutes = entities.get("statutes", [])
            if statutes:
                st.subheader("⚖️ Statutes Referenced")
                for s in statutes[:8]:
                    st.markdown(f"• `{s}`")

        st.divider()

        # Use in chat button
        if st.button("💬 Use this document in Chat", type="primary"):
            st.session_state["pending_document_id"] = doc_id
            st.session_state["pending_document_name"] = uploaded_file.name
            st.switch_page("pages/1_Chat.py")
"""
Similar cases display panel component.
"""
import streamlit as st


def render_cases_panel(cases: list[dict]) -> None:
    """
    Render up to 3 similar past cases with outcome badges.
    Each case is expandable to show steps taken.
    """
    if not cases:
        st.caption("No similar past cases found.")
        return

    st.markdown(f"**{len(cases)} similar case(s)**")
    for i, case in enumerate(cases, start=1):
        badge = case.get("outcome_badge", "❓")
        summary = case.get("situation_summary", "")[:120]
        score = case.get("similarity_score", 0.0)
        outcome_summary = case.get("outcome_summary", "")
        steps = case.get("steps_taken", "")

        with st.expander(
            f"Case {i} {badge} — {summary}…",
            expanded=False,
        ):
            col1, col2 = st.columns([3, 1])
            with col1:
                st.caption("Situation (anonymized)")
                st.write(case.get("situation_summary", "—"))
            with col2:
                st.caption("Similarity")
                st.metric("", f"{score:.0%}")

            if steps:
                st.caption("Steps taken")
                st.markdown(steps)

            if outcome_summary:
                st.caption("Outcome")
                st.info(outcome_summary)
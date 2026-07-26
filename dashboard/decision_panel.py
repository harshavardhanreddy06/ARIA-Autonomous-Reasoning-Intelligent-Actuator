"""
dashboard/decision_panel.py
==============================
LLM decision log. This is a log/table, not a chart — a list of reasoning
entries has no magnitude/identity/polarity to plot. Real vs synthetic
entries use the fixed status palette (never the categorical series
colors) and always pair color with an icon + label, never color alone.
"""
import streamlit as st

from dashboard.data import load_decisions

STATUS_GOOD = "#0ca30c"
STATUS_WARNING = "#fab219"


def render_decision_panel(conn, limit: int | None = None) -> None:
    st.subheader("Decision Log")

    df = load_decisions(conn)
    if df.empty:
        st.info("No decisions logged yet — run main.py first.")
        return

    total = len(df)
    auto = int(df["auto_generated"].sum())
    llm_authored = total - auto
    col1, col2, col3 = st.columns(3)
    col1.metric("Total decisions", total)
    col2.metric("LLM-authored", f"{llm_authored} ({llm_authored/total*100:.1f}%)")
    col3.metric("Auto-generated (fallback)", f"{auto} ({auto/total*100:.1f}%)")

    st.caption(
        "Auto-generated entries are written automatically when the model didn't call "
        "log_decision this cycle — shown honestly rather than hidden (see fallback_handler.py). "
        f"Showing all {total} decisions below, most recent first."
    )

    rows = df if limit is None else df.head(limit)
    for _, row in rows.iterrows():
        if row["auto_generated"]:
            badge = ":orange[● AUTO]"
        else:
            badge = ":green[● LLM]"
        with st.expander(f"{badge}  Timestep {row['timestep']} — {row['reasoning'][:80]}"):
            st.write(row["reasoning"])
            if row["actions_taken"]:
                st.write("**Actions:**", ", ".join(row["actions_taken"]))
            if row["fallback_used"]:
                st.warning("Fallback setpoints used this cycle (LLM call failed).")

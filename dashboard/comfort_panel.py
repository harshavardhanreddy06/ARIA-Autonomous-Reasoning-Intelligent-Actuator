"""
dashboard/comfort_panel.py
============================
PMV zone x time heatmap. PMV is signed (cold/neutral/hot) so this uses a
proper diverging scale — blue/red poles with a neutral gray midpoint at
PMV=0 — never a rainbow, never a sequential single-hue ramp for signed data.
"""
import plotly.graph_objects as go
import streamlit as st

from dashboard.data import load_timestep_data

# Diverging: blue (cold) <-> gray (neutral, PMV=0) <-> red (hot)
DIVERGING_SCALE = [
    [0.0, "#2a78d6"],
    [0.5, "#f0efec"],
    [1.0, "#e34948"],
]
PMV_RANGE = 2.0  # symmetric zmin/zmax so 0 lands exactly on the gray midpoint


def render_comfort_panel(conn, run_type: str = "aria") -> None:
    st.subheader(f"Comfort — PMV by zone over time ({run_type})")

    df = load_timestep_data(conn, run_type)
    if df.empty:
        st.info("No comfort data yet — run main.py first.")
        return

    pivot = df.pivot_table(index="zone_id", columns="datetime", values="pmv")
    zone_labels = [f"Zone {z}" for z in pivot.index]

    fig = go.Figure(data=go.Heatmap(
        z=pivot.values,
        x=pivot.columns,
        y=zone_labels,
        colorscale=DIVERGING_SCALE,
        zmin=-PMV_RANGE, zmax=PMV_RANGE, zmid=0,
        colorbar=dict(title="PMV"),
        hovertemplate="%{y}<br>%{x}<br>PMV=%{z:.2f}<extra></extra>",
    ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        xaxis_title="Time", yaxis_title="",
        margin=dict(l=10, r=10, t=10, b=10),
    )
    st.plotly_chart(fig, width="stretch")

    violations = df[(df["pmv"] < -0.5) | (df["pmv"] > 0.5)]
    occupied_violations = violations[violations["occ_fraction"] > 0]
    total_occupied_rows = (df["occ_fraction"] > 0).sum()
    pct = (len(occupied_violations) / total_occupied_rows * 100) if total_occupied_rows else 0
    st.metric("Occupied-zone PMV compliance", f"{100 - pct:.1f}%",
              help="Share of occupied zone-timesteps with PMV in [-0.5, +0.5]")

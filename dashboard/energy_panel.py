"""
dashboard/energy_panel.py
===========================
ARIA vs baseline building electricity demand over time. Single y-axis
(kW), never dual-axis; two named series (ARIA, Baseline), each a fixed
color chosen for colorblind-safe separation.
"""
import plotly.graph_objects as go
import streamlit as st

from dashboard.data import building_level_series, load_timestep_data

COLOR_ARIA = "#2a78d6"      # categorical slot 1 (blue)
COLOR_BASELINE = "#eb6834"  # categorical slot 2 (orange)


def render_energy_panel(conn) -> None:
    st.subheader("Energy — ARIA vs Baseline")

    aria_df = building_level_series(load_timestep_data(conn, "aria"))
    baseline_df = building_level_series(load_timestep_data(conn, "baseline"))

    if aria_df.empty and baseline_df.empty:
        st.info("No energy data yet — run main.py and/or run_baseline.py first.")
        return

    fig = go.Figure()
    if not baseline_df.empty:
        fig.add_trace(go.Scatter(
            x=baseline_df["datetime"], y=baseline_df["total_demand_kw"],
            name="Baseline", mode="lines",
            line=dict(color=COLOR_BASELINE, width=2),
        ))
    if not aria_df.empty:
        fig.add_trace(go.Scatter(
            x=aria_df["datetime"], y=aria_df["total_demand_kw"],
            name="ARIA", mode="lines",
            line=dict(color=COLOR_ARIA, width=2),
        ))

    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        xaxis_title="Time", yaxis_title="Building demand (kW)",
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        margin=dict(l=10, r=10, t=10, b=10),
        hovermode="x unified",
    )
    fig.update_xaxes(gridcolor="rgba(128,128,128,0.2)")
    fig.update_yaxes(gridcolor="rgba(128,128,128,0.2)")

    st.plotly_chart(fig, width="stretch")

    if not aria_df.empty and not baseline_df.empty:
        aria_kwh = (aria_df["total_demand_kw"] * 0.25).sum()  # 15-min intervals
        baseline_kwh = (baseline_df["total_demand_kw"] * 0.25).sum()
        pct = (aria_kwh - baseline_kwh) / baseline_kwh * 100 if baseline_kwh else 0
        col1, col2, col3 = st.columns(3)
        col1.metric("Baseline total", f"{baseline_kwh:.1f} kWh")
        col2.metric("ARIA total", f"{aria_kwh:.1f} kWh")
        col3.metric("Change", f"{pct:+.1f}%")

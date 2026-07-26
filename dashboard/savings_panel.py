"""
dashboard/savings_panel.py
=============================
Cumulative kWh / USD / CO2 saved — presented as stat tiles rather than a
chart, since each is a single headline number rather than a series.
USD and CO2 are estimates from assumed constants (documented below, since
neither an electricity rate nor a flat carbon factor exists in
settings.yaml) — labeled as estimates, not measured values.
"""
import streamlit as st

from carbon.grid_intensity import MOCK_PROFILE_BY_HOUR
from dashboard.data import building_level_series, load_timestep_data

ASSUMED_ELECTRICITY_RATE_USD_PER_KWH = 0.15  # US commercial average, assumed
AVG_GRID_INTENSITY_GCO2_PER_KWH = sum(MOCK_PROFILE_BY_HOUR.values()) / len(MOCK_PROFILE_BY_HOUR)


def render_savings_panel(conn) -> None:
    st.subheader("Savings vs Baseline")

    aria_df = building_level_series(load_timestep_data(conn, "aria"))
    baseline_df = building_level_series(load_timestep_data(conn, "baseline"))

    if aria_df.empty or baseline_df.empty:
        st.info("Need both an ARIA run and a baseline run to compute savings.")
        return

    aria_kwh = (aria_df["total_demand_kw"] * 0.25).sum()
    baseline_kwh = (baseline_df["total_demand_kw"] * 0.25).sum()
    kwh_saved = baseline_kwh - aria_kwh
    usd_saved = kwh_saved * ASSUMED_ELECTRICITY_RATE_USD_PER_KWH
    co2_saved_kg = kwh_saved * AVG_GRID_INTENSITY_GCO2_PER_KWH / 1000.0

    col1, col2, col3 = st.columns(3)
    col1.metric("Energy saved", f"{kwh_saved:.1f} kWh",
                f"{kwh_saved/baseline_kwh*100:+.1f}%" if baseline_kwh else None)
    col2.metric("Est. cost saved", f"${usd_saved:.2f}",
                help=f"Assumes ${ASSUMED_ELECTRICITY_RATE_USD_PER_KWH:.2f}/kWh")
    col3.metric("Est. CO2 avoided", f"{co2_saved_kg:.1f} kg",
                help=f"Assumes {AVG_GRID_INTENSITY_GCO2_PER_KWH:.0f} gCO2/kWh grid average")

    st.caption(
        "Cost and CO2 figures are estimates from assumed constants "
        f"(${ASSUMED_ELECTRICITY_RATE_USD_PER_KWH}/kWh, "
        f"{AVG_GRID_INTENSITY_GCO2_PER_KWH:.0f} gCO2/kWh average) — energy (kWh) is the only measured value."
    )

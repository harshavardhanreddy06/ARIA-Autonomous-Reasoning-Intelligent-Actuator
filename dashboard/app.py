"""
dashboard/app.py
==================
Streamlit main. Run: streamlit run dashboard/app.py
"""
import sys
import time
from pathlib import Path

# Streamlit only adds this script's own directory to sys.path, not the
# project root — unlike running `python3 main.py` from the project root,
# where the cwd itself lands on sys.path. Without this, every absolute
# import below (from config.env_loader, from dashboard.*) 404s with
# ModuleNotFoundError the moment `streamlit run` launches it.
sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st  # noqa: E402

from config.env_loader import load_settings  # noqa: E402
from dashboard.comfort_panel import render_comfort_panel
from dashboard.data import get_connection
from dashboard.decision_panel import render_decision_panel
from dashboard.energy_panel import render_energy_panel
from dashboard.savings_panel import render_savings_panel

settings = load_settings()

st.set_page_config(page_title="ARIA — Eco-Loop Building Agent", layout="wide")
st.title("ARIA — Eco-Loop Building Agent")

conn = get_connection(settings["database"]["path"])

render_energy_panel(conn)
st.divider()
render_comfort_panel(conn)
st.divider()
render_savings_panel(conn)
st.divider()
render_decision_panel(conn)

if st.checkbox("Auto-refresh", value=False):
    time.sleep(settings["dashboard"]["refresh_interval_seconds"])
    st.rerun()

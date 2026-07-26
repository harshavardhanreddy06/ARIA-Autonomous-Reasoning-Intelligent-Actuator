#!/bin/bash
# scripts/run_aria.sh
# Run full ARIA simulation (LLM agent + EnergyPlus). This does NOT launch
# the dashboard — run `streamlit run dashboard/app.py` separately (before,
# during, or after) to watch aria.db fill in live.
# Runs correctly from any working directory.
set -e
cd "$(dirname "${BASH_SOURCE[0]}")/.."
source .venv/bin/activate 2>/dev/null || true
echo "Starting ARIA system (agent + EnergyPlus)..."
python3 main.py "$@"

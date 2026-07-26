#!/bin/bash
# scripts/run_baseline.sh
# Run EnergyPlus baseline simulation (no AI control).
# Runs correctly from any working directory.
set -e
cd "$(dirname "${BASH_SOURCE[0]}")/.."
source .venv/bin/activate 2>/dev/null || true
echo "Starting ARIA baseline simulation..."
python3 run_baseline.py "$@"

"""
config/env_loader.py
====================
Single source of truth for the EnergyPlus install path.

Works for ALL launch methods:
  - terminal (source .venv/bin/activate && python main.py)
  - subprocess (subprocess.run(['python', 'run_baseline.py']))
  - Streamlit  (streamlit run dashboard/app.py)
  - double-click / cron / CI

Priority order:
  1. ENERGYPLUS_DIR environment variable  (set manually or by CI)
  2. .aria_env file in project root       (written by setup.sh — preferred)
  3. Hardcoded macOS default              (last resort)
"""

import os
import sys
from pathlib import Path

# Anchor for every relative path in settings.yaml — resolving against this
# instead of the process's current working directory means main.py,
# run_baseline.py, the dashboard, and the scripts/ launchers all behave
# identically regardless of where they're invoked from (project root,
# a subdirectory, an IDE run config, or a cron job with an unset cwd).
PROJECT_ROOT = Path(__file__).parent.parent

# .aria_env lives at the project root
_ENV_FILE = PROJECT_ROOT / ".aria_env"
_DEFAULT_EP_DIR = "/Applications/EnergyPlus-26-1-0"

# settings.yaml keys that hold filesystem paths and must be resolved
# relative to PROJECT_ROOT rather than left for the OS to interpret
# against whatever the current working directory happens to be.
_PATH_KEYS = (
    ("energyplus", "model_path"),
    ("energyplus", "weather_path"),
    ("energyplus", "output_dir"),
    ("energyplus", "baseline_output_dir"),
    ("database", "path"),
)


def load_ep_dir() -> str:
    """Return the EnergyPlus install directory (does NOT verify it exists)."""
    # 1. Explicit environment variable
    if ep := os.environ.get("ENERGYPLUS_DIR"):
        return ep.strip()

    # 2. .aria_env file written by setup.sh
    if _ENV_FILE.exists():
        for line in _ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line.startswith("ENERGYPLUS_DIR="):
                return line.split("=", 1)[1].strip()

    # 3. Hardcoded macOS default
    return _DEFAULT_EP_DIR


def get_ep_dir() -> str:
    """Return the EnergyPlus install directory, raising if it doesn't exist."""
    ep_dir = load_ep_dir()
    if not os.path.isdir(ep_dir):
        raise FileNotFoundError(
            f"\nEnergyPlus not found at: {ep_dir}\n"
            f"Run: bash scripts/setup.sh\n"
            f"Or set: export ENERGYPLUS_DIR=/path/to/energyplus"
        )
    return ep_dir


def inject_ep_path() -> str:
    """
    Adds EnergyPlus directory to sys.path so pyenergyplus can be imported.
    Returns the directory path.
    Call this at the TOP of any file that imports pyenergyplus.

    Usage:
        from config.env_loader import inject_ep_path
        inject_ep_path()
        from pyenergyplus.api import EnergyPlusAPI
    """
    ep_dir = get_ep_dir()
    if ep_dir not in sys.path:
        sys.path.insert(0, ep_dir)
    return ep_dir


def load_settings() -> dict:
    """
    Load settings.yaml, resolve every path field to an absolute path
    anchored at the project root, and ensure the EnergyPlus output
    directories exist (they're gitignored, so a fresh clone won't have
    them, and EnergyPlus's -d flag requires the directory to pre-exist).
    """
    import yaml
    settings_path = Path(__file__).parent / "settings.yaml"
    with open(settings_path, "r") as f:
        settings = yaml.safe_load(f)

    for section, key in _PATH_KEYS:
        raw = settings[section][key]
        settings[section][key] = str((PROJECT_ROOT / raw).resolve()) if not os.path.isabs(raw) else raw

    os.makedirs(settings["energyplus"]["output_dir"], exist_ok=True)
    os.makedirs(settings["energyplus"]["baseline_output_dir"], exist_ok=True)

    return settings

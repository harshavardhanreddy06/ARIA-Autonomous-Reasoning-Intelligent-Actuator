"""
run_baseline.py
================
Standalone baseline run — no AI. Runs the real 7-day period with zones
following their native DualSetpoint schedules untouched, logging every
timestep to the database for later comparison against the ARIA run.
Run: python3 run_baseline.py
"""
from config.env_loader import load_settings
from data.database import Database
from simulation.baseline_env import run_baseline


def main():
    settings = load_settings()
    database = Database(settings["database"]["path"])

    print("=" * 55)
    print("ARIA — Baseline Run (no AI control)")
    print("=" * 55)

    env = run_baseline(settings, database, print_snapshots=True)

    print(f"\nBaseline run complete: {env._tick_count} timesteps logged to {settings['database']['path']}")


if __name__ == "__main__":
    main()

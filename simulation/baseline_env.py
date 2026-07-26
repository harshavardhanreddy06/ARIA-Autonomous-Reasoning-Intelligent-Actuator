"""
simulation/baseline_env.py
============================
Runs the building with NO ARIA control — zones follow their native
DualSetpoint schedules untouched. Reuses EnergyPlusEnv as-is (nothing
populates state_manager's pending writes, so _apply_pending_writes is a
no-op every tick); this just logs every real timestep to timestep_data
with run_type='baseline' for later comparison against the ARIA run.
"""
import logging

from data.database import Database
from simulation.energyplus_env import EnergyPlusEnv

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("ARIA.baseline_env")


def run_baseline(
    settings: dict,
    database: Database,
    print_snapshots: bool = False,
    max_ticks: int | None = None,
) -> EnergyPlusEnv:
    baseline_settings = {
        **settings,
        "energyplus": {**settings["energyplus"], "output_dir": settings["energyplus"]["baseline_output_dir"]},
    }
    env = EnergyPlusEnv(settings=baseline_settings, print_snapshots=print_snapshots, max_ticks=max_ticks)

    def on_timestep(timestep, snapshot):
        database.insert_timestep_snapshot("baseline", timestep, snapshot)

    env.on_timestep = on_timestep

    exit_code = env.run()
    logger.info(f"Baseline run complete. Exit code: {exit_code}. Ticks: {env._tick_count}")
    return env

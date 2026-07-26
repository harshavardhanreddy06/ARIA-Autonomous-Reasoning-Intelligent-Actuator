"""
main.py
========
Master entry point: full ARIA run (real EnergyPlus + real LLM agent).
Each real timestep: the agent decision cycle runs (populating pending
actuator writes), then the sensor snapshot is logged to timestep_data
before EnergyPlusEnv applies those pending writes.
Run: python3 main.py
"""
import logging

from agent.aria_agent import AriaAgent
from agent.fallback_handler import FallbackHandler
from agent.tool_registry import ToolRegistry
from carbon.grid_intensity import get_carbon_intensity
from config.env_loader import load_settings
from data.database import Database
from simulation.energyplus_env import EnergyPlusEnv

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("ARIA.main")


def run_aria(settings: dict | None = None, print_snapshots: bool = True, max_ticks: int | None = None) -> EnergyPlusEnv:
    settings = settings or load_settings()
    database = Database(settings["database"]["path"])
    fallback = FallbackHandler()

    env = EnergyPlusEnv(settings=settings, print_snapshots=print_snapshots, max_ticks=max_ticks)
    tool_registry = ToolRegistry(env.state_manager, database)
    agent = AriaAgent(tool_registry, fallback, settings)

    def on_timestep(timestep, snapshot):
        carbon = get_carbon_intensity(snapshot["sim_hour"], settings)
        agent.run_decision_cycle(timestep, snapshot, carbon)
        database.insert_timestep_snapshot("aria", timestep, snapshot)

    env.on_timestep = on_timestep

    exit_code = env.run()
    stats = database.get_decision_stats()
    logger.info(f"ARIA run complete. Exit code: {exit_code}. Ticks: {env._tick_count}. Decisions: {stats}")
    return env


if __name__ == "__main__":
    print("=" * 55)
    print("ARIA — Full Run (real LLM + real EnergyPlus)")
    print("=" * 55)
    run_aria()

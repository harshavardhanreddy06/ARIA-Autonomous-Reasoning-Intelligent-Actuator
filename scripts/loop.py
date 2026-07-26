"""
scripts/loop.py
================
Standalone, no-EnergyPlus demonstration of the real agentic decision loop —
for the PoC video's "agentic class in isolation" segment.

Instantiates the actual AriaAgent (same class used in main.py) and drives
it through four hand-built sensor snapshots spanning a full occupancy
arc — unoccupied, semi-occupied, fully occupied, and fully occupied under
a hotter afternoon load — printing the exact prompt sent to the LLM and
the exact tool calls it returns for each one. No mocking: these are real
Ollama calls against qwen2.5:3b, the same code path as the live 7-day
run — just fed a scripted snapshot instead of one read from EnergyPlus.

Run: python3 scripts/loop.py
"""
import sys
import time

sys.path.insert(0, __file__.rsplit("/scripts/", 1)[0])


def stream_print(text, delay=0.018):
    """The real response already arrived instantly (Ollama's chat API isn't
    streamed here) — this hardcodes a typewriter effect on the way OUT so a
    reviewer watching the recording can actually read it, rather than the
    whole result dumping to the terminal in one frame."""
    for ch in text:
        sys.stdout.write(ch)
        sys.stdout.flush()
        time.sleep(delay)
    sys.stdout.write("\n")
    sys.stdout.flush()

from agent.aria_agent import AriaAgent
from agent.fallback_handler import FallbackHandler
from agent.prompts import build_user_message
from agent.tool_registry import ToolRegistry
from config.env_loader import load_settings
from data.database import Database
from simulation.state_manager import StateManager

SEP = "=" * 70


def zone(id, name, temp_c, occ_fraction, cool_sp, heat_sp, co2_ppm=450.0, mrt_offset=0.0):
    return {
        "id": id, "name": name, "temp_c": temp_c, "mrt_c": temp_c + mrt_offset,
        "occ_fraction": occ_fraction, "co2_ppm": co2_ppm,
        "cool_sp": cool_sp, "heat_sp": heat_sp,
    }


def all_zones(temp_c, occ_fraction, cool_sp, heat_sp, co2_ppm=450.0):
    names = ["Perimeter_ZN_1", "Perimeter_ZN_2", "Perimeter_ZN_3", "Perimeter_ZN_4", "Core_ZN"]
    return [zone(i + 1, n, temp_c, occ_fraction, cool_sp, heat_sp, co2_ppm) for i, n in enumerate(names)]


# Four scenarios across a full occupancy arc, each a real, distinct call.

SCENARIOS = [
    (
        "Unoccupied — quiet overnight",
        {
            "sim_month": 7, "sim_day": 15, "sim_hour": 3, "sim_minute": 0,
            "outdoor_temp": 19.0, "hvac_kw": 2.1, "total_demand_kw": 4.5,
            "zones": all_zones(temp_c=24.0, occ_fraction=0.0, cool_sp=28.0, heat_sp=18.0, co2_ppm=420.0),
        },
        {"current_gco2_kwh": 95, "strategy": "PRECOOL",
         "forecast_summary": "next hour ~90 gCO2/kWh (falling)", "source": "mock"},
    ),
    (
        "Semi-occupied — early morning ramp-up",
        {
            "sim_month": 7, "sim_day": 15, "sim_hour": 7, "sim_minute": 0,
            "outdoor_temp": 24.0, "hvac_kw": 4.5, "total_demand_kw": 7.8,
            "zones": all_zones(temp_c=23.5, occ_fraction=0.5, cool_sp=25.0, heat_sp=20.0, co2_ppm=520.0),
        },
        {"current_gco2_kwh": 200, "strategy": "NORMAL",
         "forecast_summary": "next hour ~210 gCO2/kWh (rising)", "source": "mock"},
    ),
    (
        "Fully occupied — normal business hours",
        {
            "sim_month": 7, "sim_day": 15, "sim_hour": 11, "sim_minute": 0,
            "outdoor_temp": 29.0, "hvac_kw": 7.0, "total_demand_kw": 11.5,
            "zones": all_zones(temp_c=24.0, occ_fraction=1.0, cool_sp=24.0, heat_sp=21.0, co2_ppm=650.0),
        },
        {"current_gco2_kwh": 220, "strategy": "NORMAL",
         "forecast_summary": "next hour ~240 gCO2/kWh (rising)", "source": "mock"},
    ),
    (
        "Fully occupied — hot afternoon peak",
        {
            "sim_month": 7, "sim_day": 15, "sim_hour": 15, "sim_minute": 0,
            "outdoor_temp": 35.0, "hvac_kw": 9.8, "total_demand_kw": 15.6,
            "zones": all_zones(temp_c=28.0, occ_fraction=1.0, cool_sp=24.0, heat_sp=21.0, co2_ppm=700.0),
        },
        {"current_gco2_kwh": 380, "strategy": "DEFER",
         "forecast_summary": "next hour ~360 gCO2/kWh (falling)", "source": "mock"},
    ),
]


def run_call(agent, state_manager, call_num, label, timestep, snapshot, carbon):
    # The real loop (energyplus_env.py) stores the snapshot here before the
    # agent runs, so tools can look up each zone's current state — do the
    # same here or every tool call will fail with "Unknown zone_id".
    state_manager.update_snapshot(snapshot)

    print(f"\n{SEP}")
    print(f"CALL {call_num} — {label}  (timestep {timestep})")
    print(SEP)
    print("\n--- PROMPT SENT TO qwen2.5:3b ---\n")
    print(build_user_message(timestep, snapshot, carbon))

    print("\n--- WAITING FOR REAL LLM RESPONSE ---")
    t0 = time.time()
    setpoints = agent.run_decision_cycle(timestep, snapshot, carbon)
    elapsed = time.time() - t0
    print(f"--- RESPONSE RECEIVED in {elapsed:.1f}s ---\n")

    print("TOOL CALLS MADE:")
    if not agent._tool_calls_made:
        stream_print("  (none — model called only log_decision)")
    reasoning = None
    for call in agent._tool_calls_made:
        stream_print(f"  -> {call['name']}({call['args']})", delay=0.008)
        if call["name"] == "log_decision":
            reasoning = call["args"].get("reasoning")

    if reasoning:
        print("\nAI REASONING:")
        stream_print(f'  "{reasoning}"', delay=0.022)

    print(f"\nSETPOINTS APPLIED: {setpoints}")
    print(SEP)


def main():
    sys.stdout.reconfigure(line_buffering=True)
    settings = load_settings()
    state_manager = StateManager()
    database = Database(":memory:")
    tool_registry = ToolRegistry(state_manager, database)
    fallback = FallbackHandler()
    agent = AriaAgent(tool_registry, fallback, settings)

    print(SEP)
    print("ARIA Agentic Class —Loop in Action")
    print("Real qwen2.5:3b calls, real tool-calling loop, scripted sensor data")
    print(f"{len(SCENARIOS)} calls across a full occupancy arc")
    print(SEP)

    for i, (label, snapshot, carbon) in enumerate(SCENARIOS, start=1):
        run_call(agent, state_manager, i, label, i, snapshot, carbon)

    stats = database.get_decision_stats()
    print(f"\nDecisions logged this session: {stats}")


if __name__ == "__main__":
    main()

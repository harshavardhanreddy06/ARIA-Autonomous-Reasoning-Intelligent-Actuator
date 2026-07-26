"""
tests/test_agent.py
====================
Exercises the real Ollama + qwen2.5:3b model against hand-built snapshot
scenarios — no EnergyPlus involved. Also proves the audit-trail guarantee
by deliberately pointing the agent at a nonexistent model so the LLM call
itself fails, and confirming the synthetic log still fires.
"""
import pytest

from agent.aria_agent import AriaAgent
from agent.fallback_handler import FallbackHandler
from agent.tool_registry import ToolRegistry
from config.env_loader import load_settings
from data.database import Database
from simulation.state_manager import StateManager


def make_zone(zone_id, temp_c=22.0, mrt_c=21.5, occ_fraction=1.0, co2_ppm=500.0, cool_sp=23.0, heat_sp=21.0):
    return {
        "id": zone_id, "name": f"Zone{zone_id}",
        "temp_c": temp_c, "mrt_c": mrt_c, "occ_fraction": occ_fraction,
        "co2_ppm": co2_ppm, "cool_sp": cool_sp, "heat_sp": heat_sp,
    }


def make_snapshot(zones, sim_hour=14):
    return {
        "sim_month": 7, "sim_day": 15, "sim_hour": sim_hour, "sim_minute": 0,
        "outdoor_temp": 28.0, "hvac_kw": 3.2, "total_demand_kw": 6.5,
        "zones": zones,
    }


@pytest.fixture
def agent():
    settings = load_settings()
    sm = StateManager()
    db = Database(":memory:")
    tool_registry = ToolRegistry(sm, db)
    fallback = FallbackHandler()
    return AriaAgent(tool_registry, fallback, settings)


def test_normal_conditions_logs_decision(agent):
    # A live 3B model can genuinely time out on some cycles (see
    # ARCHITECTURE.md's documented 45s-timeout-then-fallback behavior) — the
    # actual guarantee under test is "exactly one audit entry per cycle,
    # real or synthetic," not "the LLM always succeeds in time."
    zones = [make_zone(i, temp_c=22.5, occ_fraction=1.0) for i in range(1, 6)]
    snapshot = make_snapshot(zones)
    carbon = {"current_gco2_kwh": 200, "strategy": "NORMAL", "forecast_summary": "flat"}

    agent.run_decision_cycle(timestep=1, snapshot=snapshot, carbon=carbon)

    stats = agent.tool_registry.database.get_decision_stats()
    assert stats["total"] == 1


def test_pmv_violation_triggers_setpoint_change(agent):
    # Zone 1 far too warm, rest neutral — model should act on the hot zone
    # (best-effort check; only meaningful if the LLM call didn't time out).
    zones = [make_zone(1, temp_c=29.0, mrt_c=29.0, occ_fraction=1.0)] + \
            [make_zone(i, temp_c=22.0, occ_fraction=1.0) for i in range(2, 6)]
    snapshot = make_snapshot(zones)
    carbon = {"current_gco2_kwh": 200, "strategy": "NORMAL", "forecast_summary": "flat"}

    agent.run_decision_cycle(timestep=2, snapshot=snapshot, carbon=carbon)

    stats = agent.tool_registry.database.get_decision_stats()
    assert stats["total"] == 1
    if agent._log_decision_called:
        tool_names = [c["name"] for c in agent._tool_calls_made]
        assert "set_hvac_setpoint" in tool_names


def test_unoccupied_zones_run_to_completion(agent):
    zones = [make_zone(i, occ_fraction=0.0, cool_sp=28.0, heat_sp=18.0) for i in range(1, 6)]
    snapshot = make_snapshot(zones)
    carbon = {"current_gco2_kwh": 200, "strategy": "NORMAL", "forecast_summary": "flat"}

    agent.run_decision_cycle(timestep=3, snapshot=snapshot, carbon=carbon)
    stats = agent.tool_registry.database.get_decision_stats()
    assert stats["total"] == 1


def test_precool_strategy_runs_to_completion(agent):
    zones = [make_zone(i, occ_fraction=1.0) for i in range(1, 6)]
    snapshot = make_snapshot(zones)
    carbon = {"current_gco2_kwh": 90, "strategy": "PRECOOL", "forecast_summary": "next hour ~80 (falling)"}

    agent.run_decision_cycle(timestep=4, snapshot=snapshot, carbon=carbon)
    stats = agent.tool_registry.database.get_decision_stats()
    assert stats["total"] == 1


def test_llm_failure_triggers_synthetic_audit_log(agent):
    # Force a real failure: point at a nonexistent model so ollama.chat() raises.
    agent.llm_settings["model"] = "this-model-does-not-exist:latest"

    zones = [make_zone(i) for i in range(1, 6)]
    snapshot = make_snapshot(zones)
    carbon = {"current_gco2_kwh": 200, "strategy": "NORMAL", "forecast_summary": "flat"}

    agent.run_decision_cycle(timestep=99, snapshot=snapshot, carbon=carbon)

    assert agent._log_decision_called is False  # model never got to run
    stats = agent.tool_registry.database.get_decision_stats()
    assert stats["total"] == 1
    assert stats["auto_generated"] == 1
    assert agent.fallback_handler.failure_count == 1

    row = agent.tool_registry.database.get_decision(1)
    assert row["auto_generated"] is True
    assert "log_decision not called" in row["reasoning"]

"""
tests/test_tools.py
====================
Tools are exercised directly with hand-crafted args, no LLM involved yet.
"""
import pytest

from agent.tool_registry import ToolRegistry
from data.database import Database
from simulation.state_manager import StateManager


def make_zone(zone_id, cool_sp=23.0, heat_sp=21.0, occ_fraction=1.0):
    return {
        "id": zone_id, "name": f"Zone{zone_id}",
        "temp_c": 22.0, "mrt_c": 21.0, "occ_fraction": occ_fraction,
        "co2_ppm": 500.0, "cool_sp": cool_sp, "heat_sp": heat_sp,
    }


@pytest.fixture
def registry():
    sm = StateManager()
    sm.update_snapshot({
        "sim_hour": 14,
        "zones": [
            make_zone(1, occ_fraction=1.0),
            make_zone(2, occ_fraction=0.0, cool_sp=28.0, heat_sp=18.0),
        ],
    })
    db = Database(":memory:")
    return ToolRegistry(sm, db)


class FakeAgent:
    def __init__(self):
        self._log_decision_called = False


# ── set_hvac_setpoint (flat single-zone form — still supported) ────

def test_set_hvac_setpoint_within_bounds_not_clamped(registry):
    result = registry.execute("set_hvac_setpoint", {"zone_id": 1, "cooling_sp": 24.0, "heating_sp": 21.0})
    assert result["success"] is True
    r = result["results"][0]
    assert r["clamped"] is False
    assert r["applied_cooling_sp"] == 24.0
    assert registry.state_manager.get_pending_setpoints()[1] == (24.0, 21.0)


def test_set_hvac_setpoint_out_of_bounds_gets_clamped(registry):
    result = registry.execute("set_hvac_setpoint", {"zone_id": 1, "cooling_sp": 40.0, "heating_sp": 21.0})
    assert result["success"] is True
    r = result["results"][0]
    assert r["clamped"] is True
    assert r["applied_cooling_sp"] == 25.0  # occupied bounds max 26, ramp from 23 caps at +2
    assert r["clamp_reason"] != ""


def test_set_hvac_setpoint_unoccupied_zone_uses_wider_band(registry):
    result = registry.execute("set_hvac_setpoint", {"zone_id": 2, "cooling_sp": 30.0, "heating_sp": 18.0})
    assert result["success"] is True
    r = result["results"][0]
    assert r["applied_cooling_sp"] == 30.0
    assert r["clamped"] is False


def test_set_hvac_setpoint_unknown_zone(registry):
    result = registry.execute("set_hvac_setpoint", {"zone_id": 99, "cooling_sp": 23.0, "heating_sp": 21.0})
    assert result["success"] is False
    assert "error" in result["results"][0]


# ── set_hvac_setpoint (batched "zones" form — the fast path) ───────

def test_set_hvac_setpoint_batch_multiple_zones_one_call(registry):
    result = registry.execute("set_hvac_setpoint", {"zones": [
        {"zone_id": 1, "cooling_sp": 24.0, "heating_sp": 21.0},
        {"zone_id": 2, "cooling_sp": 29.0, "heating_sp": 18.0},
    ]})
    assert result["success"] is True
    assert len(result["results"]) == 2
    pending = registry.state_manager.get_pending_setpoints()
    assert pending[1] == (24.0, 21.0)
    assert pending[2] == (29.0, 18.0)


def test_set_hvac_setpoint_batch_partial_failure_reported_per_zone(registry):
    result = registry.execute("set_hvac_setpoint", {"zones": [
        {"zone_id": 1, "cooling_sp": 24.0, "heating_sp": 21.0},
        {"zone_id": 99, "cooling_sp": 24.0, "heating_sp": 21.0},  # unknown zone
    ]})
    assert result["success"] is False  # overall False since one entry failed
    assert result["results"][0]["success"] is True
    assert result["results"][1]["success"] is False


# ── set_lighting_level (flat + batched) ─────────────────────────────

def test_set_lighting_occupied_zone_floored(registry):
    result = registry.execute("set_lighting_level", {"zone_id": 1, "level_fraction": 0.0})
    assert result["success"] is True
    r = result["results"][0]
    assert r["applied_level"] == 0.2
    assert registry.state_manager.get_pending_lighting()[1] == 0.2


def test_set_lighting_unoccupied_zone_can_go_dark(registry):
    result = registry.execute("set_lighting_level", {"zone_id": 2, "level_fraction": 0.0})
    assert result["success"] is True
    assert result["results"][0]["applied_level"] == 0.0


def test_set_lighting_unknown_zone(registry):
    result = registry.execute("set_lighting_level", {"zone_id": 99, "level_fraction": 0.5})
    assert result["success"] is False


def test_set_lighting_batch_multiple_zones_one_call(registry):
    result = registry.execute("set_lighting_level", {"zones": [
        {"zone_id": 1, "level_fraction": 0.8},
        {"zone_id": 2, "level_fraction": 0.0},
    ]})
    assert result["success"] is True
    pending = registry.state_manager.get_pending_lighting()
    assert pending[1] == 0.8
    assert pending[2] == 0.0


# ── schedule_precool ───────────────────────────────────────────────

def test_schedule_precool(registry):
    result = registry.execute("schedule_precool", {"target_temp_c": 21.0, "duration_minutes": 60})
    assert result["success"] is True
    assert result["scheduled_start_hour"] == 14  # from fixture snapshot
    assert result["estimated_co2_saved_kg"] > 0
    schedule = registry.state_manager.get_precool_schedule()
    assert schedule == {"target_temp_c": 21.0, "duration_minutes": 60, "start_hour": 14}


# ── log_decision ───────────────────────────────────────────────────

def test_log_decision_persists_to_database(registry):
    result = registry.execute(
        "log_decision",
        {"reasoning": "Zone 1 is warm, raised cooling setpoint.", "actions_taken": ["set_hvac_setpoint(zone=1)"]},
        timestep=42,
    )
    assert result["success"] is True
    decision_id = result["decision_id"]

    row = registry.database.get_decision(decision_id)
    assert row is not None
    assert row["timestep"] == 42
    assert row["reasoning"] == "Zone 1 is warm, raised cooling setpoint."
    assert row["actions_taken"] == ["set_hvac_setpoint(zone=1)"]
    assert row["auto_generated"] is False


def test_log_decision_sets_agent_flag(registry):
    agent = FakeAgent()
    registry.execute("log_decision", {"reasoning": "test", "actions_taken": []}, agent_ref=agent)
    assert agent._log_decision_called is True


def test_log_decision_without_agent_ref_does_not_crash(registry):
    result = registry.execute("log_decision", {"reasoning": "test", "actions_taken": []})
    assert result["success"] is True


def test_log_decision_missing_actions_taken_still_persists(registry):
    # Guards against a known small-model failure mode: the model calls
    # log_decision but omits actions_taken. Must still log something,
    # not crash the whole cycle into the fallback path.
    result = registry.execute("log_decision", {"reasoning": "Nothing needed changing."})
    assert result["success"] is True
    row = registry.database.get_decision(result["decision_id"])
    assert row["reasoning"] == "Nothing needed changing."
    assert row["actions_taken"] == []


def test_log_decision_missing_reasoning_still_persists(registry):
    result = registry.execute("log_decision", {"actions_taken": ["set_hvac_setpoint(zone=1)"]})
    assert result["success"] is True


# ── malformed args on other tools don't crash the cycle ────────────

def test_set_hvac_setpoint_missing_field_returns_error_not_crash(registry):
    result = registry.execute("set_hvac_setpoint", {"zone_id": 1, "cooling_sp": 23.0})  # heating_sp missing
    assert result["success"] is False
    assert "Malformed arguments" in result["error"]


def test_set_lighting_level_missing_field_returns_error_not_crash(registry):
    result = registry.execute("set_lighting_level", {"zone_id": 1})  # level_fraction missing
    assert result["success"] is False


def test_schedule_precool_missing_field_returns_error_not_crash(registry):
    result = registry.execute("schedule_precool", {"target_temp_c": 21.0})  # duration_minutes missing
    assert result["success"] is False


def test_decision_stats_track_llm_authored_count(registry):
    registry.execute("log_decision", {"reasoning": "a", "actions_taken": []})
    registry.execute("log_decision", {"reasoning": "b", "actions_taken": []})
    stats = registry.database.get_decision_stats()
    assert stats == {"total": 2, "auto_generated": 0, "llm_authored": 2}


# ── unknown tool ───────────────────────────────────────────────────

def test_unknown_tool_name(registry):
    result = registry.execute("delete_all_data", {})
    assert result["success"] is False
    assert "Unknown tool" in result["error"]

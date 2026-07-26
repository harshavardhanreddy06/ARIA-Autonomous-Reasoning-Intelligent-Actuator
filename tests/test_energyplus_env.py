"""
tests/test_energyplus_env.py
==============================
Unit-tests _apply_pending_writes' occupancy-transition safety net using
mocked EnergyPlus exchange/handle-registry objects — no real EnergyPlus
state needed to verify the branching logic. Covers the scenario where a
zone's setpoint, valid for its previous occupancy state, would otherwise
persist after that state changes (e.g. an unoccupied-range heating
setpoint left in place once the zone becomes occupied) because the agent
did not call set_hvac_setpoint for that zone this cycle.
"""
from unittest.mock import MagicMock

from simulation.energyplus_env import EnergyPlusEnv


def make_env():
    env = EnergyPlusEnv.__new__(EnergyPlusEnv)  # skip __init__ (no real EnergyPlusAPI needed)
    env.state_manager = MagicMock()
    env.state_manager.get_pending_setpoints.return_value = {}
    env.state_manager.get_pending_lighting.return_value = {}
    env.state_manager.get_precool_schedule.return_value = None
    env.api = MagicMock()
    env.handle_registry = MagicMock()
    env.handle_registry.get.side_effect = lambda name: name  # handle == name, for assertions
    return env


def make_zone(zone_id, cool_sp, heat_sp, occ_fraction):
    return {"id": zone_id, "cool_sp": cool_sp, "heat_sp": heat_sp, "occ_fraction": occ_fraction}


def test_stale_unoccupied_setpoint_corrected_when_zone_becomes_occupied():
    """heat_sp=18.0 (valid for unoccupied, 15-20C) left in place when
    occ_fraction flips positive (occupied requires 20-24C)."""
    env = make_env()
    snapshot = {"zones": [make_zone(4, cool_sp=26.0, heat_sp=18.0, occ_fraction=0.1)]}

    env._apply_pending_writes(state="fake_state", snapshot=snapshot)

    calls = {c.args[1]: c.args[2] for c in env.api.exchange.set_actuator_value.call_args_list}
    assert "heat_sp_4" in calls
    assert calls["heat_sp_4"] == 20.0  # clamped up to the occupied floor
    assert "cool_sp_4" not in calls  # 26.0 already valid for occupied (<=26 max)...


def test_untouched_zone_within_bounds_is_not_rewritten():
    env = make_env()
    snapshot = {"zones": [make_zone(1, cool_sp=23.0, heat_sp=21.0, occ_fraction=1.0)]}

    env._apply_pending_writes(state="fake_state", snapshot=snapshot)

    env.api.exchange.set_actuator_value.assert_not_called()


def test_pending_setpoint_zone_is_not_double_touched_by_safety_net():
    env = make_env()
    env.state_manager.get_pending_setpoints.return_value = {2: (24.0, 21.0)}
    snapshot = {"zones": [make_zone(2, cool_sp=24.0, heat_sp=21.0, occ_fraction=1.0)]}

    env._apply_pending_writes(state="fake_state", snapshot=snapshot)

    calls = env.api.exchange.set_actuator_value.call_args_list
    handles_written = [c.args[1] for c in calls]
    assert handles_written.count("cool_sp_2") == 1  # only the pending write, no duplicate
    assert handles_written.count("heat_sp_2") == 1


def test_stale_occupied_setpoint_corrected_when_zone_becomes_unoccupied():
    env = make_env()
    # 23.5C cooling / 21.0C heating were valid while occupied; zone just emptied.
    snapshot = {"zones": [make_zone(1, cool_sp=23.5, heat_sp=21.0, occ_fraction=0.0)]}

    env._apply_pending_writes(state="fake_state", snapshot=snapshot)

    calls = {c.args[1]: c.args[2] for c in env.api.exchange.set_actuator_value.call_args_list}
    assert calls["cool_sp_1"] == 26.0  # clamped up to the unoccupied floor
    assert calls["heat_sp_1"] == 20.0  # clamped down to the unoccupied ceiling

"""
tests/test_safety.py
=====================
"""
from agent import safety_validator as sv
from config.env_loader import load_settings


def test_settings_yaml_matches_hardcoded_constants():
    """
    safety_validator.py hard-codes these deliberately (a config edit must
    never silently weaken a safety bound) — but settings.yaml should still
    carry the same numbers for display, so this catches drift between them.
    """
    cfg = load_settings()["safety"]
    assert cfg["cooling_sp_min_occupied"] == sv.COOL_MIN_OCC
    assert cfg["cooling_sp_max_occupied"] == sv.COOL_MAX_OCC
    assert cfg["heating_sp_min_occupied"] == sv.HEAT_MIN_OCC
    assert cfg["heating_sp_max_occupied"] == sv.HEAT_MAX_OCC
    assert cfg["cooling_sp_min_unoccupied"] == sv.COOL_MIN_UOCC
    assert cfg["cooling_sp_max_unoccupied"] == sv.COOL_MAX_UOCC
    assert cfg["heating_sp_min_unoccupied"] == sv.HEAT_MIN_UOCC
    assert cfg["heating_sp_max_unoccupied"] == sv.HEAT_MAX_UOCC
    assert cfg["max_setpoint_delta"] == sv.MAX_RAMP
    assert cfg["co2_alarm_ppm"] == sv.CO2_ALARM_PPM
    assert cfg["min_lighting_fraction_occupied"] == sv.MIN_LIGHT_OCC


# ── Cooling setpoint ──────────────────────────────────────────────

def test_cooling_clips_below_absolute_min_occupied():
    result = sv.validate_cooling_sp(1, proposed=15.0, current=21.0, is_occupied=True)
    assert result == 20.0  # clipped to floor, not ramp-limited to 19


def test_cooling_clips_above_absolute_max_occupied():
    result = sv.validate_cooling_sp(1, proposed=40.0, current=25.0, is_occupied=True)
    assert result == 26.0  # ramp would allow 27, but absolute max wins


def test_cooling_ramp_limits_big_jump_within_bounds():
    result = sv.validate_cooling_sp(1, proposed=26.0, current=21.0, is_occupied=True)
    assert result == 23.0  # 5 degree jump clipped to +2


def test_cooling_unoccupied_uses_wider_band():
    result = sv.validate_cooling_sp(1, proposed=30.0, current=29.0, is_occupied=False)
    assert result == 30.0  # within [26,32] and within ramp of current


# ── Heating setpoint ──────────────────────────────────────────────

def test_heating_clips_below_absolute_min_occupied():
    result = sv.validate_heating_sp(1, proposed=10.0, current=21.0, is_occupied=True)
    assert result == 20.0


def test_heating_clips_above_absolute_max_unoccupied():
    result = sv.validate_heating_sp(1, proposed=25.0, current=19.0, is_occupied=False)
    assert result == 20.0  # unoccupied heating max is 20, not 24


def test_heating_ramp_limits_big_drop():
    result = sv.validate_heating_sp(1, proposed=15.0, current=22.0, is_occupied=True)
    assert result == 20.0  # ramp allows down to 20, and 20 is also the occupied floor


# ── Occupancy-transition edge case ──────────────────────────────────

def test_occupancy_transition_never_violates_new_absolute_bounds():
    """
    Zone was unoccupied last cycle (current=15, a valid unoccupied heating
    setpoint) and becomes occupied this cycle (bounds jump to [20,24]).
    Clamping absolute-then-ramp with no re-clamp would give 17 (out of the
    new occupied bounds) — absolute bounds must win over the ramp limit.
    """
    result = sv.validate_heating_sp(1, proposed=22.0, current=15.0, is_occupied=True)
    assert 20.0 <= result <= 24.0
    assert result == 20.0  # ramp-limited to 17, then re-clamped up to the floor


def test_occupancy_transition_cooling_direction():
    # Occupied cooling (22) -> zone empties -> unoccupied bounds [26,32].
    result = sv.validate_cooling_sp(1, proposed=27.0, current=22.0, is_occupied=False)
    assert 26.0 <= result <= 32.0
    assert result == 26.0  # ramp-limited to 24, then re-clamped up to the new floor


# ── Lighting ───────────────────────────────────────────────────────

def test_lighting_occupied_floor():
    assert sv.validate_lighting_level(0.0, is_occupied=True) == 0.2


def test_lighting_occupied_ceiling():
    assert sv.validate_lighting_level(1.5, is_occupied=True) == 1.0


def test_lighting_unoccupied_can_go_to_zero():
    assert sv.validate_lighting_level(0.0, is_occupied=False) == 0.0


def test_lighting_unoccupied_ceiling():
    assert sv.validate_lighting_level(1.5, is_occupied=False) == 1.0


# ── CO2 ────────────────────────────────────────────────────────────

def test_co2_below_alarm():
    assert sv.is_co2_critical(999) is False


def test_co2_at_alarm_threshold():
    assert sv.is_co2_critical(1000) is True


def test_co2_above_alarm():
    assert sv.is_co2_critical(1500) is True

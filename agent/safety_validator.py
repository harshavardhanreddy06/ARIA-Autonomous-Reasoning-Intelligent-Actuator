"""
agent/safety_validator.py
==========================
Hard limit gate applied to every actuator write before it reaches
EnergyPlus. Values are hard-coded here (not read from settings.yaml) so a
config file edit can never silently weaken a safety bound — settings.yaml
carries the same numbers for display/reference, and test_safety.py asserts
the two stay in sync.
"""

COOL_MIN_OCC, COOL_MAX_OCC = 20.0, 26.0
HEAT_MIN_OCC, HEAT_MAX_OCC = 20.0, 24.0
COOL_MIN_UOCC, COOL_MAX_UOCC = 26.0, 32.0
HEAT_MIN_UOCC, HEAT_MAX_UOCC = 15.0, 20.0  # unified with PDF

MAX_RAMP = 2.0          # °C max change per cycle — thermal shock prevention
CO2_ALARM_PPM = 1000    # ASHRAE 62.1
MIN_LIGHT_OCC = 0.2     # minimum lighting fraction while a zone is occupied


def _clip(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _validate_setpoint(proposed: float, current: float, bounds: tuple[float, float]) -> float:
    """
    Absolute bounds always win. Ramp rate is a secondary (comfort) concern —
    applying it naively AFTER absolute clamping can pull the result back out
    of bounds when a zone's occupancy state just changed (e.g. current=15
    from an unoccupied cycle, zone becomes occupied requiring 20-24 this
    cycle: clamping to [20,24] then to current±2 gives 17, which violates
    the occupied floor). Clamping to absolute bounds again afterward closes
    that gap — safety (bounds) outranks comfort (ramp smoothness) per the
    system's own priority order.
    """
    lo, hi = bounds
    safe = _clip(proposed, lo, hi)
    safe = _clip(safe, current - MAX_RAMP, current + MAX_RAMP)
    safe = _clip(safe, lo, hi)
    return safe


def validate_cooling_sp(zone_id: int, proposed: float, current: float, is_occupied: bool) -> float:
    bounds = (COOL_MIN_OCC, COOL_MAX_OCC) if is_occupied else (COOL_MIN_UOCC, COOL_MAX_UOCC)
    return _validate_setpoint(proposed, current, bounds)


def validate_heating_sp(zone_id: int, proposed: float, current: float, is_occupied: bool) -> float:
    bounds = (HEAT_MIN_OCC, HEAT_MAX_OCC) if is_occupied else (HEAT_MIN_UOCC, HEAT_MAX_UOCC)
    return _validate_setpoint(proposed, current, bounds)


def validate_lighting_level(proposed: float, is_occupied: bool) -> float:
    lo = MIN_LIGHT_OCC if is_occupied else 0.0
    return _clip(proposed, lo, 1.0)


def is_co2_critical(co2_ppm: float) -> bool:
    return co2_ppm >= CO2_ALARM_PPM

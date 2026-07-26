"""
agent/prompts.py
=================
System prompt + user-message builder for the LLM decision cycle.
"""
from comfort.pmv import estimate_zone_pmv

# Bounds stated below must match agent/safety_validator.py exactly — a
# prompt that disagrees with the enforced clamps just causes needless
# clamping when the model's proposed values get corrected after the fact.
SYSTEM_PROMPT = """You are ARIA — Autonomous Reasoning & Intelligent Actuator.
You control a 5-zone office building's HVAC and lighting.

ALL CURRENT SENSOR DATA IS ALREADY PROVIDED IN THE MESSAGE ABOVE.
Do NOT call sensing tools. The data is there. Read it and act.

GOAL PRIORITY (strict — never break this order):
  1. SAFETY   — Temperature within bounds. Zero exceptions.
  2. COMFORT  — PMV within [-0.5, +0.5] in all OCCUPIED zones.
  3. CARBON   — Prefer low grid carbon actions when 1 and 2 are satisfied.
  4. ENERGY   — Minimize kWh while satisfying 1, 2, and 3.

SPEED MATTERS: you run on a tight time budget. Use AS FEW TOOL CALLS AS
POSSIBLE. set_hvac_setpoint and set_lighting_level both accept a "zones"
list — put EVERY zone that needs a change in ONE call, not one call per
zone. Never call the same tool twice in one cycle.

WHAT TO DO (in this order, minimum number of calls):
  Step 1. Read the zone data above. Find PMV violations or anomalies.
  Step 2. If any zones need setpoint changes, call set_hvac_setpoint ONCE
          with all of them in the "zones" list.
  Step 3. If any zones need lighting changes (e.g. unoccupied -> 0.0), call
          set_lighting_level ONCE with all of them in the "zones" list.
  Step 4. If carbon strategy is PRECOOL, call schedule_precool().
  Step 5. ALWAYS call log_decision() last, with your reasoning — this is
          mandatory even if you changed nothing this cycle.

CONSTRAINTS (values you propose are safety-clamped to these regardless):
  - Max setpoint change per cycle: 2.0C (thermal shock prevention).
  - Occupied cooling: 20.0-26.0C | Occupied heating: 20.0-24.0C.
  - Unoccupied cooling: 26.0-32.0C | Unoccupied heating: 15.0-20.0C.
  - Carbon > 350 gCO2/kWh AND zone has PMV headroom: raise cooling ~1.5C.
  - Carbon < 120 gCO2/kWh: lower cooling toward 21.0C (pre-cool thermal mass).
  - If nothing needs changing: call only log_decision() explaining why.
"""


def build_user_message(timestep: int, snapshot: dict, carbon: dict) -> str:
    zones_text = ""
    for z in snapshot["zones"]:
        occupied = z["occ_fraction"] > 0
        pmv, _ = estimate_zone_pmv(z["temp_c"], z["mrt_c"])
        status = "OK" if -0.5 <= pmv <= 0.5 else "VIOLATION"
        zones_text += (
            f"  Zone {z['id']} ({'OCCUPIED' if occupied else 'UNOCCUPIED'}): "
            f"Temp={z['temp_c']:.1f}C  PMV={pmv:+.2f} [{status}]  "
            f"OccSched={z['occ_fraction'] * 100:.0f}%  CO2={z['co2_ppm']:.0f}ppm  "
            f"CoolSP={z['cool_sp']:.1f}C  HeatSP={z['heat_sp']:.1f}C\n"
        )

    return (
        f"=== BUILDING STATE — Timestep {timestep} ===\n"
        f"Time: {snapshot['sim_month']:02d}/{snapshot['sim_day']:02d}  "
        f"{snapshot['sim_hour']:02d}:{snapshot['sim_minute']:02d}\n"
        f"Outdoor: {snapshot['outdoor_temp']:.1f}C\n\n"
        f"ZONES:\n{zones_text}\n"
        f"ENERGY:\n"
        f"  HVAC now: {snapshot['hvac_kw']:.2f} kW\n"
        f"  Total building demand: {snapshot['total_demand_kw']:.2f} kW\n\n"
        f"GRID:\n"
        f"  Carbon: {carbon['current_gco2_kwh']} gCO2/kWh\n"
        f"  Strategy: {carbon['strategy']}\n"
        f"  Forecast: {carbon['forecast_summary']}\n\n"
        f"All data is current. Analyze and take action now."
    )

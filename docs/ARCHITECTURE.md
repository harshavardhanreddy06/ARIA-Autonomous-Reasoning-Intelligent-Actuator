# ARIA — Architecture

Eco-Loop Building Agent · Honeywell Hackathon 2026

## Tool-Calling Architecture

Per the problem statement: "Implement an MCP Server or custom agentic tools."
ARIA implements option 2 — custom agentic tools via Ollama's native function-calling
API. Tools are defined as JSON schemas (`agent/tool_registry.py`) and called directly
through the Ollama chat API's `tools` parameter — no JSON-RPC/MCP protocol risk.

Four tools: `set_hvac_setpoint`, `set_lighting_level`, `schedule_precool`,
`log_decision`. The first two accept **either** a single zone or a batched
`zones: [...]` array in one call — added after measuring that a small model
making one tool call per zone per round-trip was the dominant cost driver in
per-cycle latency (see *Prompt Latency Management* below).

## Prompt Engineering Strategy

- All sensor data pre-packed into the user message — zero sensing round-trips.
- System prompt enforces a strict 4-goal priority hierarchy: SAFETY > COMFORT >
  CARBON > ENERGY.
- Explicit "use as few tool calls as possible, batch every zone into one call"
  instruction — added specifically to cut LLM round-trips per cycle.
- Temperature 0.1 for near-deterministic control decisions.
- `log_decision` guaranteed by a Python-side fallback (`_write_synthetic_log` in
  `agent/aria_agent.py`) — 100% audit trail by construction, not by model
  reliability. The dashboard shows real vs. synthetic entries honestly rather
  than hiding the difference.

## Prompt Latency Management

Measured directly against the real model (qwen2.5:3b via Ollama, Apple Silicon,
local, zero network latency), not estimated:

| Configuration | Avg cycle | Max cycle | Fallback rate |
|---|---|---|---|
| Original plan (per-zone tool calls) | 46.0s | 77.3s | 52% (25/48 cycles) |
| Batched tools + error hardening | 31.9s | 45.0s (timeout cap) | 19% (9/48 cycles) |

The dominant latency driver was round-trip count, not raw model speed: a cycle
needing one tool call per zone (5 zones x 2 tools = up to 10 round-trips, each
carrying the growing conversation history) reliably exceeded the 45s timeout.
Redesigning `set_hvac_setpoint`/`set_lighting_level` to accept every zone in a
single batched call cut typical cycles to 2 (batch call + `log_decision`),
roughly halving average latency and cutting the fallback rate by ~2.7x.

45-second timeout with automatic fallback to last valid setpoints
(`agent/fallback_handler.py`) covers whatever fraction of cycles still don't
complete in time — this is a deliberate design choice (accept an imperfect
small model, catch its failures in software) rather than an unhandled edge case.

## Handling Lengthy Simulation Logs

- Only the current timestep snapshot is sent to the LLM (not history).
- SQLite (`timestep_data`, `decisions` tables) stores the complete history —
  queried by the dashboard, not by the LLM.
- EnergyPlus's own verbose output is written to `output/` only.

## Building Model Deliverables

- `simulation/models/SmallOffice.idf` — DOE Small Office prototype, with the
  fixes below applied. Still the same baseline building physics — only
  reporting variables, CO2 simulation, and simulation-control flags changed,
  none of which alter the underlying thermal/energy behavior being measured.
- Live actuator API (`simulation/energyplus_env.py`) is the primary control
  mechanism for both the ARIA and baseline runs.
- `scripts/export_aria_idf.py` — post-run static IDF snapshot with ARIA's
  converged setpoints baked in, as a secondary artifact for the deliverable
  requirement.

## Real Bugs Found and Fixed During Development

Every item below was caught by actually running EnergyPlus and the real LLM,
not by code review — several were silent failures that would have produced
plausible-looking but wrong results if left unverified.

1. **`request_variable()` doesn't register handles at runtime** in this
   EnergyPlus build — only variables statically declared as `Output:Variable`
   objects in the IDF ever resolve. Every sensor ARIA reads had to be added
   to the IDF directly.
2. **`get_actuator_handle`'s real argument order** is
   `(state, component_type, control_type, actuator_key)`, not
   `(state, component_type, actuator_key, control_type)` as originally planned.
3. **A `raise` inside an EnergyPlus ctypes callback is silently swallowed** —
   it prints a traceback but the simulation keeps running past a fatal error.
   The hard-fail handle-validation gate uses a flag checked after
   `run_energyplus()` returns instead, where a real exception can propagate.
4. **`SimulationControl`'s "Run Simulation for Weather File Run Periods" was
   `NO`** in the base IDF — meaning the real weather-driven simulation period
   never executed at all; every early test was unknowingly running against
   EnergyPlus's HVAC-sizing/design-day calculation passes instead of real
   July data. Combined with not filtering `kind_of_sim`/`warmup_flag`, an
   unfiltered "7-day run" would have wasted ~2,900 extra ticks (~25 hours at
   measured LLM latency) on sizing/warmup data nothing uses.
5. **`get_actuator_value()` on setpoint actuators only reflects a value ARIA
   itself wrote** — it reads back `0.0` before that, not the schedule-driven
   current setpoint. Fixed by reading dedicated report variables
   (`Zone Thermostat Heating/Cooling Setpoint Temperature`) for display.
6. **The safety validator's ramp-rate clamp could override the absolute
   safety bounds** on an occupancy transition (e.g. a zone at 15C unoccupied
   heating becoming occupied, requiring 20-24C, would clamp to 17C — below
   the occupied floor). Fixed by re-clamping to absolute bounds after the
   ramp-rate clamp; safety must outrank ramp-smoothing per the system's own
   stated priority order.
7. **A missing/malformed tool argument from the model crashed the whole
   decision cycle** (observed: `KeyError: 'actions_taken'` when the model
   called `log_decision` without it) instead of failing just that one tool
   call. `log_decision` now uses defaults for optional fields; all tool
   dispatch is wrapped so malformed arguments degrade gracefully.
8. **EnergyPlus's `minutes()` can exceed 60** (observed up to 63, not just a
   clean rollover to 60) — timestamp normalization needed real modulo
   arithmetic, not an `== 60` special case.
9. **No true per-zone CO2 or occupant-count variables exist** in the base
   model — CO2 wasn't simulated at all (no `ZoneAirContaminantBalance`
   object), and all 5 zones share one building-wide occupancy schedule.
   Fixed by enabling real CO2 physics (not mocking it) and reading the
   shared occupancy schedule's fraction honestly rather than pretending a
   distinct per-zone count exists.
10. **A zone's setpoint could briefly outlive its occupancy state** — found
    live during the real 7-day run: a zone's heating setpoint sat at 18C
    (valid for unoccupied, 15-20C) for two ticks (30 min) after occupancy
    transitioned to occupied (requiring 20-24C), because the LLM's tool
    calls that cycle didn't happen to touch that zone. The safety validator
    only clamps values actively proposed — nothing previously re-checked an
    untouched zone's *existing* setpoint against its *current* occupancy
    state. Values never left the absolute safe envelope (20-32C cooling,
    15-24C heating) at any point — this was a comfort-policy gap, not a
    hard-safety breach. Fixed in `energyplus_env.py._apply_pending_writes`:
    every zone not touched by a pending write or an active precool this
    cycle is now re-validated against its current occupancy state every
    tick, independent of the LLM's tool calls (see
    `tests/test_energyplus_env.py`). This fix landed after the live 7-day
    run had already progressed past ~47% — see the Results section for how
    that's accounted for.

## Known Simplifications

- **PMV uses fixed comfort assumptions** (50% RH, 0.1 m/s air speed, 1.2 met,
  0.5 clo) — EnergyPlus isn't sensing per-zone humidity or air velocity in
  this build. Good enough to classify comfort violations; not measured humidity.
- **Occupancy is a shared 0-1 schedule fraction**, not a true per-zone
  occupant count — accurate to how this DOE prototype model is actually built.
- **Cost and CO2-avoided figures on the dashboard are estimates** from
  assumed constants ($0.15/kWh, a 24h-average grid intensity from the mock
  carbon profile) — only energy (kWh) is a directly measured value.

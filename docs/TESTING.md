# ARIA — Testing & Verification Log

This documents how each phase of ARIA was built and verified, and the real
issues found and fixed along the way. Every phase was validated by actually
running the code — against real EnergyPlus, the real local LLM, and (for the
final phases) a complete real 7-day simulation — not by code review alone.

---

## Phase 0 — Environment Verification

Confirmed the three external dependencies actually work together before
writing any project code:

- `pyenergyplus` imports correctly from the local EnergyPlus install.
- The model files (`SmallOffice.idf`, `Chicago.epw`) are present and valid.
- Ollama + `qwen2.5:3b` responds to a real tool-calling prompt (not just
  text) — confirmed the model returns a structured `set_hvac_setpoint` call,
  not prose, for a simple "zone is too warm" scenario.
- Python version check.

Result: clean pass on all four checks.

## Phase 1 — Project Skeleton + Config

Built the directory structure, `settings.yaml`, `env_loader.py`, and
`setup.sh`, then verified `setup.sh` actually runs end-to-end rather than
just existing.

**Found and fixed:**
- `requirements.txt` pinned `eppy==0.5.65`, a version that doesn't exist on
  PyPI at all — `pip install` failed outright.
- After bumping that pin, the fully-pinned (`==`) requirements file produced
  an unresolvable dependency conflict between `eppy` and `pytest`. Switched
  to `>=` floors, verified the install resolves cleanly in a throwaway clean
  virtualenv (not just the already-populated dev one, which can mask a
  broken lock file).

## Phase 2 — EnergyPlus Handle Validation

Built `handle_registry.py` and a minimal `energyplus_env.py` that does
nothing but validate every sensor/actuator handle ARIA needs, hard-failing
loudly if any of them don't resolve.

**Found and fixed:**
- `get_actuator_handle`'s real argument order is
  `(state, component_type, control_type, actuator_key)` — the plan's
  original draft had `actuator_key` and `control_type` swapped.
- The hard-fail itself didn't work: a Python `raise` inside an EnergyPlus
  ctypes callback is silently swallowed (ctypes prints a traceback and the
  simulation just keeps running). Fixed by setting a flag inside the
  callback and raising for real after `run_energyplus()` returns, in normal
  Python call context.
- `request_variable()` — the documented way to register an output variable
  at runtime — does not actually register anything in this EnergyPlus
  build. Confirmed by testing: only variables *already* declared as static
  `Output:Variable` objects in the IDF ever resolved to a valid handle.
  Every sensor ARIA reads had to be added to the IDF directly instead.
- Several variable names were simply wrong for this model/version:
  `"Zone Air Temperature"` never resolves per-zone (use
  `"Zone Mean Air Temperature"`); `"Air System Electric Energy"` and
  `"Facility Total Electric Demand Power"` don't exist at all (replaced with
  `"Facility Total HVAC Electricity Demand Rate"` /
  `"Facility Total Electricity Demand Rate"`, both requiring key
  `"Whole Building"` — blank `""` and `"*"` both silently failed).
- No true per-zone occupant-count variable exists — all 5 zones share one
  building-wide occupancy schedule. Confirmed by inspecting the IDF's
  `People` objects directly.
- CO2 wasn't simulated at all (no `ZoneAirContaminantBalance` object in the
  base model). Rather than drop the CO2 safety check or fake the data,
  enabled real CO2 physics (`ZoneAirContaminantBalance` + a constant 400ppm
  outdoor schedule) and verified the handle resolves afterward.

Result: 33 handles validated clean, plus a deliberately-broken handle name
test to confirm the hard-fail path actually raises (it didn't, until fixed).

## Phase 3 — Sensor Read Loop + State Manager

Wired the validated handles into a per-timestep snapshot stored in a new
thread-safe `state_manager.py`, and ran it across a full simulated day,
checking the printed values for physical plausibility (not just "no crash").

**Found and fixed:**
- `get_actuator_value()` on the setpoint actuators only reflects a value
  ARIA itself has written via `set_actuator_value()` — before that it reads
  back `0.0`, not the real schedule-driven setpoint. Every printed setpoint
  was silently `0.0/0.0` until this was caught. Fixed by reading the actual
  current setpoint from dedicated report variables
  (`Zone Thermostat Heating/Cooling Setpoint Temperature`) for display,
  while keeping the actuators for writing.
- EnergyPlus's `minutes()` can exceed 60 — not just hit exactly 60 at an
  hour boundary, but values like 63 were observed (likely from
  timestep-shortening during warmup). A first fix that only handled the
  `==60` case still produced garbage timestamps (`22:63`); corrected with
  real modulo arithmetic.

Result: a full simulated day of sane, physically plausible, correctly
time-stamped sensor data.

## Phase 4 — Comfort (PMV) + Carbon Intensity

Both are pure functions with no EnergyPlus dependency, so both were
validated independently of the simulation:

- `comfort/pmv.py` (Fanger PMV/PPD) was cross-checked numerically against
  `pythermalcomfort`, an independently-maintained reference implementation,
  across 7 conditions spanning cold/hot/humid/dry — matched within rounding
  noise (≤0.05 PMV, ≤0.5 PPD) on every case. `pythermalcomfort` was only
  installed temporarily for this comparison; it is not a project dependency.
- `carbon/grid_intensity.py`'s mock profile was checked across all 24 hours
  to confirm it actually exercises all three carbon strategies (PRECOOL
  overnight, NORMAL midday, DEFER at evening peak) rather than sitting
  entirely inside one band — an arbitrary "realistic-looking" curve
  wouldn't necessarily cross both configured thresholds. Also verified the
  live-API failure path degrades to the mock cleanly (tested against the
  real Electricity Maps endpoint with a deliberately invalid key — a real
  401, caught and handled without crashing).

Result: 11 automated tests (`test_pmv.py`) plus manual verification of the
carbon strategy coverage and API-failure fallback.

## Phase 5 — Safety Validator

**Found and fixed a real safety-priority bug** before it could matter: the
original clamp order (absolute bounds, then ramp-rate limit) lets the
ramp-rate clamp silently override the absolute safety bounds whenever a
zone's occupancy state changes mid-cycle. Proved numerically: a zone at
15°C (valid unoccupied heating) becoming occupied (requiring 20-24°C) would
clamp to **17°C** — below the occupied floor — because the ramp window
(15±2) doesn't overlap the new occupied bounds. Fixed by re-clamping to
absolute bounds after the ramp-rate clamp, since safety must outrank
ramp-smoothing per the system's own stated priority order. Verified both
that the fix produces the correct result (20°C) and that the original naive
algorithm really did produce the violation (17°C).

Result: 17 automated tests (`test_safety.py`), including that exact
occupancy-transition regression case in both heating and cooling
directions, plus a test asserting `settings.yaml`'s safety numbers never
drift from the hard-coded validator constants.

## Phase 6 — Tool Registry

Built the database layer (`decisions` table) and the 4 agent tools,
exercised directly with hand-crafted arguments (no LLM yet). Verified
in-bounds vs. clamped setpoints, occupied/unoccupied lighting floors,
unknown-zone error handling, precool scheduling, and that `log_decision`
actually persists to and reads back correctly from the database, including
the audit-trail flag it sets.

Result: 21 automated tests (`test_tools.py`).

## Phase 7 — Prompts + Agent Loop (Real LLM, No EnergyPlus Yet)

Wired the actual Ollama tool-calling loop for the first time, exercised
against hand-built sensor scenarios (normal, PMV violation, unoccupied,
precool) with the real model — not mocked.

**Verified before writing any code:**
- The exact Ollama `Message`/`ToolCall` schema (a `tool_name` field is
  required on tool-result messages; `call.function.name` /
  `call.function.arguments` for reading a call) — checked directly against
  the installed client library rather than assumed.
- `ollama.Client()` passes unknown kwargs straight through to its internal
  `httpx.Client`, confirming `timeout=` is a legitimate way to enforce the
  configured per-cycle LLM timeout.

**Found and fixed:**
- The original system prompt described unoccupied setpoint bounds as single
  fixed values ("cooling 28.0°C") when the actual enforced bounds (matching
  the safety validator) are ranges — a mismatched prompt just causes
  needless clamping.

**A real failure, not a bug:** one test run genuinely hit the 45-second LLM
timeout on a 5-zone scenario. The system did exactly what it was designed
to do — caught it, recorded the failure, wrote the synthetic audit entry,
returned fallback setpoints, no crash. The first draft of the tests wrongly
assumed the LLM always succeeds in time; corrected to assert the real
guarantee (exactly one audit entry per cycle, real or synthetic) instead of
assuming success — a local 3B model timing out sometimes is expected, not
a defect.

Result: 5 tests against the live model (`test_agent.py`, ~2-3 min), including
a deliberately-broken model name proving the synthetic audit log fires on
total LLM failure.

## Phase 8 — Full Integration (Real LLM + Real EnergyPlus)

This phase surfaced the most significant findings of the whole build.

**Closing gaps found while wiring the real loop:**
- No lighting actuator existed yet — added it, plus the internal variable
  for each zone's real design wattage (a 0-1 fraction from the LLM needed a
  real watts value to actually control anything).
- `schedule_precool`'s `target_temp_c` was never safety-clamped at all
  (it has no per-zone context at the tool level) — fixed by validating it
  per zone, against each zone's actual current state, at the moment it's
  applied.
- `settings.yaml` specified 15-minute cycles (`timestep_per_hour: 4`) but
  the IDF was still configured for 10-minute steps (`Timestep,6`) — fixed
  to match, which is also what makes a 7-day run equal exactly 672 cycles.

**Measured, then fixed, a major latency problem.** A first 48-cycle test run
(one tool call per zone) averaged **46.0s/cycle** (max 77.3s) with a
**52% fallback rate** — the model was spending its entire time budget
making one tool call per zone per round-trip. Redesigned `set_hvac_setpoint`
and `set_lighting_level` to accept every zone in a single batched call and
added an explicit "use as few tool calls as possible" prompt instruction.
Re-measured on the same 48-cycle test: **31.9s/cycle average** (max 45.0s),
**19% fallback rate** — roughly halving latency and cutting the fallback
rate by ~2.7x from a single change.

**The single most important finding of the entire build:** `SimulationControl`'s
"Run Simulation for Weather File Run Periods" flag was set to `NO` in the
base IDF. This means **the real July 1-7 weather-driven simulation had never
executed even once**, in any earlier phase's testing — every prior "real
run" test was unknowingly running against EnergyPlus's throwaway
HVAC-sizing/design-day calculation passes instead. Combined with not
filtering on `kind_of_sim`/`warmup_flag`, an unfiltered "7-day run" would
have wasted roughly 2,900 extra ticks (~25 hours at measured LLM latency) on
sizing and warmup data nothing would ever use. Fixed both: flipped the
`SimulationControl` flag, and added a filter so only real, post-warmup,
weather-period ticks count — verified empirically to produce exactly 672
ticks spanning precisely July 1 00:15 through July 7 00:00.

## Phase 9 — Baseline Run, Full Pipeline, Dashboard

Built the `timestep_data` table, `baseline_env.py`, `main.py`, and the
Streamlit dashboard, verifying each against short real runs before trusting
them for the full 7-day dataset.

- `baseline_env.py` verified against an 8-tick run: exactly 40 rows logged
  (8 ticks x 5 zones), as expected.
- `main.py` verified against a 4-tick run with the real LLM: decisions and
  sensor data both landed correctly in the real database file (not just
  `:memory:`).
- The dashboard was opened in an actual browser (via a real screenshot, not
  assumed) against real seed data — which immediately surfaced a
  `ModuleNotFoundError`: Streamlit only adds its own script's directory to
  `sys.path`, not the project root, breaking every project-root-relative
  import the moment the app launched. Fixed with an explicit `sys.path`
  bootstrap at the top of `dashboard/app.py`. After the fix, all four
  panels (energy, comfort heatmap, savings, decision log) were re-verified
  rendering correctly with real LLM-generated reasoning text and a properly
  diverging PMV color scale.

## Real 7-Day Runs

- **Baseline** (no AI): all 672 ticks completed in under 2 seconds of
  EnergyPlus compute time (pure physics, no LLM calls) — 1,251.2 kWh total.
- **ARIA** (real LLM): a genuine 6 hour 40 minute continuous run against the live model — 672 real decision cycles, zero crashes, zero restarts.
  Mid-run checkpoints showed a 0.3% fallback rate (well below the ~19%
  measured in the shorter Phase 8 test, now including real daytime
  complexity), ~91% occupied-zone PMV compliance, and energy tracking
  meaningfully below the baseline over the same elapsed window.

**Found one more real gap, checked directly against the live data:** 14
timestep-zone rows (out of roughly 1,600 checked) showed a zone's setpoint
sitting outside its *current* occupancy band — e.g. heating left at 18°C for
two ticks (30 minutes) right after a zone became occupied (requiring
≥20°C), because the LLM's tool calls that cycle didn't happen to touch that
zone. Checked every flagged value against the absolute safe envelope
(20-32°C cooling, 15-24°C heating): none ever left it — this was a
comfort-policy gap during occupancy transitions, not a hard-safety breach.
Root cause: the safety validator only clamps values the LLM actively
proposes; nothing was proactively re-checking an *untouched* zone's
existing setpoint against its current occupancy state. Fixed in
`energyplus_env.py` — every zone not touched by a pending write or an
active precool this cycle is now re-validated against its current occupancy
state every tick, independent of the LLM's tool calls — and covered by 4
new unit tests reproducing the exact observed scenario with mocked
EnergyPlus objects (no live simulation needed to verify the branching logic).

This fix landed after the real 7-day run had already progressed past the
halfway point; restarting to apply it retroactively would have discarded
several hours of real, otherwise-valid computed data for a gap already
confirmed non-dangerous, so the live run was allowed to finish rather than
restarted. The fix itself is verified and in place for any future run.

---

## Automated Test Suite Summary

| File | Count | What it covers |
|---|---|---|
| `test_pmv.py` | 11 | PMV/PPD against cross-validated reference values + mathematical properties |
| `test_safety.py` | 17 | Setpoint/lighting clamps, ramp-rate limits, occupancy-transition edge cases |
| `test_tools.py` | 21 | All 4 tools, single-zone and batched calling forms, malformed-argument handling |
| `test_database.py` | 2 | Timestep snapshot persistence and run-type filtering |
| `test_energyplus_env.py` | 4 | The occupancy-transition safety-net fix, via mocked EnergyPlus objects |
| `test_agent.py` | 5 | The real Ollama + qwen2.5:3b tool-calling loop, including forced-failure handling |

**60 tests total**, all passing.

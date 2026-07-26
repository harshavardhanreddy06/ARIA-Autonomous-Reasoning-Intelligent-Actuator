# ARIA — Testing & Verification

**Eco-Loop Building Agent · Honeywell Hackathon 2026**
Prepared by Konepalli Harshavardhan

> For the project overview, see [`README.md`](README.md). For the system
> flow and component design, see [`ARCHITECTURE.md`](ARCHITECTURE.md).

Every phase of ARIA was verified by actually running it — against real
EnergyPlus, the real local LLM, and, for final validation, a complete real
7-day simulation — not by code review or assumption. This document covers
the automated test suite, how real failure situations were found and
handled, and what a genuine 6-hour-40-minute continuous run actually
demands of a system and the machine running it.

---

## 1. Automated Test Suite

| File | Tests | Covers |
|---|---|---|
| `test_pmv.py` | 11 | PMV/PPD against cross-validated reference values and mathematical invariants |
| `test_safety.py` | 17 | Setpoint/lighting clamps, ramp-rate limits, occupancy-transition edge cases |
| `test_tools.py` | 21 | All 4 tools, single-zone and batched calling forms, malformed-argument handling |
| `test_database.py` | 2 | Timestep snapshot persistence and run-type filtering |
| `test_energyplus_env.py` | 4 | The occupancy-transition safety-net fix, via mocked EnergyPlus objects |
| `test_agent.py` | 5 | The real Ollama + qwen2.5:3b tool-calling loop, including forced-failure handling |
| **Total** | **60** | **All passing** |

Run it yourself: `python3 -m pytest tests/ -q`

---

## 2. How Real Bad Situations Were Found and Handled

These weren't hypothetical edge cases written into a test after the fact —
each was a real failure encountered while actually running the system,
investigated to a specific root cause, then fixed and re-verified.

### A hard-fail that didn't actually fail
Handle validation is supposed to stop the whole simulation the instant any
EnergyPlus sensor/actuator handle is invalid. Deliberately breaking a handle
name to test this produced a **"PASSED"** result anyway, with 22 invalid
handles quietly logged as console errors nobody was watching for. Root
cause: a Python `raise` inside a ctypes callback invoked by EnergyPlus is
silently swallowed — the C code just keeps running past it. Fixed by
setting a flag inside the callback and raising for real once control
returns to normal Python code, then confirmed the exception actually
propagates on a second deliberate break.

### An API that doesn't implement its own documentation
Every sensor was supposed to register at runtime via `request_variable()` —
the documented mechanism. All 22 sensor handles came back invalid anyway.
Investigation (a diagnostic requesting variables in different orders)
showed only variables *already* declared as static `Output:Variable`
objects in the IDF ever resolved — `request_variable()` doesn't register
anything in this EnergyPlus build, regardless of call order or naming.
Fixed by declaring every needed sensor statically in the IDF.

### Wrong EnergyPlus variable names, silently
`"Zone Air Temperature"` never resolves per-zone in this build — only
`"Zone Mean Air Temperature"` does, despite both being listed as legal in
the `.rdd`. Facility-level electricity variables required the exact key
`"Whole Building"` — a blank key or `"*"` both failed silently, with no
error, just an invalid handle to catch at startup validation.

### The setpoint the dashboard was quietly making up
Every zone's displayed setpoint showed `0.0` from the very first tick.
`get_actuator_value()` — the natural-sounding function for "what is this
set to" — actually means "what has *this process* written to it," and
returns `0.0` until ARIA itself writes something. Fixed by reading a
separate report variable for display, while keeping the actuator handles
for writing.

### The clamp that could un-clamp itself
Caught by reasoning through an edge case on paper before it could become a
live incident: a zone at 15°C (valid unoccupied heating) becoming occupied
requires 20–24°C. The original two-step clamp (absolute bounds, then a
±2°C ramp-rate window) applied the ramp window *around the old 15°C value*
second, pulling the result down to **17°C** — below the occupied floor the
first clamp was supposed to guarantee. Proved numerically on paper, then
fixed by re-applying the absolute clamp after the ramp clamp, since safety
must always outrank ramp-smoothing.

### The model that almost always ran out of time
A 48-cycle test run hit a **52% fallback rate** — the model routinely made
5–9 tool calls per cycle (one setpoint call per zone), and each round-trip
carried the entire growing conversation history. Redesigned the tools to
accept a batched `zones` array; re-measured on the identical test at
**31.9s average cycle time and 19% fallback** — see
[`ARCHITECTURE.md`](ARCHITECTURE.md) for the full before/after numbers.

### The crash hiding inside a mostly-successful tool call
A handful of cycles failed with `KeyError: 'actions_taken'` — the model had
called `log_decision` correctly, just without one optional argument. A
mostly-correct call was crashing the whole cycle. Fixed with `.get()`
defaults and per-tool error isolation, so one malformed argument fails only
that tool call, not the cycle's reasoning.

### The seven-day simulation that had never actually run
The single most significant finding of the build. A test expected to cover
roughly one simulated day produced **1,344 ticks** — almost 4x too many —
and the log showed two design-day "Warming up" sequences, not the real July
window. Every tick, in every earlier test, belonged to an HVAC-sizing
design-day environment. `SimulationControl`'s "Run Simulation for Weather
File Run Periods" was set to `NO` — **the real simulation had never
executed once**, in any earlier phase. Fixed by flipping that flag and
filtering on `kind_of_sim` + `warmup_flag` so only real, post-warmup,
weather-period ticks count — verified afterward to produce exactly 672
ticks spanning precisely July 1st through July 7th.

### The dashboard that only worked from a Python REPL
Every panel function worked fine in a test script. The moment
`streamlit run dashboard/app.py` actually launched in a real browser:
`ModuleNotFoundError: No module named 'config'`. Streamlit only adds its
own script's directory to `sys.path`, not the project root. Caught by
opening the real page and taking a real screenshot rather than trusting
unit-level checks — fixed with an explicit `sys.path` bootstrap at the top
of `dashboard/app.py`.

### The setpoint that outlived its own occupancy state
Found **live, mid-run**, during the actual 7-day evaluation — not in a
pre-written test. A routine audit of the accumulating database turned up 14
timestep-zone rows where a zone's setpoint sat outside the bounds for its
*current* occupancy state (e.g. 18°C heating, valid unoccupied, persisting
for two ticks after the zone became occupied and required ≥20°C). Every
flagged value was checked against the absolute safety envelope — none ever
left it; this was a comfort-policy gap, not a safety breach. Root cause:
the safety validator only clamps values the LLM actively proposes that
cycle — an untouched zone's existing setpoint was never re-checked against
a newly-changed occupancy state. Fixed with a continuous re-validation pass
every tick, independent of the model's tool calls, and covered by 4 new
tests reproducing the exact scenario with mocked EnergyPlus objects.

**The judgment call that came with it:** the live run had already passed
the halfway mark when this was found. Restarting to apply the fix
retroactively would have discarded several hours of real, already-valid
computed results for a gap already confirmed non-dangerous. We let the run
finish rather than restart it, and reported the tradeoff plainly rather
than quietly re-running for a "clean" number.

---

## 3. The Real Run: 6 Hours 40 Minutes, and What That Actually Tests

Short test runs (a handful of cycles, a simulated day) are enough to prove
logic works. They are not enough to prove a system survives. The final
evaluation run — **672 decision cycles, 7 full simulated days, 6 hours and
40 minutes of continuous, real, local LLM inference, start to finish** —
is what actually validated System Integration under real conditions:

- **No restarts, no crashes.** The entire closed loop — EnergyPlus,
  qwen2.5:3b, the safety validator, the database writer — held together
  for 6h40m of unattended, continuous operation. A single unhandled
  exception, memory leak, or stalled model call anywhere in that loop would
  have surfaced over a run this long, in a way a 48-cycle test simply
  cannot expose.
- **Sustained local load.** Running EnergyPlus's physics engine and a
  locally-hosted 3B-parameter model back-to-back for 6h40m straight is a
  meaningfully heavier, longer sustained load on a single machine than any
  short development test in this project — the kind of duration where
  thermal throttling, memory growth, or a slow resource leak would have
  had time to actually show up, and none did.
- **Real bugs only a long run can surface.** The occupancy-transition gap
  (Section 2) was invisible in every short test that came before it — it
  only appeared, and was caught, by auditing live data partway through this
  specific long run.
- **A genuine reliability number, not a projected one.** 672/672 decisions
  completed, 667 LLM-authored (99.3%), 5 auto-generated on outright LLM
  failure (0.74%), 0 absolute safety-envelope violations — measured over
  the full 6h40m, not extrapolated from a shorter sample.

The no-AI baseline comparison, by contrast, completed in under 2 seconds
for the identical 672 timesteps (pure physics, zero LLM calls) — the
entire 6h40m duration belongs to real model reasoning, run once, honestly,
start to finish.

---

## 4. Final Verified Numbers

| Check | Result |
|---|---|
| Automated tests | 60 / 60 passing |
| Real 7-day decision cycles completed | 672 / 672 |
| LLM-authored decisions | 667 (99.3%) |
| Auto-generated fallback decisions | 5 (0.74%) |
| Crashes across the full run | 0 |
| Absolute safety-envelope violations | 0 |
| Continuous real run duration | 6 hours 40 minutes |

For the full incident-by-incident history (13 documented challenges, in
more narrative detail than the summary above), see
[`docs/CHALLENGES.md`](docs/CHALLENGES.md). For the phase-by-phase build
log, see [`docs/TESTING.md`](docs/TESTING.md).

---

*Back to [`README.md`](README.md), or continue to
[`ARCHITECTURE.md`](ARCHITECTURE.md) for the full system design.*

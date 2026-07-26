# ARIA — Challenges & How We Solved Them

A handful of the situations below looked like nothing at first — a slightly
odd number, a quiet log line, a test that passed too easily. Each one turned
out to matter. This documents them as standalone incidents: what we saw,
what we thought was happening, what was actually happening, and how we
confirmed the fix.

---

## 1. The dependency install that couldn't decide on a version

**What happened:** `pip install -r requirements.txt` failed on a completely
fresh setup. `eppy==0.5.65` — a specific version pin — simply doesn't exist
on PyPI.

**How we tackled it:** Bumped the pin to a version that exists. That
immediately exposed a second problem: with every package strictly pinned
(`==`), `eppy` and `pytest`'s dependency trees were mutually unsatisfiable —
pip couldn't resolve the graph at all. Switching every pin to a `>=` floor
resolved cleanly. We didn't just trust that in the already-populated dev
environment (which can hide a broken lock file behind packages installed
some other way) — we re-ran the install in a completely throwaway virtualenv
to confirm it resolves from nothing.

---

## 2. The safety check that lied about passing

**What happened:** Handle validation is supposed to hard-fail the whole
simulation the moment any sensor/actuator handle is invalid. We deliberately
broke a handle name to test this — and the run reported **PASSED**, with 22
invalid handles quietly logged as errors in the console output nobody was
watching for.

**Root cause:** The validation code correctly detected the invalid handles
and called `raise RuntimeError(...)` — inside a Python callback invoked by
EnergyPlus through ctypes. Ctypes callbacks that raise an exception don't
propagate it to the caller; they print
`"Exception ignored while calling ctypes callback function"` to the console
and the C code just continues as if nothing happened. Our own "hard fail"
had no teeth.

**The fix:** Instead of raising inside the callback, the callback sets an
error flag on the environment object. After `run_energyplus()` returns
control to normal Python code, we check that flag and raise there — where
an exception behaves like an exception again.

**Confirmed by:** deliberately breaking a handle name a second time, this
time watching for the exception to actually propagate to a `try/except` in
calling code. It did.

---

## 3. The API that documents a feature it doesn't implement

**What happened:** Every sensor ARIA reads is supposed to be registered at
runtime via `request_variable()` — that's the documented, standard way to
tell EnergyPlus "I want to read this." All 22 sensor handles came back
invalid, while the 10 actuator handles (which don't need requesting) were
fine.

**Investigation:** We wrote a small diagnostic that requested three
variables in different orders and logged which ones actually resolved to a
valid handle. Only two ever worked — and they were the *only two* variables
already declared as static `Output:Variable` objects in the IDF file itself.
Every variable we tried to request purely at runtime, regardless of name or
call order, failed silently.

**Root cause:** in this EnergyPlus build, `request_variable()` doesn't
register anything. It's a documented API that doesn't do what its
documentation says.

**The fix:** every variable ARIA needs is declared statically in the IDF as
an `Output:Variable` object, and handles are fetched from there. This is
also how established EnergyPlus-based RL/control research code bases handle
it, we later realized — a safer default than trusting the runtime API.

---

## 4. The setpoint the dashboard was quietly making up

**What happened:** Once sensor reading was working, every zone's displayed
cooling/heating setpoint showed `0.0` — for every zone, every timestep, from
the very first tick.

**Root cause:** `get_actuator_value()` — the natural-sounding function for
"what is this actuator currently set to" — actually means "what value has
*this code* most recently written to this actuator." Before ARIA has ever
written anything, it returns `0.0`, not the real schedule-driven setpoint
the building is actually running on.

**The fix:** for *display*, read a separate report variable
(`Zone Thermostat Heating/Cooling Setpoint Temperature`) that reflects the
real, currently-effective setpoint regardless of who set it. The actuator
handles are still used for *writing* — just not for reading back what's
currently in effect.

---

## 5. Timestamps that don't roll over

**What happened:** Log lines occasionally showed times like `22:63` —
not a valid clock time.

**Root cause:** EnergyPlus's `minutes()` reports the *end* of the current
timestep without normalizing it — it can be exactly 60, or (we found by
logging many consecutive values) even higher, like 63, apparently from
timestep-shortening during warmup convergence. A first attempt at a fix
special-cased `minute == 60` and rolled it to the next hour — and still
produced `22:63`, because 63 isn't 60.

**The fix:** real modulo arithmetic — `hour + minute // 60`, `minute % 60` —
instead of a single special case, verified against the actual irregular
values observed in the log rather than an assumption about what "should"
happen.

---

## 6. The clamp that could un-clamp itself

**What happened:** nothing observable yet — this one was caught by working
through the safety validator's logic on paper for an edge case, before it
could ever show up as a real incident.

**The scenario:** a zone sits at 15°C heating overnight (a valid,
safety-clamped *unoccupied* value). At 8am someone walks in — the zone
becomes *occupied*, which requires 20-24°C. The validator clamps in two
steps: first to the absolute bounds for the current state, then to a
ramp-rate window (±2°C from the current value) to prevent thermal shock.
Applied in that order, a proposed 22°C gets clamped to the occupied range
first (fine, still 22°C), then clamped to the ramp window around the *old*
15°C value — pulling the final result down to **17°C**, which is *below*
the occupied floor the first clamp was supposed to guarantee.

**Why it mattered:** this is exactly the kind of bug that looks fine in
every normal-case test and only breaks on the specific transition it was
never checked against — and it would have meant an occupied zone legitimately
sitting a few degrees below its own safety floor, for real people.

**The fix:** re-apply the absolute-bounds clamp *again*, after the
ramp-rate clamp. Safety bounds must always win over ramp-smoothing, per the
system's own stated priority order (safety before comfort).

**Confirmed by:** implementing the original (buggy) two-step version
side-by-side and proving numerically that it really does produce 17°C,
then confirming the fixed three-step version produces the correct 20°C.

---

## 7. The model that almost always ran out of time

**What happened:** in a 48-cycle test run, **52%** of decisions fell back to
the synthetic audit log — meaning the LLM failed to complete over half the
time. Average cycle time was 46 seconds; the configured timeout was 45.

**Investigation:** logging the exact tool calls made per cycle showed the
model routinely making 5-9 separate tool calls per cycle — one
`set_hvac_setpoint` call *per zone*, sometimes also one `set_lighting_level`
call per zone. Each tool call is a full round-trip back to the model,
carrying the entire growing conversation history with it. The model wasn't
slow — the *conversation* was long.

**The fix:** redesigned the two setpoint/lighting tools to accept a `zones`
array, so the model can (and is explicitly instructed to) handle every zone
that needs a change in a single call. A cycle that needed 9 round-trips
before could now need as few as 2.

**Result, re-measured on the identical test:** average cycle time dropped
from 46.0s to 31.9s; fallback rate dropped from 52% to 19% — from one
architectural change, not a faster model.

---

## 8. The crash hiding inside a mostly-successful tool call

**What happened:** during the very run that validated fix #7, a handful of
cycles still failed — this time with `KeyError: 'actions_taken'`, not a
timeout.

**Root cause:** the model *did* call `log_decision` — it just omitted the
`actions_taken` argument. Our code read `args["actions_taken"]` directly,
so a mostly-correct tool call from the model crashed the entire decision
cycle into total fallback, discarding the reasoning text it *did* provide.

**The fix, at two levels:** `log_decision` now uses `.get()` with sensible
defaults, so a partially-complete call still produces a real audit entry
instead of nothing at all. Separately, *all* tool dispatch is now wrapped so
a missing or malformed argument on *any* of the four tools fails just that
one tool call — not the whole cycle — since this is a systemic risk with a
small model, not something specific to one tool.

---

## 9. The seven-day simulation that had never actually run

This is the one that mattered most.

**What happened:** while double-checking tick counts for a short test run,
the numbers didn't add up. A run expected to cover roughly one simulated day
produced **1344** ticks — almost four times too many — and the console log
showed *two* separate "Warming up" sequences before a "Starting Simulation"
message, for design days in January and July, not the actual July 1-7
window the project targets.

**Investigation:** we instrumented every tick with EnergyPlus's own
environment/kind-of-simulation identifiers and printed which "environment"
each tick belonged to. Every single tick — in every test run so far, across
every earlier phase — belonged to one of two HVAC-sizing design-day
environments. Zero ticks belonged to the real, weather-file-driven run
period. Digging into the IDF's `SimulationControl` object confirmed why:
**"Run Simulation for Weather File Run Periods" was set to `NO`.** The real
simulation had never once executed — every earlier test, at every earlier
phase, had been unknowingly running against EnergyPlus's throwaway sizing
calculations instead of real building data.

**Why this was serious:** the underlying code being tested (handle
validation, sensor reading, safety clamping, the agent loop) was still
being exercised correctly — sizing-day physics behaves similarly to real
weather-driven physics — but no test before this point had touched a single
real July data point, and nobody would have known without checking the tick
count arithmetic against expectation.

**The fix, in two parts:**
1. Flipped the `SimulationControl` flag so the real weather period actually
   runs.
2. Added a filter (`kind_of_sim` + `warmup_flag`) so only real, post-warmup,
   weather-period ticks are treated as "real" data — otherwise, a "7-day
   run" would silently include roughly 2,900 extra sizing/warmup ticks
   alongside the real 672, at real LLM-latency cost (~25 hours of pure waste
   at measured speeds).

**Confirmed by:** re-running and checking the exact tick count and date
range — exactly 672 ticks, spanning precisely July 1st 00:15 through
July 7th 00:00, matching the plan's own expected "672 decisions" figure for
the first time.

---

## 10. The dashboard that only worked from the Python REPL

**What happened:** every panel-rendering function worked fine when called
directly in a test script. The moment the dashboard was actually launched
with `streamlit run dashboard/app.py` and opened in a real browser, it
showed a bare traceback: `ModuleNotFoundError: No module named 'config'`.

**Root cause:** running a script directly (`python3 main.py`) from the
project root puts that root directory on Python's import path automatically,
which is what let every project-root-relative import
(`from config.env_loader import ...`) work throughout the rest of the
project. Streamlit does not do this — it only adds the script's own
directory (`dashboard/`) to the path, so the exact same import statement
that worked everywhere else in the codebase 404'd the instant Streamlit
launched it.

**Why we actually caught it:** by opening the real page in a real browser
and taking a real screenshot, rather than assuming the app worked because
the underlying functions had unit-style checks. The error was invisible
from anywhere except the rendered page itself.

**The fix:** a small `sys.path` bootstrap at the very top of
`dashboard/app.py`, before any project import, inserting the project root
explicitly.

**Confirmed by:** restarting the server and re-capturing a real screenshot —
all four panels rendering with real data, including a decision log showing
actual model-generated reasoning text.

---

## 11. The setpoint that outlived its own occupancy state

**What happened:** partway through the real, live 7-day run, a routine audit
of the accumulating database (not a pre-written test — a live check against
real data) turned up 14 timestep-zone rows where a zone's setpoint sat
outside the bounds for its *current* occupancy state.

**Investigation:** pulling the exact rows plus the previous tick's values
for each showed a consistent pattern — e.g. a zone at 18°C heating (a
perfectly valid *unoccupied* value) the tick before it became occupied,
still at 18°C (now below the *occupied* floor of 20°C) one or two ticks
*after* becoming occupied. We also checked whether any of these values ever
left the absolute safe envelope across both occupancy states (20-32°C
cooling, 15-24°C heating) — none did, at any point.

**Root cause:** the safety validator only clamps values the LLM *actively
proposes* that cycle. If the model's tool calls don't happen to touch a
zone whose occupancy just changed, nothing re-checks that zone's *existing*
setpoint against its *new* occupancy state — it just sits there, valid for
a state it's no longer in, until the model happens to revisit it.

**The fix:** in `energyplus_env.py`, after applying whatever the LLM
actively decided this cycle, every zone *not* touched by a pending write or
an active precool override is now re-validated against its current
occupancy state every single tick — independent of whether the model's
tool calls happened to mention it. Covered by 4 new unit tests built with
mocked EnergyPlus objects, reproducing the exact scenario observed live
(including confirming an already-correct zone is left untouched, and a
zone the LLM *did* just set isn't redundantly double-written).

**A judgment call, not just a fix:** the running 7-day simulation had
already progressed past the halfway mark by the time this was found and
fixed. Python doesn't hot-reload a running process, so the fix couldn't
apply retroactively without restarting — discarding several hours of real,
otherwise-valid computed results, for a gap already confirmed non-dangerous.
We let the live run finish rather than restart it, and documented the
tradeoff plainly rather than quietly re-running and presenting only the
"clean" version.

---

## 12. The comfort number that was worse than the baseline's

**What happened:** with the complete 7-day run finished, the baseline
(static, human-programmed schedule) hit **100%** occupied-zone PMV
compliance. ARIA hit **84.3%**. On first glance that reads as a straight
loss on comfort — exactly the tradeoff the project set out to avoid.

**Investigation, before drawing any conclusion:** we didn't stop at the
headline percentage. Pulling the actual distribution of ARIA's violations
showed 233 of 261 (89%) were on the *cold* side, not the hot side, and
checking their hour-of-day showed they cluster hardest between 6-9am —
right as zones transition from unoccupied to occupied — rather than being
spread evenly across the whole day. Comparing average setpoints during
occupied hours told the rest of the story: ARIA ran cooling setpoints
noticeably lower on average (23.7°C vs. the baseline's fixed 24.3°C),
consistent with the system prompt's carbon-driven instruction to lower
cooling toward 21°C during low-carbon overnight windows.

**Root cause:** a static schedule trivially gets 100% compliance because
it's tuned to sit in the safe middle of the comfort band permanently, at
the cost of exactly the energy ARIA exists to save. ARIA's carbon-aware
precooling logic doesn't have an explicit instruction to fully back off
its energy-driven setpoint by the moment a zone actually becomes occupied —
so a cooling setpoint lowered for a genuinely good reason (cheap, clean
grid power overnight) sometimes hadn't fully corrected upward yet when
people arrived, producing a real but transient overcooling window right at
the occupied threshold — not a sustained comfort problem throughout the day
(the average occupied PMV across the whole run, -0.17, sits comfortably
inside the [-0.5, +0.5] target).

**Why we're reporting this rather than reframing it:** an honest tradeoff
analysis is worth more than a headline number, and this is a legitimate,
specific, fixable finding — not damage control. The energy savings are real
and are in fact *larger* during occupied hours specifically (-20.1%) than
overall (-15.9%), meaning the savings aren't merely coming from turning
things off when no one's around — they're partly coming from exactly the
setpoint choices that produced this comfort shortfall. That's the real
tradeoff the system made, stated plainly instead of hidden behind an
aggregate number.

**What we'd change with more time:** tighten the system prompt so
carbon-driven setpoint changes are scoped explicitly to unoccupied zones,
or have the safety/comfort layer take priority over an in-flight carbon
optimization the moment a zone's occupancy flips to occupied — the same
pattern already built for the safety bounds in Challenge #11, extended to
the comfort target rather than just the hard safety envelope.

---

## 13. The sanity check that re-ran a multi-hour simulation by accident

**What happened:** during a final pass to confirm the codebase still worked
after a round of cleanup, a plain `import run_baseline` — intended as a
zero-risk syntax/import check — triggered a full, real EnergyPlus baseline
simulation. It completed in seconds (the baseline run has no LLM calls), but
it silently inserted a second, duplicate copy of all 3,360 baseline rows
into the same production database already holding the completed, real
7-day results.

**Root cause:** `run_baseline.py` had no `if __name__ == "__main__":` guard.
Its baseline-run logic sat directly at module level, so anything that
imported it — a test, a REPL, a future script — executed the run as a side
effect of import, silently, whether or not that was the intent. `main.py`
already had this guard; `run_baseline.py` did not, an inconsistency that
went unnoticed until an import actually triggered it.

**Why this mattered:** the duplication wasn't just wasted compute — every
downstream number (the dashboard's energy comparison, the Results table,
the savings estimate) reads from that same table, and a doubled baseline
total would have silently thrown off every comparison against it, with
no error or warning anywhere in that path.

**The fix:** confirmed the two sets of rows were exact duplicates (same
IDs pattern, identical values — expected, since EnergyPlus physics is
deterministic given the same inputs) before deleting the duplicate batch,
then wrapped `run_baseline.py`'s entry logic in `def main(): ... if
__name__ == "__main__": main()`, matching `main.py`'s existing pattern.
Verified afterward that plainly importing the module no longer executes
anything, and that the database's numbers exactly match the values
recorded before the incident.

**The general lesson:** a script that performs a real, expensive, stateful
action as a side effect of being imported — rather than only when
deliberately run — is a hazard regardless of how "safe" the triggering
action looks. The fix generalizes: every entry-point script in this project
now follows the same `if __name__ == "__main__":` convention, verified by
checking that importing each one is a no-op.

---

## The common thread

Almost none of these were caught by a test that was written to check for
them specifically — the ramp-rate bug was found by reasoning through an
edge case on paper; the sizing-period bug was found by noticing arithmetic
that didn't add up; the dashboard bug was found by actually looking at a
screenshot instead of trusting the code path; the occupancy-transition bug
was found by auditing real accumulating data mid-run instead of only
checking results at the end. The pattern worth keeping: verify against the
real system, at the real boundary a user or the data would actually hit,
not just against the assumption of how it's supposed to behave.

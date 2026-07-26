# ARIA — Autonomous Reasoning & Intelligent Actuator

### Eco-Loop Building Agent · Honeywell Hackathon 2026
**Prepared by Konepalli Harshavardhan**

ARIA is an AI building management agent that autonomously controls a
commercial office building's HVAC and lighting to cut energy use and carbon
impact — without sacrificing occupant comfort. It runs a local LLM in a
closed control loop against a physics-accurate EnergyPlus simulation of a
real 5-zone office building, reasoning over live sensor data every 15
simulated minutes and deciding what to change, if anything.

There is no cloud dependency anywhere in the control loop: the simulation,
the language model, and the database are all local. ARIA reads sensor data,
reasons about it, acts on the building, and explains itself — every single
cycle, with a guaranteed audit trail.

### 📖 Read next

This README covers the full system. Two companion documents go deeper on
specific parts of the story, both in this same root folder:

- **[`ARCHITECTURE.md`](ARCHITECTURE.md)** — the system flow diagram, every
  component's responsibility, and a dedicated section on the real 7-day
  run: **6 hours 40 minutes of continuous, real, local LLM inference**,
  start to finish, no restarts.
- **[`TESTING.md`](TESTING.md)** — how real failures were found and fixed
  (EnergyPlus variable mismatches, a hard-fail that didn't fail, a bug
  caught live mid-run), the full automated test suite, and what a 6h40m
  continuous run actually demands of a system under real, sustained load.

### At a glance

| | |
|---|---|
| **Energy reduction, 7-day run** | **-15.9%** vs. no-AI baseline (-20.1% during occupied hours) |
| **Decision cycles completed** | **672 / 672** — 99.3% LLM-authored |
| **Continuous real run duration** | **6 hours 40 minutes**, zero crashes, zero restarts |
| **Safety violations** | **0** — never left the hard temperature envelope, ever |
| **Automated tests** | **60 / 60** passing |
| **Cloud dependency** | **None** — simulation, LLM, and database are all local |

---

## The Problem

Commercial buildings account for a large share of global energy consumption,
and most run on static, human-programmed thermostat schedules that can't
adapt to real-time occupancy, weather, or the carbon intensity of the
electricity grid. A schedule that's efficient in January is wasteful in July;
a set-and-forget setpoint can't tell the difference between a full office
and an empty one, or between a grid running on cheap wind power and one
running on peaker plants.

ARIA replaces that static schedule with a reasoning agent that treats every
decision as a fresh judgment call, grounded in what's actually happening in
the building and on the grid right now.

---

## Custom Agentic Tools (not MCP)

Per the problem statement's choice of "an MCP Server or custom agentic
tools," ARIA implements the latter: tools are plain JSON schemas
(`agent/tool_registry.py`) called directly through Ollama's native
function-calling API — no JSON-RPC layer, no external protocol surface.

Four tools, deliberately minimal:

| Tool | Purpose |
|---|---|
| `set_hvac_setpoint` | Cooling/heating setpoints for one or more zones |
| `set_lighting_level` | Lighting fraction (0.0-1.0) for one or more zones |
| `schedule_precool` | Schedule a building-wide precooling event |
| `log_decision` | Record reasoning and actions taken — required every cycle |

**Batched calls.** `set_hvac_setpoint` and `set_lighting_level` both accept
either a single zone or a `zones: [...]` array covering every zone that
needs a change in one call — the model is explicitly instructed to batch
rather than call a tool once per zone, since each round-trip to the LLM
carries the full growing conversation history and batching is what keeps a
5-zone decision cycle to a small, bounded number of model calls.

**Tool execution is defensive by design.** A tool call with a missing or
malformed argument fails just that one call — the reasoning that led to it
and the rest of the cycle continue rather than aborting outright.

## Reasoning & Prompt Design

Every cycle, ARIA's system prompt enforces a strict, non-negotiable
priority order:

1. **Safety** — zone temperatures stay within hard bounds. No exceptions.
2. **Comfort** — occupied zones stay within an ISO 7730 PMV comfort band.
3. **Carbon** — prefer action during low-carbon grid windows once 1 and 2
   are satisfied.
4. **Energy** — minimize consumption once 1, 2, and 3 are all satisfied.

All current sensor data is pre-packed directly into the prompt — zone
temperatures, PMV, occupancy, CO2, current setpoints, building energy draw,
and live grid carbon intensity — so the model never needs a "read sensor"
tool call of its own; it reasons over what it's already been given and acts.
Temperature is fixed low (0.1) for near-deterministic control decisions.

## Safety Architecture

Safety limits are hard-coded in `agent/safety_validator.py`, independent of
`config/settings.yaml` — a configuration edit alone can never weaken a
safety bound; the config file carries the same numbers only for reference.

- **Absolute bounds**, different for occupied vs. unoccupied zones:
  cooling 20-26°C occupied / 26-32°C unoccupied; heating 20-24°C occupied /
  15-20°C unoccupied.
- **Ramp-rate limiting** — no more than a 2°C setpoint change per cycle, to
  prevent thermal shock — with absolute bounds re-applied after the ramp
  clamp, so ramp-smoothing can never itself push a value out of the hard
  safety range.
- **Occupancy-transition enforcement** — every zone is re-validated against
  its *current* occupancy state every cycle, whether or not the model's
  tool calls happened to touch that zone. A zone's setpoint can never
  silently outlive the occupancy state it was set for.
- **Lighting floor** — occupied zones are floored at a minimum lighting
  fraction (0.2); only unoccupied zones can go fully dark.
- **CO2 alarm threshold** (1000 ppm, ASHRAE 62.1) is available to the
  reasoning loop as a hard signal, independent of the comfort/energy
  tradeoff.

Every proposed setpoint passes through this gate before it ever reaches the
building — the model can *propose* anything; it can never *apply* a value
outside the safe range.

## System Reliability & Self-Correction Loops

A 3B local model will occasionally misbehave — time out, skip a required
tool call, or send a malformed argument. ARIA is built to run for days
unattended anyway, by treating every one of those as an expected, handled
case rather than a crash:

- **Hard-fail startup validation.** Every sensor/actuator handle ARIA needs
  is validated before the simulation runs a single real timestep — if any
  are invalid, the run stops immediately with a clear error instead of
  silently producing garbage data for days.
- **Guaranteed audit trail, enforced in software.** `log_decision` is
  required every cycle. If the model's turn ends without calling it, the
  system writes an auditable entry itself — clearly flagged
  `auto_generated` rather than model-authored. The dashboard shows both
  counts honestly (e.g. "668 LLM-authored, 4 auto-generated") instead of
  hiding the difference — a transparent reliability number is more credible
  than a hidden one.
- **Fallback on outright LLM failure.** `agent/fallback_handler.py` holds
  the last known-good setpoints; if an LLM call fails entirely (timeout,
  malformed response), ARIA applies those rather than leaving the building
  un-controlled for that cycle, and records the failure. A per-cycle
  timeout (default 45s) bounds how long any single decision can take.
- **Per-tool error isolation.** A malformed or missing argument on any one
  tool call fails just that call — not the whole decision cycle — since a
  small model occasionally dropping one field is expected, not fatal.
- **Continuous occupancy-state enforcement.** Independent of what the LLM
  chose to call this cycle, every zone's setpoint is re-validated against
  its *current* occupancy state every tick — a zone's setpoint can never
  silently outlive the occupancy state it was set for, whether or not the
  model happened to revisit it.
- **Latency self-correction, measured and closed.** The tool interface
  itself was redesigned after measuring real per-cycle latency against the
  live model — batching every zone into a single tool call cut average
  cycle time by ~30% and the fallback rate by more than half, from one
  architectural change driven by observed data rather than guesswork.

Together these mean the closed loop is designed to survive a multi-day,
unattended run without crashing or stalling — proven, not just designed for:
the real evaluation run held together for **6 hours 40 minutes** of
continuous operation with zero crashes. See [`TESTING.md`](TESTING.md) for
the full reliability story and [`docs/TESTING.md`](docs/TESTING.md) for the
phase-by-phase build log.

## Comfort Modeling

`comfort/pmv.py` implements the Fanger PMV/PPD model (ISO 7730 / ASHRAE 55
Appendix D) from first principles — air temperature, mean radiant
temperature, relative humidity, air speed, metabolic rate, and clothing
insulation combine into a single Predicted Mean Vote (-3 cold to +3 hot) and
a Predicted Percentage Dissatisfied. ARIA targets PMV within [-0.5, +0.5]
for every occupied zone.

Per-zone air temperature and mean radiant temperature come directly from
EnergyPlus; relative humidity and air speed use fixed typical-office
assumptions, since this building model doesn't expose per-zone sensors for
them — documented plainly in code rather than presented as measured.

## Carbon Awareness

`carbon/grid_intensity.py` checks the Electricity Maps API when an API key
is configured, and otherwise uses a realistic time-of-day mock profile —
deliberately shaped so it genuinely dips into low-carbon territory overnight
and crosses into high-carbon territory at evening peak, so both carbon
strategies are actually exercised rather than one being permanently
unreachable:

- **DEFER** — grid carbon above the high threshold (350 gCO2/kWh default):
  avoid non-essential cooling.
- **PRECOOL** — grid carbon below the low threshold (120 gCO2/kWh default):
  pre-cool the building's thermal mass while power is clean.
- **NORMAL** — between the two: optimize for comfort and energy as usual.

## Precooling

`schedule_precool` lets ARIA schedule a building-wide precooling event ahead
of an anticipated high-carbon or high-demand period — a target temperature
and duration, applied for that window across all zones once it begins, with
the target temperature safety-validated per zone (occupied vs. unoccupied)
at the moment it's actually applied.

## Building Model & EnergyPlus Integration

- **Model**: DOE Small Office reference building — 4 perimeter zones + 1
  core zone, a real 5-zone commercial office layout — simulated against a
  real Chicago TMY3 weather file.
- **Live actuator control** (`simulation/energyplus_env.py`) reads every
  sensor and writes every actuator through EnergyPlus's real-time data
  exchange API — no static schedule edits; ARIA's setpoints and lighting
  levels are applied to the running simulation every timestep.
- **CO2 physics** is fully simulated (`ZoneAirContaminantBalance`), not
  mocked, so the CO2 signal available to the reasoning loop reflects real
  occupancy-driven accumulation.
- **Lighting control** goes through each zone's actual electrical lighting
  actuator, scaled by that zone's real design wattage — a 0.0-1.0 fraction
  from the LLM maps to genuine watts, not an abstract proxy.
- **Occupancy** is read as a live 0-1 schedule fraction per zone, reflecting
  this building's real, shared occupancy pattern rather than a synthetic
  per-zone headcount.
- **`simulation/state_manager.py`** provides thread-safe handoff between the
  EnergyPlus callback thread and the reasoning/dashboard layers — every
  pending actuator write is staged here and applied atomically each tick.

## Data & Persistence

SQLite (`aria.db`) stores the complete run history so the LLM never has to
reason over it directly — only the current snapshot goes into the prompt;
everything else is queried by the dashboard.

- **`decisions`** — every cycle's reasoning, actions taken, whether it was
  LLM-authored or auto-generated, and whether a fallback was used.
- **`timestep_data`** — one row per zone per timestep, for both the ARIA run
  and the no-AI baseline run (`run_type` distinguishes them) — temperature,
  MRT, PMV/PPD, occupancy, CO2, setpoints, and building-wide energy draw.

## Dashboard

A Streamlit + Plotly dashboard reads directly from `aria.db`:

- **Energy** — ARIA vs. baseline building demand over time, plus total kWh
  and percentage change.
- **Comfort** — a zone x time PMV heatmap using a proper diverging color
  scale (cold/neutral/hot), with an occupied-zone compliance stat.
- **Savings** — cumulative energy, estimated cost, and estimated CO2 avoided
  versus the baseline run.
- **Decision Log** — the full reasoning history, LLM-authored vs.
  auto-generated entries visually distinguished, expandable per-cycle detail.

## Configuration

Everything tunable lives in one place, `config/settings.yaml`, loaded
through `config/env_loader.py`:

- **`energyplus`** — model/weather paths, simulation timestep, run period.
- **`llm`** — model name, host, temperature, per-cycle timeout, max tool
  iterations, fallback behavior.
- **`safety`** — every bound and the ramp-rate limit (mirrored, not
  sourced, from the hard-coded validator).
- **`carbon`** — Electricity Maps API key (blank → mock profile), grid
  region, DEFER/PRECOOL thresholds.
- **`targets`** — daily energy and carbon budgets, shown on the dashboard.
- **`dashboard`** — port and auto-refresh interval.

`env_loader.py` resolves the EnergyPlus install path with a clear priority:
an `ENERGYPLUS_DIR` environment variable, then a machine-local `.aria_env`
file (written by `scripts/setup.sh`), then a hardcoded macOS default — so
the same codebase runs unmodified across machines and launch methods
(terminal, subprocess, Streamlit).

## Project Structure

```
aria/
├── main.py                    # Full ARIA run: EnergyPlus + LLM agent
├── run_baseline.py            # No-AI reference run, for comparison
│
├── agent/
│   ├── aria_agent.py          # Ollama tool-calling loop + audit guarantee
│   ├── tool_registry.py       # The 4 tools the LLM can call
│   ├── prompts.py             # System prompt + per-cycle state message
│   ├── safety_validator.py    # Hard setpoint/lighting bounds, always enforced
│   └── fallback_handler.py    # Last-known-good setpoints on LLM failure
│
├── simulation/
│   ├── energyplus_env.py      # EnergyPlus API wrapper + control loop
│   ├── baseline_env.py        # Same building, no AI control
│   ├── handle_registry.py     # Sensor/actuator handle management
│   ├── state_manager.py       # Thread-safe shared state
│   └── models/                # SmallOffice.idf + Chicago.epw
│
├── comfort/pmv.py             # ISO 7730 PMV/PPD comfort model
├── carbon/grid_intensity.py   # Live or mocked grid carbon intensity
│
├── data/
│   ├── database.py            # SQLite read/write layer
│   └── schema.py               # Table definitions
│
├── dashboard/                  # Streamlit app + 4 visualization panels
├── config/settings.yaml        # All tunable configuration, one place
└── scripts/
    ├── setup.sh                 # Environment + dependency setup
    └── export_aria_idf.py       # Post-run static IDF export deliverable
```

## Requirements

- macOS (Apple Silicon recommended)
- [EnergyPlus](https://energyplus.net) 24.x or 26.x, installed at `/Applications/EnergyPlus-*/`
- [Ollama](https://ollama.com) installed with `qwen2.5:3b` pulled
- Python 3.10+

## Quick Start

```bash
# 1. Clone and enter the project
git clone <repo-url> && cd aria

# 2. One-time setup: detects EnergyPlus, creates a venv, installs deps,
#    pulls the Ollama model if needed
bash scripts/setup.sh
source .venv/bin/activate

# 3. Run the no-AI baseline (fast — pure physics, no LLM)
bash scripts/run_baseline.sh

# 4. Run ARIA (real LLM reasoning + EnergyPlus — the full 7-day run took
#    6 hours 40 minutes on the reference hardware; expect several hours)
bash scripts/run_aria.sh

# 5. View results (run anytime — before, during, or after step 4)
streamlit run dashboard/app.py
```

## Post-Run Deliverable: Optimized IDF

Live actuator control is ARIA's primary control mechanism.
`scripts/export_aria_idf.py` additionally produces a static
`ARIA_Optimized.idf` snapshot with ARIA's converged setpoints baked into the
building model's existing schedules, as a secondary, portable deliverable:

```bash
python3 scripts/export_aria_idf.py
```

## Results

From a complete, real 7-day run — 672 decision cycles, both ARIA and the
no-AI baseline simulated against the identical building and weather data.

| Metric | Baseline | ARIA | Change |
|---|---|---|---|
| Energy (7 days) | 1251.2 kWh | 1051.8 kWh | **-15.9%** |
| Energy, occupied hours only | 952.7 kWh | 761.4 kWh | **-20.1%** |
| PMV compliance, occupied zones | 100.0% | 84.3% | see below |
| Avg PMV, occupied zones | ~0.0 (fixed schedule) | -0.17 | within [-0.5, +0.5] target |
| Est. cost saved | — | ~$29.90 | (@ $0.15/kWh, estimate) |
| Est. CO₂ avoided | — | ~43.7 kg | (@ mock grid average, estimate) |
| Decisions logged | — | 672 / 672 | 99.3% LLM-authored, 0.74% auto-generated |
| Absolute safety violations | — | 0 | never left the hard 20-32°C / 15-24°C envelope |
| Continuous run duration | < 2 sec (pure physics) | **6h 40m** | real, live LLM inference, zero crashes, zero restarts |

**On the comfort number, honestly:** the no-AI baseline hits 100% PMV
compliance because a static schedule is tuned to sit in the safe middle of
the comfort band at all times, at the cost of the energy that wastes.
ARIA's 84.3% reflects it actually taking on that tradeoff — and the
shortfall has a specific, identified cause, not an unexplained one: 233 of
261 occupied-zone violations (89%) were on the *cold* side, concentrated
most heavily in the early-morning hours (6-9am) as occupancy begins. This
lines up with the carbon-aware instruction to lower cooling toward 21°C
during low-carbon overnight windows — that setpoint change doesn't always
fully correct back upward by the time a zone becomes occupied, causing a
transient overcooling window right at the occupied threshold rather than a
sustained problem throughout the day. It's a real, energy-vs-comfort
tension worth being upfront about, not a hidden flaw — see
[`docs/CHALLENGES.md`](docs/CHALLENGES.md) for the full investigation.

## Tech Stack

EnergyPlus · Ollama (qwen2.5:3b) · Python · Streamlit · Plotly · SQLite · pandas

## Documentation Index

| Document | What it covers |
|---|---|
| [`README.md`](README.md) | This file — full project overview, setup, and results |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | System flow diagram, component design, and the real 7-day run (6h 40m continuous) |
| [`TESTING.md`](TESTING.md) | Test suite, real failures found and fixed, and what the long run actually stress-tested |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Deeper technical architecture notes |
| [`docs/TESTING.md`](docs/TESTING.md) | Full phase-by-phase build and verification log |
| [`docs/CHALLENGES.md`](docs/CHALLENGES.md) | All 13 real incidents, narrated in full detail |
| [`ARIA_Project_Documentation.pdf`](ARIA_Project_Documentation.pdf) | Single-file corporate-style project report |

# ARIA — Submission Requirements Mapping

This document maps each required submission item to exactly where it lives
in this repository, for quick reviewer verification.

---

## 1. Fully Functional Source Code

> A unified codebase (Python preferred) managing the EnergyPlus API
> wrapper, the LLM agent orchestration logic, and the communication bus.

| Component | Location |
|---|---|
| EnergyPlus API wrapper | [`simulation/energyplus_env.py`](simulation/energyplus_env.py) (data-exchange loop, sensor reads, actuator writes) + [`simulation/handle_registry.py`](simulation/handle_registry.py) (handle resolution/validation) |
| LLM agent orchestration logic | [`agent/aria_agent.py`](agent/aria_agent.py) (Ollama tool-calling loop) + [`agent/tool_registry.py`](agent/tool_registry.py) (the 4 tools) + [`agent/prompts.py`](agent/prompts.py) (system/user prompts) + [`agent/safety_validator.py`](agent/safety_validator.py) (hard bounds gate) + [`agent/fallback_handler.py`](agent/fallback_handler.py) (last-known-good fallback) |
| Communication bus | [`simulation/state_manager.py`](simulation/state_manager.py) — thread-safe shared state connecting the EnergyPlus callback thread to the agent and dashboard |
| Entry points | [`main.py`](main.py) (full ARIA run) + [`run_baseline.py`](run_baseline.py) (no-AI reference run) |
| Supporting modules | [`config/`](config/) (settings + environment resolution), [`data/`](data/) (SQLite schema + persistence), [`comfort/pmv.py`](comfort/pmv.py) (PMV/PPD model), [`carbon/grid_intensity.py`](carbon/grid_intensity.py) (grid carbon intensity), [`dashboard/`](dashboard/) (visualization) |
| Automated tests | [`tests/`](tests/) — 60 tests, all passing |

---

## 2. Building Models (IDF files)

> The base baseline building file along with the modified versions
> generated during runtime evaluation.

| File | Role |
|---|---|
| [`simulation/models/SmallOffice.idf`](simulation/models/SmallOffice.idf) | Base building model — DOE Small Office reference building, 5 zones |
| [`simulation/models/ARIA_Optimized.idf`](simulation/models/ARIA_Optimized.idf) | Modified version, generated post-run by [`scripts/export_aria_idf.py`](scripts/export_aria_idf.py) — ARIA's real converged setpoints from the 7-day evaluation baked into the building's schedules |
| [`simulation/models/Chicago.epw`](simulation/models/Chicago.epw) | Weather file both models run against (Chicago TMY3) |

---

## 3. Quantitative Savings Dashboard

> A visual dashboard or final data export comparing the baseline operation
> against your AI-driven closed-loop strategy, explicitly proving
> percentage reductions in total kWh consumed while maintaining thermal
> comfort boundaries.

| Piece | Location |
|---|---|
| Live dashboard | **[aria-autonomous-reasoning-intelligent-actuator.streamlit.app](https://aria-autonomous-reasoning-intelligent-actuator.streamlit.app)** |
| Energy comparison (explicit % reduction) | [`dashboard/energy_panel.py`](dashboard/energy_panel.py) — ARIA vs. baseline on one chart, plus live-computed `Baseline total` / `ARIA total` / `Change (%)` metrics (-15.9%) |
| Comfort compliance (thermal boundary proof) | [`dashboard/comfort_panel.py`](dashboard/comfort_panel.py) — PMV zone×time heatmap plus live-computed occupied-zone PMV compliance metric (84.3%) |
| Cost/CO2 savings | [`dashboard/savings_panel.py`](dashboard/savings_panel.py) |
| Decision-level detail | [`dashboard/decision_panel.py`](dashboard/decision_panel.py) — full reasoning log, all 672 real decisions |
| Underlying real data | [`aria.db`](aria.db) — the actual completed 7-day run (baseline + ARIA), committed to the repo so results are visible with zero setup |
| Full comparison table + honest analysis | [`README.md`](README.md#results) (Results section) and [`ARIA_Project_Documentation.pdf`](ARIA_Project_Documentation.pdf) |

---

## 4. System Architecture Document

> A short Markdown report explaining your tool-calling architecture,
> prompt engineering strategies, prompt latency management, and your
> technical approach to handling lengthy simulation logs.

**[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)** covers all four required topics directly:

| Required topic | Section |
|---|---|
| Tool-calling architecture | [Tool-Calling Architecture](docs/ARCHITECTURE.md#tool-calling-architecture) |
| Prompt engineering strategies | [Prompt Engineering Strategy](docs/ARCHITECTURE.md#prompt-engineering-strategy) |
| Prompt latency management | [Prompt Latency Management](docs/ARCHITECTURE.md#prompt-latency-management) |
| Handling lengthy simulation logs | [Handling Lengthy Simulation Logs](docs/ARCHITECTURE.md#handling-lengthy-simulation-logs) |

Companion documents with deeper/broader coverage: [`ARCHITECTURE.md`](ARCHITECTURE.md) (root — system flow diagram, component design), [`docs/TESTING.md`](docs/TESTING.md) (verification methodology), [`docs/CHALLENGES.md`](docs/CHALLENGES.md) (13 real incidents found and fixed).

---

## 5. PoC Demonstration Video

> A maximum 3-minute video recording showing the loop in action —
> highlighting data transferring live from EnergyPlus to the LLM and the
> subsequent control actions updating the model parameters automatically.

**[`ARIA_POC.mp4`](ARIA_POC.mp4)** — 2 minutes 41 seconds, real recording (not staged/scripted output): live sensor data leaving EnergyPlus, the request going to qwen2.5:3b, and the resulting control actions being written back to the building before the next tick. Supporting scripts used to produce this demo: [`scripts/loop.py`](scripts/loop.py) (isolated agentic tool-calling loop, 4 real LLM calls across a full occupancy arc) and [`scripts/fixed_loop.py`](scripts/fixed_loop.py) (the real closed EnergyPlus ↔ LLM loop, narrated live in the terminal).

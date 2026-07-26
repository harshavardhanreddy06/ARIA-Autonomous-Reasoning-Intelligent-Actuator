"""
agent/tool_registry.py
=======================
The 4 tools available to the LLM agent, plus their JSON schemas for
Ollama's native tool-calling API. Each tool is callable directly with
hand-crafted arguments, independent of the LLM — aria_agent.py dispatches
the model's actual tool calls through this same execute().
"""
from agent.safety_validator import validate_cooling_sp, validate_heating_sp, validate_lighting_level
from data.database import Database
from simulation.state_manager import StateManager

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "set_hvac_setpoint",
            "description": (
                "Set cooling/heating setpoints for one or more zones in a single call. "
                "Prefer passing every zone that needs a change here at once (fewer, richer "
                "calls run much faster than one call per zone). Values are safety-clamped."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "zones": {
                        "type": "array",
                        "description": "One entry per zone needing a setpoint change this cycle",
                        "items": {
                            "type": "object",
                            "properties": {
                                "zone_id": {"type": "integer", "description": "Zone ID, 1-5"},
                                "cooling_sp": {"type": "number", "description": "Proposed cooling setpoint, degrees C"},
                                "heating_sp": {"type": "number", "description": "Proposed heating setpoint, degrees C"},
                            },
                            "required": ["zone_id", "cooling_sp", "heating_sp"],
                        },
                    },
                },
                "required": ["zones"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_lighting_level",
            "description": (
                "Set lighting level fraction for one or more zones in a single call. Prefer "
                "passing every zone needing a change here at once. Occupied zones are floored "
                "at 0.2; unoccupied zones may go to 0.0."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "zones": {
                        "type": "array",
                        "description": "One entry per zone needing a lighting change this cycle",
                        "items": {
                            "type": "object",
                            "properties": {
                                "zone_id": {"type": "integer", "description": "Zone ID, 1-5"},
                                "level_fraction": {"type": "number", "description": "0.0 (off) to 1.0 (full brightness)"},
                            },
                            "required": ["zone_id", "level_fraction"],
                        },
                    },
                },
                "required": ["zones"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "schedule_precool",
            "description": "Schedule a precooling event ahead of a high-carbon or high-demand period.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_temp_c": {"type": "number", "description": "Target temperature to precool to"},
                    "duration_minutes": {"type": "integer", "description": "How long to run the precool event"},
                },
                "required": ["target_temp_c", "duration_minutes"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "log_decision",
            "description": "Always call this last, every cycle. Record the reasoning and actions taken.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reasoning": {"type": "string", "description": "Full chain-of-thought reasoning for this cycle"},
                    "actions_taken": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["reasoning", "actions_taken"],
            },
        },
    },
]


class ToolRegistry:
    def __init__(self, state_manager: StateManager, database: Database):
        self.state_manager = state_manager
        self.database = database

    def execute(self, name: str, args: dict, timestep: int = 0, agent_ref=None) -> dict:
        handler = getattr(self, f"_tool_{name}", None)
        if handler is None:
            return {"success": False, "error": f"Unknown tool: {name}"}
        try:
            return handler(args, timestep, agent_ref)
        except (KeyError, TypeError, ValueError) as e:
            # A malformed or missing argument fails only this tool call,
            # not the whole decision cycle — the model may omit a required
            # field occasionally, and that shouldn't force a full fallback.
            return {"success": False, "error": f"Malformed arguments for {name}: {e}"}

    def _tool_set_hvac_setpoint(self, args: dict, timestep: int, agent_ref) -> dict:
        # Accepts either the batched {"zones": [...]} shape or a flat
        # single-zone call ({"zone_id", "cooling_sp", "heating_sp"}); the
        # model does not always use the batched form even when offered, so
        # both are supported rather than requiring one.
        entries = args["zones"] if "zones" in args else [args]

        results = []
        for entry in entries:
            zone_id = int(entry["zone_id"])
            zone = self._find_zone(zone_id)
            if zone is None:
                results.append({"success": False, "zone_id": zone_id, "error": f"Unknown zone_id: {zone_id}"})
                continue

            is_occupied = zone["occ_fraction"] > 0
            proposed_cool = float(entry["cooling_sp"])
            proposed_heat = float(entry["heating_sp"])

            applied_cool = validate_cooling_sp(zone_id, proposed_cool, zone["cool_sp"], is_occupied)
            applied_heat = validate_heating_sp(zone_id, proposed_heat, zone["heat_sp"], is_occupied)

            clamped = (
                abs(applied_cool - proposed_cool) > 1e-9
                or abs(applied_heat - proposed_heat) > 1e-9
            )
            clamp_reason = ""
            if clamped:
                clamp_reason = (
                    f"cooling {proposed_cool}->{applied_cool}, heating {proposed_heat}->{applied_heat} "
                    f"(occupied={is_occupied})"
                )

            self.state_manager.set_pending_setpoint(zone_id, applied_cool, applied_heat)

            results.append({
                "success": True,
                "zone_id": zone_id,
                "applied_cooling_sp": applied_cool,
                "applied_heating_sp": applied_heat,
                "clamped": clamped,
                "clamp_reason": clamp_reason,
            })

        return {"success": all(r["success"] for r in results), "results": results}

    def _tool_set_lighting_level(self, args: dict, timestep: int, agent_ref) -> dict:
        entries = args["zones"] if "zones" in args else [args]

        results = []
        for entry in entries:
            zone_id = int(entry["zone_id"])
            zone = self._find_zone(zone_id)
            if zone is None:
                results.append({"success": False, "zone_id": zone_id, "error": f"Unknown zone_id: {zone_id}"})
                continue

            is_occupied = zone["occ_fraction"] > 0
            applied = validate_lighting_level(float(entry["level_fraction"]), is_occupied)
            self.state_manager.set_pending_lighting(zone_id, applied)

            results.append({"success": True, "zone_id": zone_id, "applied_level": applied})

        return {"success": all(r["success"] for r in results), "results": results}

    def _tool_schedule_precool(self, args: dict, timestep: int, agent_ref) -> dict:
        target_temp_c = float(args["target_temp_c"])
        duration_minutes = int(args["duration_minutes"])
        current_hour = self.state_manager.get_snapshot().get("sim_hour", 0)

        self.state_manager.set_precool_schedule(target_temp_c, duration_minutes, current_hour)

        # Rough display estimate only, not a physics model — real savings
        # come from the actual setpoint trajectory logged in timestep_data.
        estimated_co2_saved_kg = round(duration_minutes / 60 * 0.5, 2)

        return {
            "success": True,
            "scheduled_start_hour": current_hour,
            "estimated_co2_saved_kg": estimated_co2_saved_kg,
        }

    def _tool_log_decision(self, args: dict, timestep: int, agent_ref) -> dict:
        # log_decision is the one tool that must never be lost to a missing
        # field — a partial audit entry (e.g. reasoning present, actions_taken
        # omitted) is still far more useful than falling all the way back to
        # a synthetic entry, so this uses defaults instead of hard lookups.
        decision_id = self.database.insert_decision(timestep, {
            "reasoning": args.get("reasoning", "(model did not provide reasoning)"),
            "actions_taken": args.get("actions_taken", []),
        })
        if agent_ref is not None:
            agent_ref._log_decision_called = True
        return {"success": True, "decision_id": decision_id}

    def _find_zone(self, zone_id: int) -> dict | None:
        for z in self.state_manager.get_snapshot().get("zones", []):
            if z["id"] == zone_id:
                return z
        return None

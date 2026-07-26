"""
agent/aria_agent.py
====================
Ollama client + tool-calling decision loop + guaranteed audit trail.

The guarantee: log_decision() is written every cycle, either by the model
itself or (if the model fails to call it, or the cycle errors out) by
_write_synthetic_log() here — so "100% audit trail" is true by construction,
not by model reliability. See tests/test_agent.py for the failure-path proof.
"""
import logging

import ollama

from agent.fallback_handler import FallbackHandler
from agent.prompts import SYSTEM_PROMPT, build_user_message
from agent.tool_registry import TOOL_SCHEMAS, ToolRegistry

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


class AriaAgent:
    def __init__(
        self,
        tool_registry: ToolRegistry,
        fallback_handler: FallbackHandler,
        settings: dict,
    ):
        self.tool_registry = tool_registry
        self.fallback_handler = fallback_handler
        self.llm_settings = settings["llm"]
        self.client = ollama.Client(
            host=self.llm_settings["host"],
            timeout=self.llm_settings["timeout_seconds"],
        )
        self.logger = logging.getLogger("ARIA.agent")

        self._tool_calls_made: list[dict] = []
        self._log_decision_called = False

    def run_decision_cycle(self, timestep: int, snapshot: dict, carbon: dict) -> dict:
        self._tool_calls_made = []
        self._log_decision_called = False
        fallback_used = False

        try:
            self._run_agent_loop(timestep, snapshot, carbon)
        except Exception as e:
            self.logger.error(f"Agent loop failed at timestep {timestep}: {e}")
            self.fallback_handler.record_failure(str(e), timestep)
            if not self.llm_settings.get("fallback_on_failure", True):
                raise
            fallback_used = True

        if not self._log_decision_called:
            self._write_synthetic_log(timestep, fallback_used)

        if fallback_used:
            setpoints = self.fallback_handler.get_fallback_setpoints()
        else:
            setpoints = self.tool_registry.state_manager.get_pending_setpoints()
            self.fallback_handler.update_last_valid(setpoints)

        return setpoints

    def _run_agent_loop(self, timestep: int, snapshot: dict, carbon: dict) -> None:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_message(timestep, snapshot, carbon)},
        ]

        max_iterations = self.llm_settings["max_tool_iterations"]
        for _ in range(max_iterations):
            response = self.client.chat(
                model=self.llm_settings["model"],
                messages=messages,
                tools=TOOL_SCHEMAS,
                options={"temperature": self.llm_settings["temperature"]},
            )
            tool_calls = response.message.tool_calls
            messages.append({
                "role": "assistant",
                "content": response.message.content or "",
                "tool_calls": tool_calls,
            })

            if not tool_calls:
                return

            for call in tool_calls:
                name = call.function.name
                args = dict(call.function.arguments)
                result = self.tool_registry.execute(name, args, timestep=timestep, agent_ref=self)
                self._tool_calls_made.append({"name": name, "args": args, "result": result})
                messages.append({"role": "tool", "tool_name": name, "content": str(result)})

            if self._log_decision_called:
                return

        raise RuntimeError(f"Exceeded max_tool_iterations ({max_iterations}) without log_decision")

    def _write_synthetic_log(self, timestep: int, fallback_used: bool) -> None:
        tool_names = [c["name"] for c in self._tool_calls_made]
        self.tool_registry.database.insert_decision(timestep, {
            "reasoning": (
                f"[AUTO] log_decision not called by model. "
                f"Fallback: {fallback_used}. Tools called: {tool_names or 'none'}."
            ),
            "actions_taken": tool_names,
            "fallback_used": fallback_used,
            "auto_generated": 1,
        })
        self.logger.warning(
            f"Timestep {timestep}: synthetic audit entry written "
            f"(model did not call log_decision)"
        )

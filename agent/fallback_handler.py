"""
agent/fallback_handler.py
==========================
Holds the last known-good setpoints so a failed LLM cycle (timeout, bad
tool call, model crash) still leaves the building in a safe, previously-
validated state rather than making no decision at all.
"""


class FallbackHandler:
    def __init__(self):
        self._last_valid_setpoints: dict[int, tuple[float, float]] = {}
        self._failures: list[dict] = []

    def update_last_valid(self, setpoints: dict[int, tuple[float, float]]) -> None:
        if setpoints:
            self._last_valid_setpoints = dict(setpoints)

    def get_fallback_setpoints(self) -> dict[int, tuple[float, float]]:
        return dict(self._last_valid_setpoints)

    def record_failure(self, error: str, timestep: int) -> None:
        self._failures.append({"timestep": timestep, "error": error})

    @property
    def failure_count(self) -> int:
        return len(self._failures)

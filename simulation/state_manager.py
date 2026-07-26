"""
simulation/state_manager.py
============================
Thread-safe shared state between the EnergyPlus callback thread and
whatever reads/writes it from elsewhere (LLM agent loop, dashboard).

Pending writes (setpoints, lighting, precool schedule) are staged here by
tool_registry.py and consumed by EnergyPlusEnv's actuator-write step —
tools never touch EnergyPlus directly.
"""
import threading


class StateManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._snapshot: dict = {}
        self._pending_setpoints: dict[int, tuple[float, float]] = {}
        self._pending_lighting: dict[int, float] = {}
        self._precool_schedule: dict | None = None

    def update_snapshot(self, snapshot: dict) -> None:
        with self._lock:
            self._snapshot = snapshot

    def get_snapshot(self) -> dict:
        with self._lock:
            return dict(self._snapshot)

    def set_pending_setpoint(self, zone_id: int, cooling_sp: float, heating_sp: float) -> None:
        with self._lock:
            self._pending_setpoints[zone_id] = (cooling_sp, heating_sp)

    def get_pending_setpoints(self) -> dict[int, tuple[float, float]]:
        with self._lock:
            return dict(self._pending_setpoints)

    def set_pending_lighting(self, zone_id: int, level_fraction: float) -> None:
        with self._lock:
            self._pending_lighting[zone_id] = level_fraction

    def get_pending_lighting(self) -> dict[int, float]:
        with self._lock:
            return dict(self._pending_lighting)

    def set_precool_schedule(self, target_temp_c: float, duration_minutes: int, start_hour: int) -> None:
        with self._lock:
            self._precool_schedule = {
                "target_temp_c": target_temp_c,
                "duration_minutes": duration_minutes,
                "start_hour": start_hour,
            }

    def get_precool_schedule(self) -> dict | None:
        with self._lock:
            return dict(self._precool_schedule) if self._precool_schedule else None

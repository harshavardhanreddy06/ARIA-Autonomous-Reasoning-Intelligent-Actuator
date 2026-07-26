"""
simulation/energyplus_env.py
=============================
EnergyPlus control loop wrapper.

Each real (post-warmup, weather-period) zone timestep:
  1. Validate all sensor/actuator handles (hard-fail on any invalid one).
  2. Read every sensor into a snapshot, store it in StateManager.
  3. Invoke the caller-supplied `on_timestep` hook, if any — this is where
     the LLM agent's decision cycle runs and stages pending actuator writes.
  4. Apply whatever's pending to the real actuators.
"""
import logging
from typing import Callable

from agent.safety_validator import validate_cooling_sp, validate_heating_sp
from config.env_loader import inject_ep_path, load_settings

inject_ep_path()
from pyenergyplus.api import EnergyPlusAPI  # noqa: E402

from simulation.handle_registry import HandleRegistry, ZONE_NAMES  # noqa: E402
from simulation.state_manager import StateManager  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# EnergyPlusAPI.exchange.kind_of_sim() return values are not documented in
# the API itself. Under this installation, 1 identifies sizing/design-day
# environments and 3 identifies the real weather-file-driven RunPeriod.
# Combined with warmup_flag(), this lets the callback below skip every tick
# that isn't part of the actual simulated period — sizing and warmup ticks
# vastly outnumber the real ones and must not be treated as real data.
KIND_OF_SIM_WEATHER_RUNPERIOD = 3


class EnergyPlusEnv:
    def __init__(
        self,
        settings: dict | None = None,
        print_snapshots: bool = False,
        max_ticks: int | None = None,
        on_timestep: Callable[[int, dict], None] | None = None,
    ):
        """
        print_snapshots: log a compact snapshot once per simulated hour.
        max_ticks: stop the simulation early after this many real (post-
                   warmup, weather-period) zone timesteps. Intended for
                   development and short verification runs; production runs
                   should leave this as None and run the full period.
        on_timestep: called as on_timestep(timestep, snapshot) after the
                   sensor snapshot is stored, before pending writes are
                   applied — this is where the agent decision cycle runs
                   and populates state_manager's pending writes for this
                   same tick to pick up.
        """
        self.settings = settings or load_settings()
        self.api = EnergyPlusAPI()
        self.handle_registry = HandleRegistry()
        self.state_manager = StateManager()
        self.logger = logging.getLogger("ARIA.energyplus_env")
        self.state = None
        self._fatal_error: str | None = None
        self._handles_validated_logged = False
        self._tick_count = 0
        self.print_snapshots = print_snapshots
        self.max_ticks = max_ticks
        self.on_timestep = on_timestep

    def run(self, extra_args: list[str] | None = None) -> int:
        self.state = self.api.state_manager.new_state()

        self.api.runtime.callback_begin_new_environment(self.state, self._on_begin_new_environment)
        self.api.runtime.callback_begin_zone_timestep_before_init_heat_balance(
            self.state, self._on_zone_timestep
        )

        cfg = self.settings["energyplus"]
        args = [
            "-d", cfg["output_dir"],
            "-w", cfg["weather_path"],
        ] + (extra_args or []) + [cfg["model_path"]]

        exit_code = self.api.runtime.run_energyplus(self.state, args)

        # ctypes swallows exceptions raised inside a C-invoked callback: it
        # prints the traceback and lets the simulation continue rather than
        # propagating the error. The callback below records a fatal-error
        # flag instead, and we raise it for real here, in ordinary Python
        # call context, once run_energyplus() has returned control to us.
        if self._fatal_error:
            raise RuntimeError(self._fatal_error)

        return exit_code

    def _on_begin_new_environment(self, state):
        self.handle_registry.request_all(self.api, state)

    def _on_zone_timestep(self, state):
        if not self.handle_registry.initialize(self.api, state):
            return  # not fully ready yet — try again next timestep

        invalid = self.handle_registry.get_invalid_handles()
        if invalid:
            msg = f"\n{'=' * 60}\nFATAL: {len(invalid)} invalid EnergyPlus handle(s):\n"
            for name, var_string in invalid:
                msg += f"  ✗ '{name}'  →  EP string: '{var_string}'\n"
            msg += (
                "\nFix: Check output/aria/*.rdd for the exact variable names "
                "for your EnergyPlus version, then update handle_registry.py.\n"
                f"{'=' * 60}"
            )
            self.logger.error(msg)
            self._fatal_error = msg
            self.api.runtime.stop_simulation(state)
            return

        if not self._handles_validated_logged:
            self.logger.info(
                f"✓ All {len(self.handle_registry.handles)} EnergyPlus handles validated."
            )
            self._handles_validated_logged = True

        ep = self.api.exchange
        is_real_period = (
            ep.kind_of_sim(state) == KIND_OF_SIM_WEATHER_RUNPERIOD
            and not ep.warmup_flag(state)
        )
        if not is_real_period:
            return  # sizing period or warmup — not part of the real dataset

        snapshot = self._read_snapshot(state)
        self.state_manager.update_snapshot(snapshot)
        self._tick_count += 1

        if self.print_snapshots:
            ticks_per_hour = self.api.exchange.num_time_steps_in_hour(state)
            if self._tick_count % ticks_per_hour == 0:
                self._log_snapshot(snapshot)

        if self.on_timestep:
            self.on_timestep(self._tick_count, snapshot)

        self._apply_pending_writes(state, snapshot)

        if self.max_ticks and self._tick_count >= self.max_ticks:
            self.api.runtime.stop_simulation(state)

    def _apply_pending_writes(self, state, snapshot: dict) -> None:
        ep = self.api.exchange
        hr = self.handle_registry

        # Setpoints and lighting are already safety-clamped by tool_registry
        # before they ever reach state_manager — nothing to re-validate here.
        pending_setpoints = self.state_manager.get_pending_setpoints()
        cool_written, heat_written = set(), set()
        for zone_id, (cool_sp, heat_sp) in pending_setpoints.items():
            ep.set_actuator_value(state, hr.get(f"cool_sp_{zone_id}"), cool_sp)
            ep.set_actuator_value(state, hr.get(f"heat_sp_{zone_id}"), heat_sp)
            cool_written.add(zone_id)
            heat_written.add(zone_id)

        for zone_id, level_fraction in self.state_manager.get_pending_lighting().items():
            design_watts = ep.get_internal_variable_value(state, hr.get(f"light_design_watts_{zone_id}"))
            ep.set_actuator_value(state, hr.get(f"light_{zone_id}"), level_fraction * design_watts)

        precool = self.state_manager.get_precool_schedule()
        if precool:
            current_hour = snapshot["sim_hour"]
            end_hour = precool["start_hour"] + precool["duration_minutes"] / 60.0
            if precool["start_hour"] <= current_hour < end_hour:
                # schedule_precool operates building-wide with no per-zone
                # context, so its target temperature was never clamped by
                # tool_registry — validate it here, per zone, against each
                # zone's own current state at the moment it's applied.
                for zone in snapshot["zones"]:
                    if zone["id"] in cool_written:
                        continue
                    is_occupied = zone["occ_fraction"] > 0
                    safe_temp = validate_cooling_sp(
                        zone["id"], precool["target_temp_c"], zone["cool_sp"], is_occupied
                    )
                    ep.set_actuator_value(state, hr.get(f"cool_sp_{zone['id']}"), safe_temp)
                    cool_written.add(zone["id"])

        # Occupancy-transition safety net. The safety validator only clamps
        # values a tool call actively proposes; a zone the agent didn't
        # touch this cycle keeps whatever setpoint was already in effect,
        # which can be valid for its PREVIOUS occupancy state but out of
        # bounds for its CURRENT one (e.g. an unoccupied-range heating
        # setpoint persisting briefly after the zone becomes occupied).
        # Every untouched zone is re-validated against its current
        # occupancy state every tick, independent of the agent's tool
        # calls, so a setpoint can never silently outlive the occupancy
        # state it was set for.
        for zone in snapshot["zones"]:
            zid = zone["id"]
            is_occupied = zone["occ_fraction"] > 0
            if zid not in cool_written:
                safe_cool = validate_cooling_sp(zid, zone["cool_sp"], zone["cool_sp"], is_occupied)
                if abs(safe_cool - zone["cool_sp"]) > 1e-9:
                    ep.set_actuator_value(state, hr.get(f"cool_sp_{zid}"), safe_cool)
            if zid not in heat_written:
                safe_heat = validate_heating_sp(zid, zone["heat_sp"], zone["heat_sp"], is_occupied)
                if abs(safe_heat - zone["heat_sp"]) > 1e-9:
                    ep.set_actuator_value(state, hr.get(f"heat_sp_{zid}"), safe_heat)

    def _read_snapshot(self, state) -> dict:
        ep = self.api.exchange
        hr = self.handle_registry

        zones = []
        for i, zone in enumerate(ZONE_NAMES, start=1):
            zones.append({
                "id": i,
                "name": zone,
                "temp_c": ep.get_variable_value(state, hr.get(f"zone_temp_{i}")),
                "mrt_c": ep.get_variable_value(state, hr.get(f"zone_mrt_{i}")),
                "occ_fraction": ep.get_variable_value(state, hr.get(f"zone_occ_frac_{i}")),
                "co2_ppm": ep.get_variable_value(state, hr.get(f"zone_co2_{i}")),
                # Setpoints are read from report variables, not the control
                # actuators — get_actuator_value() only reflects a value
                # ARIA itself has written, not the schedule-driven current
                # setpoint (see handle_registry.py for the actuator handles
                # used to write setpoints).
                "cool_sp": ep.get_variable_value(state, hr.get(f"cool_sp_report_{i}")),
                "heat_sp": ep.get_variable_value(state, hr.get(f"heat_sp_report_{i}")),
            })

        # EnergyPlus reports the end of the current timestep without
        # rolling over: minutes() can return 60, or slightly more during
        # timestep-shortening in warmup, so a plain "==60" check is
        # insufficient — normalize with modulo arithmetic.
        hour, minute = ep.hour(state), ep.minutes(state)
        if minute >= 60:
            hour, minute = (hour + minute // 60) % 24, minute % 60

        return {
            "sim_month": ep.month(state),
            "sim_day": ep.day_of_month(state),
            "sim_hour": hour,
            "sim_minute": minute,
            "outdoor_temp": ep.get_variable_value(state, hr.get("outdoor_temp")),
            "hvac_kw": ep.get_variable_value(state, hr.get("hvac_power")) / 1000.0,
            "total_demand_kw": ep.get_variable_value(state, hr.get("total_demand")) / 1000.0,
            "zones": zones,
        }

    def _log_snapshot(self, s: dict):
        self.logger.info(
            f"Day {s['sim_month']:02d}/{s['sim_day']:02d} {s['sim_hour']:02d}:{s['sim_minute']:02d}  "
            f"outdoor={s['outdoor_temp']:.1f}C  hvac={s['hvac_kw']:.2f}kW  "
            f"total_demand={s['total_demand_kw']:.2f}kW"
        )
        for z in s["zones"]:
            self.logger.info(
                f"    Zone {z['id']} ({z['name']:15s}) temp={z['temp_c']:5.1f}C  "
                f"mrt={z['mrt_c']:5.1f}C  occ={z['occ_fraction']:.2f}  "
                f"co2={z['co2_ppm']:5.0f}ppm  coolSP={z['cool_sp']:.1f}  heatSP={z['heat_sp']:.1f}"
            )

"""
simulation/handle_registry.py
=============================
Fetches and validates every EnergyPlus sensor/actuator handle ARIA needs.

Two-step process, driven by EnergyPlus's own data-exchange lifecycle:

  1. request_all(api, state) — called from callback_begin_new_environment.
     request_variable() does not register handles in this EnergyPlus build;
     only variables statically declared as Output:Variable objects in the
     IDF resolve to a valid handle. request_all() is kept as a harmless,
     spec-compliant best-effort call, but every sensor listed below is also
     declared statically in simulation/models/SmallOffice.idf. Adding a new
     sensor requires adding the matching Output:Variable object to the IDF
     as well — request_variable() alone will not make it resolve.

  2. initialize(api, state) — called from a per-timestep callback, but only
     does real work once api.exchange.api_data_fully_ready(state) is True
     (handles are not guaranteed valid before that point). Fetches every
     handle exactly once and records any that failed (-1).

Reference notes on this EnergyPlus build and model, relevant to anyone
adding a new sensor or actuator here:
  - "Zone Air Temperature" does not resolve per zone in this build; use
    "Zone Mean Air Temperature" instead (both are listed as valid in the
    .rdd, but only the latter yields a working handle).
  - CO2 concentration requires a ZoneAirContaminantBalance object in the
    IDF — the base DOE Small Office model does not include one by default.
    This project's IDF adds it, plus a constant 400ppm outdoor CO2
    schedule; the People objects already carry a default generation rate.
  - There is no per-zone occupant-count output variable in this model; all
    5 zones share one building-wide occupancy schedule ("BLDG_OCC_SCH").
    That schedule's fraction (0.0-1.0) is read once per zone instead —
    identical across zones, which reflects how this DOE prototype model
    is actually built rather than a synthetic per-zone count.
  - Facility-level electricity variables require the key "Whole Building";
    a blank key or "*" both fail to resolve.
  - get_actuator_handle's signature is
    (state, component_type, control_type, actuator_key) — component_type,
    actuator_key, and control_type in a different order will resolve
    silently to the wrong handle rather than raising an error.
  - get_actuator_value() on the setpoint actuators only reflects a value
    this process has itself written via set_actuator_value() — it returns
    0.0 until then, not the schedule-driven current setpoint. Displaying
    the current setpoint (before ARIA overrides it) requires the report
    variables "Zone Thermostat Heating/Cooling Setpoint Temperature"
    instead; the actuator handles remain the correct mechanism for writing.
  - Lighting is controlled via actuator (component_type="Lights",
    control_type="Electricity Rate", key="<Zone>_Lights") in absolute
    watts, not a 0-1 fraction — tool_registry's level_fraction is
    multiplied by the zone's design wattage (the internal variable
    "Lighting Power Design Level") at write time in energyplus_env.py.
"""

ZONE_NAMES = [
    "Perimeter_ZN_1", "Perimeter_ZN_2",
    "Perimeter_ZN_3", "Perimeter_ZN_4", "Core_ZN",
]

BUILDING_OCCUPANCY_SCHEDULE = "BLDG_OCC_SCH"  # shared by all 5 zones in this IDF

# (registry_name_template, EnergyPlus variable name, key template)
ZONE_VARIABLES = [
    ("zone_temp_{i}",      "Zone Mean Air Temperature",       "{zone}"),
    ("zone_mrt_{i}",       "Zone Mean Radiant Temperature",   "{zone}"),
    ("zone_occ_frac_{i}",  "Schedule Value",                  BUILDING_OCCUPANCY_SCHEDULE),
    ("zone_co2_{i}",       "Zone Air CO2 Concentration",      "{zone}"),
    ("cool_sp_report_{i}", "Zone Thermostat Cooling Setpoint Temperature", "{zone}"),
    ("heat_sp_report_{i}", "Zone Thermostat Heating Setpoint Temperature", "{zone}"),
]

BUILDING_VARIABLES = [
    ("hvac_power",    "Facility Total HVAC Electricity Demand Rate", "Whole Building"),
    ("outdoor_temp",  "Site Outdoor Air Drybulb Temperature",        "Environment"),
    ("total_demand",  "Facility Total Electricity Demand Rate",      "Whole Building"),
]

# (registry_name_template, component_type, control_type, key template)
ZONE_ACTUATORS = [
    ("cool_sp_{i}", "Zone Temperature Control", "Cooling Setpoint", "{zone}"),
    ("heat_sp_{i}", "Zone Temperature Control", "Heating Setpoint", "{zone}"),
    ("light_{i}",   "Lights", "Electricity Rate", "{zone}_Lights"),
]

# (registry_name_template, internal-variable name, key template) — static
# design values, read once, not per-timestep sensors.
ZONE_INTERNAL_VARIABLES = [
    ("light_design_watts_{i}", "Lighting Power Design Level", "{zone}_Lights"),
]


class HandleRegistry:
    def __init__(self):
        self.handles: dict[str, int] = {}
        self.variable_strings: dict[str, str] = {}
        self._handles_fetched = False

    def request_all(self, api, state):
        """Call from callback_begin_new_environment, every environment."""
        ep = api.exchange
        for _, var_name, key_tpl in ZONE_VARIABLES:
            for zone in ZONE_NAMES:
                ep.request_variable(state, var_name, key_tpl.format(zone=zone))
        for _, var_name, key in BUILDING_VARIABLES:
            ep.request_variable(state, var_name, key)
        # Actuators do not need to be requested — only output variables do.

    def initialize(self, api, state) -> bool:
        """
        Call every timestep until it returns True. Does nothing (and returns
        False) until api_data_fully_ready — fetches every handle exactly
        once after that, then returns True on every subsequent call.
        """
        if self._handles_fetched:
            return True
        ep = api.exchange
        if not ep.api_data_fully_ready(state):
            return False

        for name_tpl, var_name, key_tpl in ZONE_VARIABLES:
            for i, zone in enumerate(ZONE_NAMES, start=1):
                self._get_variable(
                    ep, state, name_tpl.format(i=i), var_name, key_tpl.format(zone=zone)
                )

        for name, var_name, key in BUILDING_VARIABLES:
            self._get_variable(ep, state, name, var_name, key)

        for name_tpl, comp_type, ctrl_type, key_tpl in ZONE_ACTUATORS:
            for i, zone in enumerate(ZONE_NAMES, start=1):
                self._get_actuator(
                    ep, state, name_tpl.format(i=i), comp_type, ctrl_type, key_tpl.format(zone=zone)
                )

        for name_tpl, var_name, key_tpl in ZONE_INTERNAL_VARIABLES:
            for i, zone in enumerate(ZONE_NAMES, start=1):
                self._get_internal_variable(
                    ep, state, name_tpl.format(i=i), var_name, key_tpl.format(zone=zone)
                )

        self._handles_fetched = True
        return True

    def _get_variable(self, ep, state, name, var_name, key):
        self.variable_strings[name] = f"{var_name}|{key}"
        self.handles[name] = ep.get_variable_handle(state, var_name, key)

    def _get_actuator(self, ep, state, name, comp_type, ctrl_type, actuator_key):
        # Argument order is (component_type, control_type, actuator_key).
        self.variable_strings[name] = f"{comp_type}|{ctrl_type}|{actuator_key}"
        self.handles[name] = ep.get_actuator_handle(state, comp_type, ctrl_type, actuator_key)

    def _get_internal_variable(self, ep, state, name, var_name, key):
        self.variable_strings[name] = f"{var_name}|{key}"
        self.handles[name] = ep.get_internal_variable_handle(state, var_name, key)

    def get_invalid_handles(self) -> list[tuple[str, str]]:
        """Returns [(name, variable_string), ...] for every handle == -1."""
        return [
            (name, self.variable_strings[name])
            for name, handle in self.handles.items()
            if handle == -1
        ]

    def get(self, name) -> int:
        return self.handles[name]

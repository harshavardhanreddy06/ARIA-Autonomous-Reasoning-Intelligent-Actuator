"""
scripts/export_aria_idf.py
============================
Post-run deliverable: exports a static IDF snapshot with ARIA's converged
setpoints baked into the existing CLGSETP_SCH/HTGSETP_SCH schedules. The
live actuator API (simulation/energyplus_env.py) is the real control
mechanism for both the ARIA and baseline runs — this is a secondary
artifact satisfying the "building model deliverable" requirement.

All 5 zones share ONE building-wide CLGSETP_SCH/HTGSETP_SCH schedule pair
in this DOE prototype model (the same shared-schedule pattern documented
in handle_registry.py for occupancy/lighting) — so per-zone granularity
isn't preserved here; ARIA's building-wide occupied/unoccupied setpoint
averages replace the original static values in place, keeping the
schedule's existing weekday/weekend/time-of-day structure intact.

Run: python3 scripts/export_aria_idf.py  (after main.py has produced data)
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.env_loader import get_ep_dir, load_settings  # noqa: E402
from eppy.modeleditor import IDF  # noqa: E402

# The schedules' current static values, before replacement — matched by
# value (not field position), since occupied/unoccupied slots repeat
# across Weekdays/Saturday/AllOtherDays blocks at different field indices.
ORIGINAL_OCC_COOL, ORIGINAL_UNOCC_COOL = 24.0, 26.7
ORIGINAL_OCC_HEAT, ORIGINAL_UNOCC_HEAT = 21.0, 15.6


def replace_schedule_value(sched, old_value: float, new_value: float) -> int:
    replaced = 0
    for i, field in enumerate(sched.obj):
        try:
            if abs(float(field) - old_value) < 1e-6:
                sched.obj[i] = round(new_value, 1)
                replaced += 1
        except (TypeError, ValueError):
            continue  # non-numeric field (e.g. "Until: 06:00", "For: Weekdays")
    return replaced


def get_converged_setpoints(conn: sqlite3.Connection, last_day: int, occupied: bool):
    occ_clause = "occ_fraction > 0" if occupied else "occ_fraction = 0"
    row = conn.execute(
        f"""
        SELECT AVG(cooling_sp), AVG(heating_sp)
        FROM timestep_data
        WHERE run_type='aria' AND sim_day = ? AND {occ_clause}
        """,
        (last_day,),
    ).fetchone()
    if row[0] is not None:
        return row
    # Last day alone may not include any occupied (or unoccupied) timesteps
    # yet, e.g. a partial/early run — fall back to the whole run's average.
    return conn.execute(
        f"SELECT AVG(cooling_sp), AVG(heating_sp) FROM timestep_data WHERE run_type='aria' AND {occ_clause}"
    ).fetchone()


def main():
    settings = load_settings()
    conn = sqlite3.connect(settings["database"]["path"])

    last_day = conn.execute(
        "SELECT MAX(sim_day) FROM timestep_data WHERE run_type='aria'"
    ).fetchone()[0]
    if last_day is None:
        print("No ARIA run data found in the database — run main.py first.")
        raise SystemExit(1)

    occ_cool, occ_heat = get_converged_setpoints(conn, last_day, occupied=True)
    unocc_cool, unocc_heat = get_converged_setpoints(conn, last_day, occupied=False)
    conn.close()

    if occ_cool is None or unocc_cool is None:
        print(
            "Not enough data yet: the ARIA run hasn't covered both occupied and "
            "unoccupied hours. Run this again after main.py has completed (or "
            "progressed further)."
        )
        raise SystemExit(1)

    print(f"Converged setpoints (day {last_day} of ARIA run):")
    print(f"  Occupied:   cooling={occ_cool:.1f}C  heating={occ_heat:.1f}C")
    print(f"  Unoccupied: cooling={unocc_cool:.1f}C  heating={unocc_heat:.1f}C")

    IDF.setiddname(os.path.join(get_ep_dir(), "Energy+.idd"))
    idf = IDF(settings["energyplus"]["model_path"])

    for sched in idf.idfobjects["SCHEDULE:COMPACT"]:
        if sched.Name == "CLGSETP_SCH":
            n1 = replace_schedule_value(sched, ORIGINAL_OCC_COOL, occ_cool)
            n2 = replace_schedule_value(sched, ORIGINAL_UNOCC_COOL, unocc_cool)
            print(f"CLGSETP_SCH: replaced {n1} occupied + {n2} unoccupied values")
        elif sched.Name == "HTGSETP_SCH":
            n1 = replace_schedule_value(sched, ORIGINAL_OCC_HEAT, occ_heat)
            n2 = replace_schedule_value(sched, ORIGINAL_UNOCC_HEAT, unocc_heat)
            print(f"HTGSETP_SCH: replaced {n1} occupied + {n2} unoccupied values")

    # model_path is already resolved to an absolute path by load_settings(),
    # so anchoring the export alongside it keeps this correct regardless of
    # the caller's current working directory.
    output_path = os.path.join(os.path.dirname(settings["energyplus"]["model_path"]), "ARIA_Optimized.idf")
    idf.save(output_path)
    print(f"\nSaved: {output_path}")


if __name__ == "__main__":
    main()

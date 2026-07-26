"""
tests/test_database.py
=======================
"""
from data.database import Database


def make_snapshot():
    return {
        "sim_month": 7, "sim_day": 3, "sim_hour": 14, "sim_minute": 30,
        "hvac_kw": 3.5, "total_demand_kw": 6.2,
        "zones": [
            {"id": 1, "temp_c": 24.0, "mrt_c": 23.5, "occ_fraction": 1.0,
             "co2_ppm": 550.0, "cool_sp": 24.0, "heat_sp": 21.0},
            {"id": 2, "temp_c": 22.0, "mrt_c": 21.8, "occ_fraction": 0.0,
             "co2_ppm": 400.0, "cool_sp": 28.0, "heat_sp": 18.0},
        ],
    }


def test_insert_timestep_snapshot_writes_one_row_per_zone():
    db = Database(":memory:")
    db.insert_timestep_snapshot("aria", timestep=5, snapshot=make_snapshot())

    with db._lock:
        rows = db._conn.execute(
            "SELECT run_type, timestep, zone_id, zone_temp, pmv, ppd, occ_fraction, "
            "cooling_sp, heating_sp, hvac_kw, total_demand_kw FROM timestep_data ORDER BY zone_id"
        ).fetchall()

    assert len(rows) == 2
    assert rows[0][0] == "aria"
    assert rows[0][1] == 5
    assert rows[0][2] == 1
    assert rows[0][3] == 24.0
    assert -3.0 <= rows[0][4] <= 3.0  # pmv in valid range
    assert 5.0 <= rows[0][5] <= 100.0  # ppd in valid range
    assert rows[0][6] == 1.0
    assert rows[0][9] == 3.5
    assert rows[0][10] == 6.2
    assert rows[1][2] == 2
    assert rows[1][6] == 0.0


def test_insert_timestep_snapshot_run_type_filters_correctly():
    db = Database(":memory:")
    db.insert_timestep_snapshot("aria", timestep=1, snapshot=make_snapshot())
    db.insert_timestep_snapshot("baseline", timestep=1, snapshot=make_snapshot())

    with db._lock:
        aria_count = db._conn.execute(
            "SELECT COUNT(*) FROM timestep_data WHERE run_type='aria'"
        ).fetchone()[0]
        baseline_count = db._conn.execute(
            "SELECT COUNT(*) FROM timestep_data WHERE run_type='baseline'"
        ).fetchone()[0]

    assert aria_count == 2
    assert baseline_count == 2

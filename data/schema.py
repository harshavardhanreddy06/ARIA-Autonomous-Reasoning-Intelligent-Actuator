"""
data/schema.py
==============
Table definitions as constants.

A single timestep_data table with a run_type column ('aria' or 'baseline')
covers both simulation runs — the dashboard filters by run_type rather than
joining two structurally identical tables.
"""

DECISIONS_TABLE = """
CREATE TABLE IF NOT EXISTS decisions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestep        INTEGER,
    reasoning       TEXT,
    actions_taken   TEXT,              -- JSON list
    energy_impact   TEXT,
    comfort_impact  TEXT,
    llm_ms          INTEGER,
    fallback_used   INTEGER DEFAULT 0,
    auto_generated  INTEGER DEFAULT 0, -- 1 if synthetic (log_decision not called by model)
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

TIMESTEP_DATA_TABLE = """
CREATE TABLE IF NOT EXISTS timestep_data (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_type        TEXT NOT NULL,       -- 'aria' or 'baseline'
    timestep        INTEGER,
    sim_month       INTEGER,
    sim_day         INTEGER,
    sim_hour        INTEGER,
    sim_minute      INTEGER,
    zone_id         INTEGER,
    zone_temp       REAL,
    zone_mrt        REAL,
    pmv             REAL,
    ppd             REAL,
    occ_fraction    REAL,
    co2_ppm         REAL,
    cooling_sp      REAL,
    heating_sp      REAL,
    hvac_kw         REAL,               -- building-level, repeated per zone row
    total_demand_kw REAL,               -- building-level, repeated per zone row
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

ALL_TABLES = [DECISIONS_TABLE, TIMESTEP_DATA_TABLE]

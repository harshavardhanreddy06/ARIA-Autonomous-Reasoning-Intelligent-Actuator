"""
dashboard/data.py
==================
Shared SQLite query helpers for the dashboard panels.
"""
import json
import sqlite3

import pandas as pd


def get_connection(db_path: str) -> sqlite3.Connection:
    return sqlite3.connect(db_path, check_same_thread=False)


def load_timestep_data(conn: sqlite3.Connection, run_type: str) -> pd.DataFrame:
    df = pd.read_sql_query(
        "SELECT * FROM timestep_data WHERE run_type = ? ORDER BY timestep, zone_id",
        conn, params=(run_type,),
    )
    if not df.empty:
        df["datetime"] = pd.to_datetime(
            dict(year=2026, month=df["sim_month"], day=df["sim_day"],
                 hour=df["sim_hour"], minute=df["sim_minute"])
        )
    return df


def load_decisions(conn: sqlite3.Connection) -> pd.DataFrame:
    df = pd.read_sql_query("SELECT * FROM decisions ORDER BY timestep DESC", conn)
    if not df.empty:
        df["actions_taken"] = df["actions_taken"].apply(lambda s: json.loads(s) if s else [])
    return df


def building_level_series(df: pd.DataFrame) -> pd.DataFrame:
    """timestep_data has one row per zone with hvac_kw/total_demand_kw
    repeated — collapse to one row per timestep for building-level charts."""
    if df.empty:
        return df
    return (
        df.groupby(["timestep", "datetime"], as_index=False)
        .agg(hvac_kw=("hvac_kw", "first"), total_demand_kw=("total_demand_kw", "first"))
        .sort_values("timestep")
    )

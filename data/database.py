"""
data/database.py
=================
SQLite read/write layer. check_same_thread=False since the EnergyPlus
callback thread and (later) the dashboard both touch this; WAL mode lets
reads and writes overlap without blocking each other.
"""
import json
import sqlite3
import threading

from comfort.pmv import estimate_zone_pmv
from data.schema import ALL_TABLES


class Database:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        for ddl in ALL_TABLES:
            self._conn.execute(ddl)
        self._conn.commit()

    def insert_decision(self, timestep: int, fields: dict) -> int:
        with self._lock:
            cur = self._conn.execute(
                """
                INSERT INTO decisions
                    (timestep, reasoning, actions_taken, energy_impact,
                     comfort_impact, llm_ms, fallback_used, auto_generated)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    timestep,
                    fields.get("reasoning", ""),
                    json.dumps(fields.get("actions_taken", [])),
                    fields.get("energy_impact", "unknown"),
                    fields.get("comfort_impact", "unknown"),
                    fields.get("llm_response_ms", -1),
                    int(fields.get("fallback_used", 0)),
                    int(fields.get("auto_generated", 0)),
                ),
            )
            self._conn.commit()
            return cur.lastrowid

    def get_decision(self, decision_id: int) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, timestep, reasoning, actions_taken, energy_impact, "
                "comfort_impact, llm_ms, fallback_used, auto_generated FROM decisions WHERE id = ?",
                (decision_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row[0], "timestep": row[1], "reasoning": row[2],
            "actions_taken": json.loads(row[3]), "energy_impact": row[4],
            "comfort_impact": row[5], "llm_ms": row[6],
            "fallback_used": bool(row[7]), "auto_generated": bool(row[8]),
        }

    def get_decision_stats(self) -> dict:
        with self._lock:
            total = self._conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
            auto = self._conn.execute(
                "SELECT COUNT(*) FROM decisions WHERE auto_generated=1"
            ).fetchone()[0]
        return {"total": total, "auto_generated": auto, "llm_authored": total - auto}

    def insert_timestep_snapshot(self, run_type: str, timestep: int, snapshot: dict) -> None:
        """One row per zone per timestep. PMV/PPD computed here (not stored
        upstream) so every caller — ARIA's real run, the baseline run —
        gets identical, correctly-computed comfort numbers for free."""
        with self._lock:
            for z in snapshot["zones"]:
                pmv, ppd = estimate_zone_pmv(z["temp_c"], z["mrt_c"])
                self._conn.execute(
                    """
                    INSERT INTO timestep_data
                        (run_type, timestep, sim_month, sim_day, sim_hour, sim_minute,
                         zone_id, zone_temp, zone_mrt, pmv, ppd, occ_fraction, co2_ppm,
                         cooling_sp, heating_sp, hvac_kw, total_demand_kw)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_type, timestep,
                        snapshot["sim_month"], snapshot["sim_day"],
                        snapshot["sim_hour"], snapshot["sim_minute"],
                        z["id"], z["temp_c"], z["mrt_c"], pmv, ppd,
                        z["occ_fraction"], z["co2_ppm"], z["cool_sp"], z["heat_sp"],
                        snapshot["hvac_kw"], snapshot["total_demand_kw"],
                    ),
                )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

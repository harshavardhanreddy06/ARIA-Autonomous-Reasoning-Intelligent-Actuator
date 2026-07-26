"""
carbon/grid_intensity.py
=========================
Real-time grid carbon intensity. Uses the Electricity Maps API if
carbon.api_key is set in settings.yaml; otherwise falls back to a
time-of-day mock profile. settings.yaml ships with a blank api_key, so the
mock profile is what runs unless a key is configured.

The mock profile is deliberately shaped to cross both configured
thresholds (low_threshold_gco2_kwh=120, high_threshold_gco2_kwh=350) at
different hours of the day — a flatter curve sitting entirely inside
"NORMAL" would mean the PRECOOL/DEFER carbon strategies never actually
fire, regardless of how the agent itself behaves.
"""
import logging

import requests

from config.env_loader import load_settings

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("ARIA.grid_intensity")

ELECTRICITY_MAPS_URL = "https://api.electricitymap.org/v3/carbon-intensity/latest"

# gCO2/kWh by hour-of-day: overnight low (wind-heavy, low demand) dips below
# the 120 PRECOOL threshold; evening peak (fossil peakers) crosses the 350
# DEFER threshold. Loosely modeled on real PJM daily variation, simplified.
MOCK_PROFILE_BY_HOUR = {
    0: 110, 1: 100, 2:  95, 3:  95, 4: 100, 5: 115,
    6: 160, 7: 220, 8: 280, 9: 260, 10: 220, 11: 190,
    12: 170, 13: 160, 14: 165, 15: 190, 16: 260, 17: 360,
    18: 420, 19: 440, 20: 410, 21: 340, 22: 240, 23: 160,
}


def get_mock_intensity(hour: int) -> int:
    return MOCK_PROFILE_BY_HOUR[hour % 24]


def _classify_strategy(gco2: float, cfg: dict) -> str:
    if gco2 > cfg["high_threshold_gco2_kwh"]:
        return "DEFER"
    if gco2 < cfg["low_threshold_gco2_kwh"]:
        return "PRECOOL"
    return "NORMAL"


def get_carbon_intensity(hour: int, settings: dict | None = None) -> dict:
    """
    Returns:
      current_gco2_kwh: int
      strategy: "DEFER" | "PRECOOL" | "NORMAL"
      forecast_summary: str
      source: "electricitymaps" | "mock" | "mock (API failed)"
    """
    settings = settings or load_settings()
    cfg = settings["carbon"]
    api_key = cfg.get("api_key", "")

    if api_key:
        try:
            resp = requests.get(
                ELECTRICITY_MAPS_URL,
                params={"zone": cfg["region"]},
                headers={"auth-token": api_key},
                timeout=5,
            )
            resp.raise_for_status()
            gco2 = round(resp.json()["carbonIntensity"])
            source = "electricitymaps"
        except Exception as e:
            logger.warning(f"Electricity Maps API failed ({e}); falling back to mock.")
            gco2 = get_mock_intensity(hour)
            source = "mock (API failed)"
    else:
        gco2 = get_mock_intensity(hour)
        source = "mock"

    strategy = _classify_strategy(gco2, cfg)

    if source == "electricitymaps":
        forecast_summary = "forecast unavailable from live API (spot value only)"
    else:
        next_gco2 = get_mock_intensity(hour + 1)
        trend = "rising" if next_gco2 > gco2 else "falling" if next_gco2 < gco2 else "flat"
        forecast_summary = f"next hour ~{next_gco2} gCO2/kWh ({trend})"

    return {
        "current_gco2_kwh": gco2,
        "strategy": strategy,
        "forecast_summary": forecast_summary,
        "source": source,
    }

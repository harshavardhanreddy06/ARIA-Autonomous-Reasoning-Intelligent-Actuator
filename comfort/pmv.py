"""
comfort/pmv.py
==============
Fanger PMV/PPD thermal comfort model (ISO 7730 / ASHRAE 55 Appendix D).

PMV (Predicted Mean Vote): -3 (cold) .. 0 (neutral) .. +3 (hot)
PPD (Predicted Percentage Dissatisfied): 5% (minimum, at PMV=0) .. 100%
"""
import math

MAX_ITERATIONS = 150
CONVERGENCE_EPS = 0.00015


def pmv_ppd(
    ta: float,
    tr: float,
    vr: float,
    rh: float,
    met: float,
    clo: float,
    wme: float = 0.0,
) -> tuple[float, float]:
    """
    ta:  air temperature (°C)
    tr:  mean radiant temperature (°C)
    vr:  relative air velocity (m/s)
    rh:  relative humidity (%)
    met: metabolic rate (met units; 1 met = 58.15 W/m2)
    clo: clothing insulation (clo units; 1 clo = 0.155 m2K/W)
    wme: external work (met units, usually 0)

    Returns (pmv, ppd).
    """
    pa = rh * 10 * math.exp(16.6536 - 4030.183 / (ta + 235))

    icl = 0.155 * clo
    m = met * 58.15
    w = wme * 58.15
    mw = m - w

    fcl = 1 + 1.29 * icl if icl <= 0.078 else 1.05 + 0.645 * icl

    hcf = 12.1 * math.sqrt(vr)
    taa = ta + 273
    tra = tr + 273
    tcla = taa + (35.5 - ta) / (3.5 * icl + 0.1)

    p1 = icl * fcl
    p2 = p1 * 3.96
    p3 = p1 * 100
    p4 = p1 * taa
    p5 = 308.7 - 0.028 * mw + p2 * (tra / 100) ** 4
    xn = tcla / 100
    xf = tcla / 50

    n = 0
    hc = hcf
    while abs(xn - xf) > CONVERGENCE_EPS:
        xf = (xf + xn) / 2
        hcn = 2.38 * abs(100 * xf - taa) ** 0.25
        hc = hcf if hcf > hcn else hcn
        xn = (p5 + p4 * hc - p2 * xf ** 4) / (100 + p3 * hc)
        n += 1
        if n > MAX_ITERATIONS:
            raise RuntimeError("PMV calculation did not converge")

    tcl = 100 * xn - 273

    hl1 = 3.05 * 0.001 * (5733 - 6.99 * mw - pa)
    hl2 = 0.42 * (mw - 58.15) if mw > 58.15 else 0.0
    hl3 = 1.7 * 0.00001 * m * (5867 - pa)
    hl4 = 0.0014 * m * (34 - ta)
    hl5 = 3.96 * fcl * (xn ** 4 - (tra / 100) ** 4)
    hl6 = fcl * hc * (tcl - ta)

    ts = 0.303 * math.exp(-0.036 * m) + 0.028
    pmv = ts * (mw - hl1 - hl2 - hl3 - hl4 - hl5 - hl6)
    ppd = 100 - 95 * math.exp(-0.03353 * pmv ** 4 - 0.2179 * pmv ** 2)

    return pmv, ppd


# This building model does not expose per-zone relative humidity or air
# velocity sensors (see handle_registry.py) — these are fixed typical-office
# assumptions, not measured values. Sufficient for classifying comfort
# violations; a deployment with real humidity/air-speed sensors should pass
# measured values into pmv_ppd() directly instead of using this wrapper.
ASSUMED_RELATIVE_HUMIDITY = 50.0  # %
ASSUMED_AIR_VELOCITY = 0.1        # m/s
ASSUMED_MET = 1.2                 # met — seated, light office work
ASSUMED_CLO = 0.5                 # clo — indoor business casual


def estimate_zone_pmv(temp_c: float, mrt_c: float) -> tuple[float, float]:
    """Convenience wrapper for zone snapshots carrying only temp_c/mrt_c."""
    return pmv_ppd(
        ta=temp_c, tr=mrt_c,
        vr=ASSUMED_AIR_VELOCITY, rh=ASSUMED_RELATIVE_HUMIDITY,
        met=ASSUMED_MET, clo=ASSUMED_CLO,
    )

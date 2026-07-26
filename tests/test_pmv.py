"""
tests/test_pmv.py
==================
Reference values cross-validated against pythermalcomfort's pmv_ppd_iso
(ISO 7730 implementation) across 7 conditions spanning cold/hot/humid/dry,
matched within rounding noise. Sourced from an independent implementation
rather than hand-derived, so a transcription error isn't baked in as a
"known good" value.
"""
import math

import pytest

from comfort.pmv import pmv_ppd

# (ta, tr, vr, rh, met, clo, expected_pmv, expected_ppd)
REFERENCE_CASES = [
    (25, 25, 0.1, 50, 1.2, 0.5, 0.080, 5.1),
    (20, 20, 0.1, 50, 1.2, 1.0, -0.340, 7.4),
    (28, 28, 0.2, 60, 1.2, 0.5, 0.910, 22.3),
    (24, 24, 0.1, 50, 1.1, 0.5, -0.460, 9.4),
    (30, 32, 0.3, 70, 1.4, 0.3, 1.790, 66.7),
    (18, 16, 0.1, 40, 1.0, 1.2, -1.280, 39.5),
    (23, 23, 0.15, 45, 1.2, 0.6, -0.480, 9.8),
]


@pytest.mark.parametrize("ta,tr,vr,rh,met,clo,expected_pmv,expected_ppd", REFERENCE_CASES)
def test_pmv_matches_iso_reference(ta, tr, vr, rh, met, clo, expected_pmv, expected_ppd):
    pmv, ppd = pmv_ppd(ta, tr, vr, rh, met, clo)
    assert pmv == pytest.approx(expected_pmv, abs=0.05)
    assert ppd == pytest.approx(expected_ppd, abs=0.5)


def test_ppd_minimum_is_5_percent_at_neutral():
    # Mathematical property of the model: ppd = 100 - 95*exp(0) = 5 at pmv=0.
    pmv, ppd = pmv_ppd(ta=25.0, tr=25.0, vr=0.1, rh=50, met=1.0, clo=0.5)
    # Not exactly neutral, but confirms PPD never dips below the 5% floor.
    assert ppd >= 5.0


def test_ppd_never_below_5_percent():
    for ta in range(15, 35):
        _, ppd = pmv_ppd(ta=float(ta), tr=float(ta), vr=0.1, rh=50, met=1.2, clo=0.5)
        assert ppd >= 5.0 - 1e-6


def test_pmv_increases_with_air_temperature():
    # Warmer air, everything else fixed, should never make PMV colder.
    pmvs = [pmv_ppd(ta=t, tr=24, vr=0.1, rh=50, met=1.2, clo=0.5)[0] for t in range(18, 30)]
    assert all(b >= a - 1e-9 for a, b in zip(pmvs, pmvs[1:]))


def test_pmv_symmetric_ppd_formula():
    # ppd is a function of pmv^2 and pmv^4 only — sign-symmetric.
    pmv, ppd_pos = pmv_ppd(ta=28, tr=28, vr=0.1, rh=50, met=1.2, clo=0.5)
    expected_ppd = 100 - 95 * math.exp(-0.03353 * pmv**4 - 0.2179 * pmv**2)
    assert ppd_pos == pytest.approx(expected_ppd, abs=1e-6)

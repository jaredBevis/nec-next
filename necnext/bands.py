"""Amateur radio band edges (MHz) by region, for chart highlights.

USA follows the FCC Part 97 allocations; the others follow the IARU region band
plans. National rules inside a region vary, so check your own licence.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

_HF_COMMON = [("30m", 10.1, 10.15), ("20m", 14.0, 14.35), ("17m", 18.068, 18.168),
              ("15m", 21.0, 21.45), ("12m", 24.89, 24.99), ("10m", 28.0, 29.7)]
_LF = [("2200m", 0.1357, 0.1378), ("630m", 0.472, 0.479)]
_UHF = [("23cm", 1240.0, 1300.0)]

BAND_PLANS: Dict[str, List[Tuple[str, float, float]]] = {
    "USA": _LF + [("160m", 1.8, 2.0), ("80m", 3.5, 4.0), ("60m", 5.3305, 5.4065), ("40m", 7.0, 7.3)]
    + _HF_COMMON + [("6m", 50.0, 54.0), ("2m", 144.0, 148.0), ("1.25m", 222.0, 225.0),
                    ("70cm", 420.0, 450.0), ("33cm", 902.0, 928.0)] + _UHF,
    "IARU Region 1 (Europe, Africa, Middle East)": _LF + [
        ("160m", 1.81, 2.0), ("80m", 3.5, 3.8), ("60m", 5.3515, 5.3665), ("40m", 7.0, 7.2)]
    + _HF_COMMON + [("6m", 50.0, 52.0), ("4m", 70.0, 70.5), ("2m", 144.0, 146.0),
                    ("70cm", 430.0, 440.0)] + _UHF,
    "IARU Region 2 (Americas)": _LF + [
        ("160m", 1.8, 2.0), ("80m", 3.5, 4.0), ("60m", 5.3515, 5.3665), ("40m", 7.0, 7.3)]
    + _HF_COMMON + [("6m", 50.0, 54.0), ("2m", 144.0, 148.0), ("1.25m", 220.0, 225.0),
                    ("70cm", 430.0, 440.0), ("33cm", 902.0, 928.0)] + _UHF,
    "IARU Region 3 (Asia, Pacific)": _LF + [
        ("160m", 1.8, 2.0), ("80m", 3.5, 3.9), ("60m", 5.3515, 5.3665), ("40m", 7.0, 7.3)]
    + _HF_COMMON + [("6m", 50.0, 54.0), ("2m", 144.0, 148.0), ("70cm", 430.0, 440.0)] + _UHF,
}
DEFAULT_REGION = "USA"


def bands(region: str) -> List[Tuple[str, float, float]]:
    return BAND_PLANS.get(region, [])


def band_at(region: str, f: float) -> Optional[str]:
    for name, lo, hi in bands(region):
        if lo <= f <= hi:
            return name
    return None

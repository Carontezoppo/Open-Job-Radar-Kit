"""
Rule-based fit score for a lead. No LLM call.

Components (max 100, floor 0):
  Stage     pre-seed/seed → +30, early-stage → +15, unknown → +10, later → 0
  VC        tracked VC/accelerator → +20
  Location  London/UK → +25, EU city → +15, Europe (generic) → +10, unknown → 0
  Sector    strong_interest match → +25, open_to → +10, avoid → -20
"""

from __future__ import annotations

import re

TRACKED_VCS = {
    "index ventures", "northzone", "atomico", "seedcamp",
    "phoenix court", "localglobe", "notion capital", "balderton",
    "entrepreneur first", "ef", "antler",
}

_STAGE_SCORES: dict[str, int] = {
    "pre-seed": 30,
    "pre seed": 30,
    "seed": 30,
    "series a": 15,
    "early": 15,
    "early-stage": 15,
    "unknown": 10,
}

_UK_SIGNALS = {"london", "uk", "united kingdom", "england", "scotland", "cambridge",
               "oxford", "bristol", "manchester", "edinburgh"}
_EU_CITY_SIGNALS = {
    "amsterdam", "berlin", "munich", "hamburg", "dublin", "lisbon", "stockholm",
    "copenhagen", "oslo", "barcelona", "madrid", "milan", "paris", "brussels",
    "helsinki", "tallinn", "riga", "vilnius", "warsaw", "prague", "budapest",
    "bucharest", "athens", "zurich", "vienna",
}
_EU_GENERIC_SIGNALS = {"europe", "eu", "netherlands", "germany", "ireland", "portugal",
                       "sweden", "denmark", "norway", "iceland", "spain", "italy",
                       "france", "belgium", "luxembourg", "finland", "estonia",
                       "latvia", "lithuania", "poland", "czech", "slovakia",
                       "hungary", "romania", "bulgaria", "croatia", "slovenia",
                       "greece", "switzerland", "austria"}


def compute_fit_score(
    *,
    funding_stage: str,
    vc: str,
    location: str,
    sector_guess: str,
    profile_sectors: dict,
) -> int:
    score = 0

    # Stage
    stage_key = funding_stage.lower().strip()
    score += _STAGE_SCORES.get(stage_key, 0)

    # VC
    if vc.lower().strip() in TRACKED_VCS:
        score += 20

    # Location
    loc_lower = location.lower()
    if _mentions(loc_lower, _UK_SIGNALS):
        score += 25
    elif _mentions(loc_lower, _EU_CITY_SIGNALS):
        score += 15
    elif _mentions(loc_lower, _EU_GENERIC_SIGNALS):
        score += 10

    # Sector
    sector_lower = sector_guess.lower()
    strong = [s.lower() for s in profile_sectors.get("strong_interest", [])]
    open_to = [s.lower() for s in profile_sectors.get("open_to", [])]
    avoid = [s.lower() for s in profile_sectors.get("avoid", [])]

    if sector_lower and any(_overlap(sector_lower, s) for s in avoid):
        score -= 20
    elif sector_lower and any(_overlap(sector_lower, s) for s in strong):
        score += 25
    elif sector_lower and any(_overlap(sector_lower, s) for s in open_to):
        score += 10

    return max(0, min(100, score))


def is_european(location: str) -> bool:
    """UK or continental Europe. Used by global portfolios to keep leads in market."""
    loc = location.lower()
    return any(_mentions(loc, s) for s in (_UK_SIGNALS, _EU_CITY_SIGNALS, _EU_GENERIC_SIGNALS))


def _mentions(text: str, signals: set[str]) -> bool:
    # Whole words only, so "uk" doesn't match "Kyiv, Ukraine"
    return any(re.search(rf"\b{re.escape(s)}\b", text) for s in signals)


def _overlap(a: str, b: str) -> bool:
    return a in b or b in a

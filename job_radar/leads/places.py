"""
Lead locations → map coordinates, for the Leads map.

Leads carry a free-text HQ location ("London, United Kingdom", "Sweden"),
and there are few distinct ones (about 90 for 1,300+ leads), so each is
looked up once with OpenStreetMap's Nominatim geocoder and cached in
lead_places.json. Coordinates are a lookup, not a judgement, so this uses a
real geocoder rather than Claude, which can place a small town confidently
wrong.

Each cache entry is either None (Nominatim found nothing) or:
    {"lat": 51.5, "lon": -0.13, "name": "London", "kind": "city" | "country"}
"country" marks a location known only to country level, which the map draws
differently rather than pretending it's a city.

Nominatim's usage policy asks for an identifying User-Agent and at most one
request a second; results are cached, so each place is only ever asked once.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "job-radar/0.1 (personal job aggregator)"
UNKNOWN = {"", "unknown"}


def load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def resolve(locations: list[str], path: Path) -> dict[str, Any]:
    """Geocode any locations not yet in the cache, save it, and return it."""
    cache = load(path)
    todo = sorted({loc.strip() for loc in locations if loc and loc.strip().lower() not in UNKNOWN} - set(cache))
    if not todo:
        return cache

    logger.info("Placing %d new lead locations on the map...", len(todo))
    with httpx.Client(timeout=20, headers={"User-Agent": USER_AGENT}) as client:
        for i, loc in enumerate(todo):
            if i:
                time.sleep(1)  # Nominatim's limit: one request a second
            try:
                resp = client.get(
                    NOMINATIM_URL,
                    params={"q": loc, "format": "jsonv2", "limit": 1, "accept-language": "en"},
                )
                resp.raise_for_status()
                results = resp.json()
            except Exception as e:
                # Left out of the cache, so the next run tries again
                logger.warning("Couldn't place %r: %s", loc, e)
                continue
            if not results:
                cache[loc] = None
                continue
            hit = results[0]
            cache[loc] = {
                "lat": float(hit["lat"]),
                "lon": float(hit["lon"]),
                "name": hit.get("name") or loc.split(",")[0],
                "kind": "country" if hit.get("addresstype") == "country" else "city",
            }

    path.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")
    return cache

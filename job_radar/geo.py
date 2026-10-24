"""
Where a job can be done, as country codes.

Job boards describe places in free text — "Austin, TX", "Remote US",
"EMEA - Remote", "India, Hyderabad" — so matching them against a list of
place names never closes: there is always another city. Instead each
distinct location string is translated once into ISO country codes by
Claude, cached in locations.json, and filtering becomes a set comparison
against the countries the profile allows (`location.countries`).

A profile without `location.countries` (or with an empty list) has no
country filter at all, and the pre-filter falls back to the older
`would_not_relocate` check. That keeps any profile written before this
module behaving exactly as it did.

Two special results:
  ["ANY"] — can be done from anywhere ("Remote", "Worldwide")
  []      — Claude couldn't tell ("HQ", "Office 2"); treated as allowed,
            so an odd string never silently hides a good job
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from anthropic import Anthropic
from pydantic import BaseModel

logger = logging.getLogger(__name__)

ANYWHERE = "ANY"

# Regions as the settings panel groups them. A country appears in one region
# only; the panel's region chip selects or clears all of its countries.
REGIONS: dict[str, dict[str, str]] = {
    "Europe": {
        "GB": "United Kingdom", "IE": "Ireland", "FR": "France", "DE": "Germany",
        "NL": "Netherlands", "BE": "Belgium", "LU": "Luxembourg", "CH": "Switzerland",
        "AT": "Austria", "IT": "Italy", "ES": "Spain", "PT": "Portugal",
        "DK": "Denmark", "SE": "Sweden", "NO": "Norway", "FI": "Finland",
        "IS": "Iceland", "EE": "Estonia", "LV": "Latvia", "LT": "Lithuania",
        "PL": "Poland", "CZ": "Czechia", "SK": "Slovakia", "HU": "Hungary",
        "RO": "Romania", "BG": "Bulgaria", "GR": "Greece", "HR": "Croatia",
        "SI": "Slovenia", "RS": "Serbia", "UA": "Ukraine", "CY": "Cyprus",
        "MT": "Malta", "AL": "Albania", "BA": "Bosnia and Herzegovina",
        "ME": "Montenegro", "MK": "North Macedonia", "MD": "Moldova",
    },
    "North America": {
        "US": "United States", "CA": "Canada",
    },
    "Latin America": {
        "MX": "Mexico", "GT": "Guatemala", "CR": "Costa Rica", "PA": "Panama",
        "DO": "Dominican Republic", "CO": "Colombia", "VE": "Venezuela",
        "EC": "Ecuador", "PE": "Peru", "BO": "Bolivia", "CL": "Chile",
        "AR": "Argentina", "UY": "Uruguay", "PY": "Paraguay", "BR": "Brazil",
    },
    "Asia-Pacific": {
        "JP": "Japan", "KR": "South Korea", "CN": "China", "HK": "Hong Kong",
        "TW": "Taiwan", "SG": "Singapore", "MY": "Malaysia", "TH": "Thailand",
        "VN": "Vietnam", "PH": "Philippines", "ID": "Indonesia", "IN": "India",
        "PK": "Pakistan", "BD": "Bangladesh", "LK": "Sri Lanka",
        "AU": "Australia", "NZ": "New Zealand",
    },
    "Middle East & Africa": {
        "AE": "United Arab Emirates", "SA": "Saudi Arabia", "QA": "Qatar",
        "IL": "Israel", "TR": "Turkey", "EG": "Egypt", "MA": "Morocco",
        "TN": "Tunisia", "NG": "Nigeria", "GH": "Ghana", "KE": "Kenya",
        "RW": "Rwanda", "ET": "Ethiopia", "ZA": "South Africa",
    },
}


def allowed_countries(profile: dict[str, Any]) -> set[str] | None:
    """The profile's country filter, or None when it has none."""
    codes = profile.get("location", {}).get("countries") or []
    return {c.upper() for c in codes} or None


def in_allowed(codes: list[str] | None, allowed: set[str]) -> bool:
    """Unresolved, unknown and work-from-anywhere locations all pass."""
    if not codes or ANYWHERE in codes:
        return True
    return bool(allowed.intersection(codes))


def load_cache(path: Path) -> dict[str, list[str]]:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# --- Resolving new strings with Claude ----------------------------------------

SYSTEM_PROMPT = f"""\
You translate the location field of job postings into the countries where
the job can be done, as ISO 3166-1 alpha-2 codes (GB for the United Kingdom).

- A city or office gives its country: "Austin, TX" → ["US"], "Berlin Office" → ["DE"].
- Several places give several countries: "London or New York" → ["GB", "US"].
- Remote limited to a country or region gives those countries:
  "Remote US" → ["US"], "Remote (UK)" → ["GB"]. A region name expands to
  its countries: "Europe", "EMEA", "LATAM", "APAC".
- Remote with no restriction, or "Worldwide", gives ["{ANYWHERE}"].
- If the text names no place you can identify ("HQ", "Office 2", "TBC"),
  give an empty list. Never guess.
"""


class _Resolved(BaseModel):
    index: int
    countries: list[str]


class _Batch(BaseModel):
    results: list[_Resolved]


class LocationResolver:
    """Translates location strings to country codes, caching every answer."""

    BATCH_SIZE = 100

    def __init__(self, api_key: str, model: str, cache_path: Path):
        self.client = Anthropic(api_key=api_key)
        self.model = model
        self.cache_path = cache_path
        self.cache = load_cache(cache_path)

    def resolve(self, locations: list[str]) -> dict[str, list[str]]:
        """Resolve any strings not yet cached; return the whole cache."""
        todo = sorted({loc.strip() for loc in locations if loc and loc.strip()} - self.cache.keys())
        if todo:
            logger.info("Resolving %d new location string(s) to countries", len(todo))
        for start in range(0, len(todo), self.BATCH_SIZE):
            batch = todo[start:start + self.BATCH_SIZE]
            self.cache.update(self._ask(batch))
            # Save after each batch so a failure halfway keeps what was paid for
            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump(self.cache, f, indent=1, sort_keys=True, ensure_ascii=False)
        return self.cache

    def _ask(self, batch: list[str]) -> dict[str, list[str]]:
        numbered = "\n".join(f"{i}. {loc}" for i, loc in enumerate(batch))
        resp = self.client.messages.parse(
            model=self.model,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            # Lookup, not judgement — low effort keeps it cheap.
            output_config={"effort": "low"},
            output_format=_Batch,
            messages=[{"role": "user", "content": numbered}],
        )
        parsed = resp.parsed_output
        if parsed is None:
            logger.warning("Location lookup returned nothing for %d string(s)", len(batch))
            return {}
        # Indexes, not echoed strings, so a reformatted string can't miss its key.
        # Strings Claude skipped stay uncached and are retried next run.
        return {
            batch[r.index]: [c.upper() for c in r.countries]
            for r in parsed.results
            if 0 <= r.index < len(batch)
        }

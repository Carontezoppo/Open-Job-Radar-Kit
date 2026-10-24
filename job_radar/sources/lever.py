"""
Lever postings API.

Lever exposes an unauthenticated JSON endpoint per company:
    https://api.lever.co/v0/postings/{company}?mode=json

The company slug is the bit after lever.co/ in the public URL, e.g.
    https://jobs.lever.co/figma -> company is "figma"
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from bs4 import BeautifulSoup

from ..models import Job
from .base import BaseScraper

logger = logging.getLogger(__name__)


class LeverScraper(BaseScraper):
    source_name = "lever"

    def __init__(self, company: str, label: str = "", **kwargs):
        super().__init__(label=label or company, **kwargs)
        self.company = company
        self.base_url = f"https://api.lever.co/v0/postings/{company}"

    async def fetch(self) -> list[Job]:
        async with self._client() as client:
            resp = await client.get(self.base_url, params={"mode": "json"})
            resp.raise_for_status()
            data = resp.json()

        jobs: list[Job] = []
        for raw in data:
            try:
                jobs.append(self._parse(raw))
            except Exception as e:
                logger.warning("Lever parse failed for %s: %s", raw.get("id"), e)
        return jobs

    def _parse(self, raw: dict[str, Any]) -> Job:
        source_id = str(raw["id"])

        # Lever exposes description as HTML in `description` and `descriptionPlain`
        description = (
            raw.get("descriptionPlain")
            or _html_to_text(raw.get("description", ""))
            or ""
        )
        # Append the lists section if present (responsibilities, requirements etc)
        for lst in raw.get("lists", []) or []:
            heading = lst.get("text", "")
            content = _html_to_text(lst.get("content", ""))
            if content:
                description += f"\n\n{heading}\n{content}"

        categories = raw.get("categories") or {}
        location = categories.get("location")
        commitment = categories.get("commitment")  # full-time, etc
        team = categories.get("team")
        remote = bool(
            (categories.get("allLocations") and any("remote" in l.lower() for l in categories["allLocations"]))
            or (location and "remote" in location.lower())
        )

        posted_at = _parse_lever_ts(raw.get("createdAt"))

        return Job(
            id=Job.make_id(self.source_name, source_id),
            source=self.source_name,
            source_id=source_id,
            source_label=self.label,
            company=self.label or self.company,
            title=raw.get("text", "").strip(),
            location=location,
            remote=remote,
            description=description,
            url=raw.get("hostedUrl") or raw.get("applyUrl", ""),
            posted_at=posted_at,
            raw={**raw, "_team": team, "_commitment": commitment},
        )


def _html_to_text(html: str) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    return re.sub(r"\s+", " ", soup.get_text(separator=" ", strip=True))


def _parse_lever_ts(value: int | None) -> datetime | None:
    """Lever uses millisecond UNIX timestamps."""
    if not value:
        return None
    try:
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None

"""
Ashby job board API.

Ashby exposes an unauthenticated JSON endpoint per company:
    https://api.ashbyhq.com/posting-api/job-board/{slug}

The slug is the identifier in the public board URL, e.g.:
    https://jobs.ashbyhq.com/linear -> slug is "linear"

Unlike Greenhouse/Lever, Ashby provides a clean descriptionPlain field,
so BeautifulSoup is only a fallback for the rare job that omits it.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from bs4 import BeautifulSoup

from ..models import Job
from .base import BaseScraper

logger = logging.getLogger(__name__)


class AshbyScraper(BaseScraper):
    source_name = "ashby"

    def __init__(self, slug: str, label: str = "", **kwargs):
        super().__init__(label=label or slug, **kwargs)
        self.slug = slug
        self.base_url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"

    async def fetch(self) -> list[Job]:
        async with self._client() as client:
            resp = await client.get(self.base_url)
            if resp.status_code == 404 and self.slug != self.slug.lower():
                # Ashby is case-insensitive in practice, but try lowercase on 404
                # to handle any slugs captured with unexpected casing
                fallback_url = f"https://api.ashbyhq.com/posting-api/job-board/{self.slug.lower()}"
                logger.debug("Ashby 404 for %r, retrying as %r", self.slug, self.slug.lower())
                resp = await client.get(fallback_url)
            resp.raise_for_status()
            data = resp.json()

        jobs: list[Job] = []
        for raw in data.get("jobs", []):
            try:
                jobs.append(self._parse(raw))
            except Exception as e:
                logger.warning("Ashby parse failed for %s: %s", raw.get("id"), e)
        return jobs

    def _parse(self, raw: dict[str, Any]) -> Job:
        source_id = str(raw["id"])

        description = raw.get("descriptionPlain") or _html_to_text(raw.get("descriptionHtml", ""))

        location = raw.get("location") or None
        # isRemote can be null in the API (e.g. unlisted roles); fall back to workplaceType
        remote = bool(raw.get("isRemote")) or (raw.get("workplaceType") or "").lower() == "remote"

        posted_at = _parse_dt(raw.get("publishedAt"))

        return Job(
            id=Job.make_id(self.source_name, source_id),
            source=self.source_name,
            source_id=source_id,
            source_label=self.label,
            company=self.label or self.slug,
            title=raw.get("title", "").strip(),
            location=location,
            remote=remote,
            description=description,
            url=raw.get("jobUrl") or raw.get("applyUrl", ""),
            posted_at=posted_at,
            raw=raw,
        )


def _html_to_text(html: str) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    return re.sub(r"\s+", " ", soup.get_text(separator=" ", strip=True))


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None

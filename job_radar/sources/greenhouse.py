"""
Greenhouse boards API.

Greenhouse exposes an unauthenticated JSON endpoint per board:
    https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true

The token is the slug at the end of a board URL like
    https://boards.greenhouse.io/anthropic
                                  ^^^^^^^^ <- token

Docs: https://developers.greenhouse.io/job-board.html
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


class GreenhouseScraper(BaseScraper):
    source_name = "greenhouse"

    def __init__(self, token: str, label: str = "", **kwargs):
        super().__init__(label=label or token, **kwargs)
        self.token = token
        self.base_url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"

    async def fetch(self) -> list[Job]:
        async with self._client() as client:
            resp = await client.get(self.base_url, params={"content": "true"})
            resp.raise_for_status()
            data = resp.json()

        jobs: list[Job] = []
        for raw in data.get("jobs", []):
            try:
                jobs.append(self._parse(raw))
            except Exception as e:
                logger.warning("Greenhouse parse failed for %s: %s", raw.get("id"), e)
        return jobs

    def _parse(self, raw: dict[str, Any]) -> Job:
        source_id = str(raw["id"])
        # Greenhouse description is HTML — strip to plain text for the matcher
        html = raw.get("content", "") or ""
        description = _html_to_text(html)

        location = (raw.get("location") or {}).get("name") or None
        remote = bool(location and re.search(r"\bremote\b", location, re.I))

        # Greenhouse company name lives on the parent board, not always on the job
        company = (raw.get("company") or {}).get("name") or self.label or self.token

        updated = raw.get("updated_at") or raw.get("first_published")
        posted_at = _parse_dt(updated)

        return Job(
            id=Job.make_id(self.source_name, source_id),
            source=self.source_name,
            source_id=source_id,
            source_label=self.label,
            company=company,
            title=raw.get("title", "").strip(),
            location=location,
            remote=remote,
            description=description,
            url=raw.get("absolute_url", ""),
            posted_at=posted_at,
            raw=raw,
        )


def _html_to_text(html: str) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(separator=" ", strip=True)
    # Collapse runs of whitespace
    return re.sub(r"\s+", " ", text)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    # Greenhouse uses ISO 8601 like "2024-09-01T14:30:00.000Z"
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None

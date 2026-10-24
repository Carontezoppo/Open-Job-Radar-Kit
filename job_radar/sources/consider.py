"""
Consider.com-powered VC portfolio boards.

Consider is a React SPA backed by a private REST API at /api-boards/. The
relevant endpoint is /api-boards/search-jobs (POST), which accepts a board
object, a query object (jobFunctions, jobTypes, etc.), and a pagination cursor
in meta.sequence. No login, but since summer 2026 the API rejects requests
without a CSRF token (412 INVALID_CSRF): the board page sets a session cookie
and embeds "csrfToken" in its initial data, and the POST must send both. The
Origin header must match the board's domain.

The response groups jobs by company when grouped=True. We use grouped=False for
a flat list.

Response fields per job:
    title, companyName, companySlug, url, applyUrl, jobId,
    timeStamp (ISO 8601), locations[], remote (bool), hybrid (bool),
    salary {minValue, maxValue, currency.value, period.value},
    jobTypes[{id, label}], jobFunctions[{label}],
    jobSeniorities[{label}], skills[{label}],
    markets[{label}], stages[{label}],
    departments[], companyStaffCount, minYearsExp

No free-text description is returned. We synthesise one from the typed fields.

Known boards (April 2026):
    Phoenix Court / LocalGlobe  "localglobe-all"    jobs.phoenixcourt.vc
    Notion Capital              "notion-capital"    jobs.notion.vc
    Balderton Capital           "balderton-capital" careers.balderton.com

The board_id is in window.serverInitialData.board.id in the page HTML.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

import httpx

from ..models import Job
from .base import BaseScraper

logger = logging.getLogger(__name__)


class ConsiderScraper(BaseScraper):
    """Scrapes Consider.com-powered job boards via the /api-boards/search-jobs endpoint."""

    source_name = "consider"

    def __init__(
        self,
        board_id: str,
        domain: str,
        label: str = "",
        job_functions: list[str] | None = None,
        page_size: int = 60,
        max_pages: int = 20,
        **kwargs,
    ):
        """
        board_id:      the Consider board slug, e.g. "localglobe-all"
        domain:        the board's public domain, e.g. "jobs.phoenixcourt.vc"
        job_functions: server-side filter — default ["Design"]
        """
        super().__init__(label=label or board_id, **kwargs)
        self.board_id = board_id
        self.domain = domain
        self.job_functions = job_functions if job_functions is not None else ["Design"]
        self.page_size = page_size
        self.max_pages = max_pages

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self._timeout,
            follow_redirects=True,
            headers={
                "User-Agent": "job-radar/0.1 (personal job aggregator)",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Origin": f"https://{self.domain}",
                "Referer": f"https://{self.domain}/jobs",
            },
        )

    async def fetch(self) -> list[Job]:
        async with self._client() as client:
            raw_jobs = await self._fetch_all(client)

        jobs: list[Job] = []
        for raw in raw_jobs:
            try:
                jobs.append(self._parse(raw))
            except Exception as e:
                logger.warning(
                    "Consider parse failed for %r: %s",
                    raw.get("jobId") or raw.get("title"),
                    e,
                )
        return jobs

    async def _csrf_token(self, client: httpx.AsyncClient) -> str:
        """Load the board page for its CSRF token; the client keeps the session cookie it sets."""
        resp = await client.get(f"https://{self.domain}/jobs", headers={"Accept": "text/html"})
        resp.raise_for_status()
        match = re.search(r'"csrfToken":"([^"]+)"', resp.text)
        if not match:
            raise RuntimeError("no csrfToken in the board page")
        return match.group(1)

    async def _fetch_all(self, client: httpx.AsyncClient) -> list[dict[str, Any]]:
        url = f"https://{self.domain}/api-boards/search-jobs"
        client.headers["x-csrf-token"] = await self._csrf_token(client)
        board = {"id": self.board_id, "isParent": True}
        query = {"jobFunctions": self.job_functions}

        all_jobs: list[dict[str, Any]] = []
        meta: dict[str, Any] = {}

        for page in range(1, self.max_pages + 1):
            body = {"meta": meta, "board": board, "query": query, "grouped": False}

            # A failure on page 1 means the board can't be read at all, so it
            # propagates and the Sources page shows it as an error. Later pages
            # stop paging and keep what was fetched.
            try:
                resp = await client.post(url, json=body)
                resp.raise_for_status()
                data = resp.json()
                errors = data.get("errors") or []
                if errors:
                    raise RuntimeError(f"API errors: {errors}")
            except Exception as e:
                if page == 1:
                    raise
                logger.warning("Consider %s page=%d failed, keeping earlier pages: %s", self.label, page, e)
                break

            page_jobs: list[dict] = data.get("jobs") or []
            if page == 1:
                logger.info(
                    "Consider: %s page 1 returned %d jobs (jobFunctions=%r)",
                    self.label, len(page_jobs), self.job_functions,
                )

            all_jobs.extend(page_jobs)

            new_meta: dict = data.get("meta") or {}
            sequence = new_meta.get("sequence")
            if not sequence or len(page_jobs) < self.page_size:
                break  # last page — sequence absent or partial page
            meta = new_meta

        return all_jobs

    def _parse(self, raw: dict[str, Any]) -> Job:
        # Use the canonical ATS URL as source_id — jobId alone can collide across companies
        source_id = raw.get("url") or raw.get("applyUrl") or str(raw.get("jobId") or "")

        title = (raw.get("title") or "").strip()
        company = (raw.get("companyName") or self.label).strip()

        locations = raw.get("locations") or []
        # Consider often repeats city names across its location fields — dedupe preserving order
        seen: set[str] = set()
        unique_locs = [x for x in (str(l) for l in locations if l) if not (x in seen or seen.add(x))]
        location = ", ".join(unique_locs) if unique_locs else None

        remote = bool(raw.get("remote")) or bool(
            location and re.search(r"\bremote\b", location, re.I)
        )

        # Prefer the clean ATS URL over the UTM-tagged apply URL
        url = raw.get("url") or raw.get("applyUrl") or ""

        posted_at = _parse_iso(raw.get("timeStamp"))

        return Job(
            id=Job.make_id(self.source_name, source_id),
            source=self.source_name,
            source_id=source_id,
            source_label=self.label,
            company=company,
            title=title,
            location=location,
            remote=remote,
            description=_synthesise_description(raw),
            url=url,
            posted_at=posted_at,
            raw=raw,
        )


# --- Helpers ------------------------------------------------------------------

def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _synthesise_description(raw: dict[str, Any]) -> str:
    """Build a structured description from Consider's typed fields.

    Consider doesn't return free-text descriptions in the listing payload.
    The typed fields (role type, seniority, salary, skills, industry) are
    actually a denser signal for the matcher than typical marketing copy.
    """
    lines: list[str] = []

    job_types = [jt["label"] for jt in (raw.get("jobTypes") or []) if jt.get("label")]
    if job_types:
        lines.append(f"Role type: {', '.join(job_types)}")

    seniorities = [s["label"] for s in (raw.get("jobSeniorities") or []) if s.get("label")]
    if seniorities:
        lines.append(f"Seniority: {', '.join(seniorities)}")

    work_parts = []
    if raw.get("remote"):
        work_parts.append("remote")
    if raw.get("hybrid"):
        work_parts.append("hybrid")
    if not work_parts:
        work_parts.append("onsite")
    lines.append(f"Work mode: {', '.join(work_parts)}")

    if raw.get("locations"):
        seen: set[str] = set()
        locs = [x for x in (str(l) for l in raw["locations"] if l) if not (x in seen or seen.add(x))]
        if locs:
            lines.append(f"Locations: {', '.join(locs)}")

    salary = raw.get("salary")
    if salary:
        lo = salary.get("minValue")
        hi = salary.get("maxValue")
        cur = (salary.get("currency") or {}).get("value") or "USD"
        per = (salary.get("period") or {}).get("value") or "year"
        if lo and hi and lo != hi:
            lines.append(f"Compensation: {cur} {lo:,}–{hi:,} / {per}")
        elif lo or hi:
            lines.append(f"Compensation: {cur} {(lo or hi):,} / {per}")

    stages = [s["label"] for s in (raw.get("stages") or []) if s.get("label")]
    if stages:
        lines.append(f"Company stage: {', '.join(stages)}")

    markets = [m["label"] for m in (raw.get("markets") or []) if m.get("label")]
    if markets:
        lines.append(f"Industry: {', '.join(markets)}")

    if raw.get("companyStaffCount"):
        lines.append(f"Company size: {raw['companyStaffCount']} employees")

    if raw.get("minYearsExp"):
        lines.append(f"Min experience: {raw['minYearsExp']} years")

    skills = [s["label"] for s in (raw.get("skills") or []) if s.get("label")]
    if skills:
        lines.append(f"Skills: {', '.join(skills[:10])}")

    return "\n".join(lines)

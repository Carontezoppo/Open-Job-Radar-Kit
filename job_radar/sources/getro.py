"""
Getro VC portfolio boards.

Two implementations, same Job output:

  GetroScraper      — HTML fallback. Parses the rendered page. Works for any
                      Getro board with no per-VC config. Doesn't get
                      job descriptions (only what's in the listing card).

  GetroJsonScraper  — JSON API. Faster, cleaner, includes descriptions.
                      Requires knowing the collection_id for each VC.

--- API NOTES (discovered via DevTools, April 2026) ---
Base:    https://api.getro.com/api/v1/
Pattern: /collections/{collection_id}/{resource}
Auth:    None (public read), but rejects requests without an Origin header
         matching one of the known *.getro.com subdomains.
Known endpoints on a collection:
  /organizations/all   — list of portfolio companies (id, name, slug)
  /jobs                — list of jobs (suspected, unverified at time of writing)
  /custom_filters      — filter taxonomy
  /job_functions       — taxonomy
  /company_facets      — taxonomy
  /network_counts      — meta counts
  /settings            — collection settings

Collection IDs we know:
  Index Ventures:  1629
  (TODO: discover by hitting the same '/organizations/all' endpoint on each
   board's network tab — the collection_id is in the URL.)

If the JSON endpoint changes, the HTML scraper still works as a fallback.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from bs4 import BeautifulSoup

from ..models import Job
from .base import BaseScraper

logger = logging.getLogger(__name__)


# =============================================================================
# JSON SCRAPER (preferred — use this once collection_id is known for a VC)
# =============================================================================

class GetroJsonScraper(BaseScraper):
    """Hits the Getro REST API directly. Much faster and richer than HTML.

    The /api/v2/collections/{id}/search/jobs endpoint is a POST with a JSON
    body. It supports server-side filtering by query string and location,
    which is significantly cheaper than fetching everything and filtering
    client-side — the API has ~13k jobs per VC.
    """

    source_name = "getro"

    def __init__(
        self,
        collection_id: int,
        subdomain: str | None = None,
        domain: str | None = None,
        label: str = "",
        per_page: int = 50,
        max_pages: int = 20,
        query: str = "design",
        locations: list[str] | None = None,
        **kwargs,
    ):
        """
        Either subdomain OR domain must be given (domain wins if both).
            subdomain='indexventures'         -> indexventures.getro.com
            domain='careers.atomico.com'      -> careers.atomico.com

        query: search term sent server-side. 'design' catches most relevant
            roles for a design-focused profile. Leave as '' to fetch everything
            and filter client-side.
        locations: optional list of searchable_location_option filter values
            (e.g. ['remote', 'london']). Default None means no location filter.
        """
        if not subdomain and not domain:
            raise ValueError("GetroJsonScraper needs either 'subdomain' or 'domain'")
        super().__init__(label=label or domain or subdomain or "", **kwargs)
        self.host = domain or f"{subdomain}.getro.com"
        self.collection_id = collection_id
        self.per_page = per_page
        self.max_pages = max_pages
        self.query = query
        self.locations = locations or []

    def _origin(self) -> str:
        """The Origin header Getro's API expects — matches the public board URL."""
        return f"https://{self.host}"

    def _client(self):
        import httpx
        return httpx.AsyncClient(
            timeout=self._timeout,
            follow_redirects=True,
            headers={
                "User-Agent": "job-radar/0.1 (personal job aggregator)",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Origin": self._origin(),
                "Referer": f"{self._origin()}/jobs",
            },
        )

    async def fetch(self) -> list[Job]:
        async with self._client() as client:
            jobs_payload = await self._fetch_jobs(client)

        jobs: list[Job] = []
        for raw in jobs_payload:
            try:
                jobs.append(self._parse(raw))
            except Exception as e:
                logger.warning(
                    "Getro JSON parse failed for %r: %s",
                    raw.get("id") or raw.get("title"),
                    e,
                )
        return jobs

    async def _fetch_jobs(self, client) -> list[dict[str, Any]]:
        """POST to the v2 search/jobs endpoint, paginating until done.

        Body shape (April 2026):
            {
              "hitsPerPage": 20,
              "page": 1,
              "query": "Design",
              "filters": {
                "q": "Design",
                "page": 1,
                "searchable_location_option": ["remote"]
              }
            }
        """
        url = f"https://api.getro.com/api/v2/collections/{self.collection_id}/search/jobs"
        all_items: list[dict[str, Any]] = []

        for page in range(1, self.max_pages + 1):
            body = {
                "hitsPerPage": self.per_page,
                "page": page,
                "query": self.query,
                "filters": {
                    "q": self.query,
                    "page": page,
                },
            }
            if self.locations:
                body["filters"]["searchable_location_option"] = self.locations

            try:
                resp = await client.post(url, json=body)
            except Exception as e:
                logger.error("Getro POST %s page=%d raised: %s", url, page, e)
                break

            if resp.status_code != 200:
                logger.warning(
                    "Getro POST %s page=%d -> %d", url, page, resp.status_code
                )
                break

            data = resp.json()
            page_items = _extract_items(data)

            if page == 1:
                logger.info(
                    "Getro JSON: %s page 1 returned %d items (query=%r)",
                    url, len(page_items), self.query,
                )

            if not page_items:
                break
            all_items.extend(page_items)
            if len(page_items) < self.per_page:
                break  # last page

        return all_items

    def _parse(self, raw: dict[str, Any]) -> Job:
        """Parse a Getro /jobs response item.

        Confirmed fields (April 2026):
            id, title, slug, url, work_mode, seniority, locations[],
            searchable_locations[], created_at (unix seconds),
            organization { name, slug, industry_tags[], head_count, stage },
            compensation_amount_min_cents, compensation_amount_max_cents,
            compensation_currency, compensation_period, compensation_offers_equity,
            has_description, source

        The listing does NOT include the full job description. We synthesise
        a structured description from the fields we have — denser signal
        for the matcher than a marketing-fluff paragraph anyway.
        """
        source_id = str(raw.get("id") or raw.get("slug"))
        title = (raw.get("title") or "").strip()

        org = raw.get("organization") or {}
        company = (
            (isinstance(org, dict) and (org.get("name") or org.get("title")))
            or self.label
        )
        industry_tags = org.get("industry_tags", []) if isinstance(org, dict) else []

        # Locations — prefer the human-readable list
        locs = raw.get("locations") or raw.get("searchable_locations") or []
        location = ", ".join(str(x) for x in locs if x) if locs else None

        work_mode = raw.get("work_mode")  # 'remote' | 'hybrid' | 'onsite' | None
        remote = bool(
            (work_mode and "remote" in work_mode.lower())
            or (location and re.search(r"\bremote\b", location, re.I))
        )

        url = raw.get("url") or f"https://{self.host}/jobs/{source_id}"

        posted_at = _parse_unix(raw.get("created_at"))

        description = _synthesise_description(raw)

        return Job(
            id=Job.make_id(self.source_name, source_id),
            source=self.source_name,
            source_id=source_id,
            source_label=self.label,
            company=company,
            title=title,
            location=location,
            remote=remote,
            description=description,
            url=url,
            posted_at=posted_at,
            raw=raw,
        )


def _extract_items(data: Any) -> list[dict]:
    """Pull the items list out of a Getro response, regardless of envelope key.

    The /jobs endpoint wraps as {"results": {"jobs": [...]}}.
    The /organizations/all endpoint wraps as {"items": [...]}.
    We tolerate both, plus a bare list as a last resort."""
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    # Getro's /jobs endpoint nests under results.jobs
    results = data.get("results")
    if isinstance(results, dict):
        for key in ("jobs", "items", "data"):
            v = results.get(key)
            if isinstance(v, list):
                return v
    # Top-level fallbacks
    for key in ("items", "jobs", "data", "results", "hits"):
        v = data.get(key)
        if isinstance(v, list):
            return v
    return []


# =============================================================================
# HTML SCRAPER (fallback — works without collection_id but no descriptions)
# =============================================================================

class GetroScraper(BaseScraper):
    """Parses the rendered job listing page. Used when we don't have a
    collection_id, or as a fallback if the JSON API changes."""

    source_name = "getro"

    def __init__(
        self,
        subdomain: str | None = None,
        domain: str | None = None,
        label: str = "",
        **kwargs,
    ):
        if not subdomain and not domain:
            raise ValueError("GetroScraper needs either 'subdomain' or 'domain'")
        super().__init__(label=label or domain or subdomain or "", **kwargs)
        self.host = domain or f"{subdomain}.getro.com"
        self.base_url = f"https://{self.host}/jobs"

    async def fetch(self) -> list[Job]:
        async with self._client() as client:
            resp = await client.get(self.base_url)
            resp.raise_for_status()
            html = resp.text

        soup = BeautifulSoup(html, "html.parser")
        jobs: list[Job] = []

        for h4 in soup.find_all(["h3", "h4"]):
            anchor = h4.find("a", href=True)
            if not anchor:
                continue
            title = anchor.get_text(strip=True)
            url = anchor["href"]
            if not title or ("/jobs/" not in url and "/companies/" not in url):
                continue

            card = h4.find_parent()
            company = _find_company_near(card) or self.label
            location = _find_location_near(card)
            posted_text = _find_posted_text_near(card)
            posted_at = _interpret_relative_time(posted_text)

            source_id = _id_from_url(url) or url
            try:
                jobs.append(
                    Job(
                        id=Job.make_id(self.source_name, source_id),
                        source=self.source_name,
                        source_id=source_id,
                        source_label=self.label,
                        company=company,
                        title=title,
                        location=location,
                        remote=bool(location and re.search(r"\bremote\b", location, re.I)),
                        description="",
                        url=url if url.startswith("http") else f"https://{self.host}{url}",
                        posted_at=posted_at,
                        raw={"posted_text": posted_text},
                    )
                )
            except Exception as e:
                logger.warning("Getro HTML parse failed for %s: %s", title, e)

        return list({j.id: j for j in jobs}.values())


# --- Shared helpers -----------------------------------------------------------

def _html_to_text(html: str) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    return re.sub(r"\s+", " ", soup.get_text(separator=" ", strip=True))


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _parse_unix(value: int | float | None) -> datetime | None:
    """Getro's created_at is a UNIX timestamp in seconds."""
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def _synthesise_description(raw: dict[str, Any]) -> str:
    """Build a structured 'description' string from Getro's typed fields.

    The listing payload doesn't include a free-text description, but the
    structured fields (seniority, work_mode, salary, industry tags) are
    actually a denser signal for the matcher than a typical job description's
    marketing copy. We pack them into the description string so the existing
    pre-filter and Claude prompt work without modification.
    """
    lines: list[str] = []

    seniority = raw.get("seniority")
    if seniority:
        # 'mid_senior' -> 'mid senior' for keyword matching
        lines.append(f"Seniority: {seniority.replace('_', ' ')}")

    work_mode = raw.get("work_mode")
    if work_mode:
        lines.append(f"Work mode: {work_mode}")

    locs = raw.get("locations") or raw.get("searchable_locations") or []
    if locs:
        lines.append(f"Locations: {', '.join(str(x) for x in locs)}")

    # Compensation in cents — flatten to a readable line
    cmin = raw.get("compensation_amount_min_cents")
    cmax = raw.get("compensation_amount_max_cents")
    ccur = raw.get("compensation_currency") or "USD"
    cper = raw.get("compensation_period") or "year"
    if cmin or cmax:
        lo = f"{(cmin or 0) // 100:,}"
        hi = f"{(cmax or 0) // 100:,}"
        equity = " + equity" if raw.get("compensation_offers_equity") else ""
        if cmin and cmax and cmin != cmax:
            lines.append(f"Compensation: {ccur} {lo}–{hi} / {cper}{equity}")
        else:
            lines.append(f"Compensation: {ccur} {lo or hi} / {cper}{equity}")

    org = raw.get("organization") or {}
    if isinstance(org, dict):
        tags = org.get("industry_tags") or []
        if tags:
            # Dedup while preserving order
            seen = set()
            unique = [t for t in tags if not (t.lower() in seen or seen.add(t.lower()))]
            lines.append(f"Company industry: {', '.join(unique)}")
        if org.get("head_count"):
            lines.append(f"Company size: {org['head_count']} employees")
        stage = org.get("stage")
        if stage and stage != "other":
            lines.append(f"Company stage: {stage}")

    skills = raw.get("skills") or []
    if skills:
        lines.append(f"Skills: {', '.join(str(s) for s in skills)}")

    src = raw.get("source")
    if src:
        lines.append(f"Source: {src}")

    return "\n".join(lines)


def _find_company_near(card) -> Optional[str]:
    if not card:
        return None
    for a in card.find_all("a", href=True):
        if "/companies/" in a["href"]:
            text = a.get_text(strip=True)
            if text:
                return text
    return None


def _find_location_near(card) -> Optional[str]:
    if not card:
        return None
    for tag in card.find_all(["div", "span", "p"]):
        text = tag.get_text(strip=True)
        if 3 < len(text) < 120 and ("," in text or re.search(r"\b(USA|UK|EU|Remote|London|New York|Berlin|Paris)\b", text, re.I)):
            if text.count(",") > 4:
                continue
            return text
    return None


def _find_posted_text_near(card) -> Optional[str]:
    if not card:
        return None
    text = card.get_text(separator=" ", strip=True)
    m = re.search(r"\b(Today|Yesterday|\d+\s*(?:d|w|mo|m|h)\b|posted\s+\d+\s*\w+\s+ago)\b", text, re.I)
    return m.group(0) if m else None


def _interpret_relative_time(text: Optional[str]) -> Optional[datetime]:
    if not text:
        return None
    now = datetime.now(timezone.utc)
    t = text.strip().lower()
    if t.startswith("today"):
        return now
    if t.startswith("yesterday"):
        return now - timedelta(days=1)
    m = re.match(r"(\d+)\s*(d|w|mo|m|h)", t)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        if unit == "h":
            return now - timedelta(hours=n)
        if unit == "d":
            return now - timedelta(days=n)
        if unit == "w":
            return now - timedelta(weeks=n)
        if unit in ("mo", "m"):
            return now - timedelta(days=n * 30)
    return None


def _id_from_url(url: str) -> Optional[str]:
    m = re.search(r"/jobs/(\d+)-", url)
    return m.group(1) if m else None

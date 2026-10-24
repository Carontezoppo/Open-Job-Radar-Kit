"""
Northzone portfolio scraper.

Page: https://northzone.com/portfolio
Format: Webflow CMS list, 100 companies per page, paginated via a
        "?<id>_page=N" next link. Each .filters5_company-list-item card carries
        fs-cmsfilter-field children for name, stage, industry and status, plus
        a .filters5_location-list holding the country.

The card links to a Northzone detail page (/portfolio/<slug>), which has a
"company website" button — see website_for.
"""

from __future__ import annotations

import logging
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup, Tag

from ..base import BasePortfolioScraper, CompanyEntry

logger = logging.getLogger(__name__)

_URL = "https://northzone.com/portfolio"
_MAX_PAGES = 20  # guard against a pagination loop if the markup changes


class NorthzoneScraper(BasePortfolioScraper):
    vc_name = "northzone"
    default_location = "Europe"

    async def fetch(self) -> list[CompanyEntry]:
        entries: list[CompanyEntry] = []
        seen: set[str] = set()
        url: str | None = _URL
        async with self._client() as client:
            for _ in range(_MAX_PAGES):
                if not url:
                    break
                r = await client.get(url)
                r.raise_for_status()
                soup = BeautifulSoup(r.text, "html.parser")
                entries.extend(_parse(soup, self.default_location, seen))
                next_link = soup.select_one("a.w-pagination-next[href]")
                url = urljoin(_URL, str(next_link["href"])) if next_link else None

        logger.debug("[northzone] parsed %d companies", len(entries))
        return entries

    async def website_for(self, client: httpx.AsyncClient, url: str) -> str:
        r = await client.get(url)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.find_all("a", href=True):
            # Some entries have no site and link "/" instead
            href = str(a["href"])
            if a.get_text(strip=True).lower() == "company website" and href.startswith("http"):
                return href
        return ""


def _parse(soup: BeautifulSoup, default_location: str, seen: set[str]) -> list[CompanyEntry]:
    entries: list[CompanyEntry] = []
    for card in soup.select(".filters5_company-list-item"):
        name = _field(card, "name")
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())

        # Acquired companies no longer hire independently, so they aren't leads
        if _field(card, "status").lower() == "acquired":
            continue

        link = card.find("a", href=True)
        industries = [
            el.get_text(strip=True)
            for el in card.select(".filters5_industry-list [fs-cmsfilter-field]")
        ]
        countries = [
            el.get_text(strip=True)
            for el in card.select(".filters5_location-list [fs-cmsfilter-field]")
        ]

        entries.append(CompanyEntry(
            name=name,
            url=urljoin(_URL, str(link["href"])) if link else "",
            stage=_field(card, "stage").lower() or "unknown",
            sectors=[i for i in industries if i][:5],
            location=", ".join(c for c in countries if c) or default_location,
        ))
    return entries


def _field(card: Tag, field: str) -> str:
    el = card.select_one(f'[fs-cmsfilter-field="{field}"]')
    return el.get_text(strip=True) if el else ""

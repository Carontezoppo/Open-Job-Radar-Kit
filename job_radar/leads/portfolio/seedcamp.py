"""
Seedcamp portfolio scraper.

Page: https://seedcamp.com/our-companies/ (moved from /portfolio/, which now 404s)
Format: static HTML, every company in one page load. Each div.company__item
        has the name, the year Seedcamp invested, a one-line description, a
        direct link to the company's own website, and a sector as an extra
        class on the item ("company__item mix fintech").

Seedcamp is a European seed fund, so no location filter is needed; the page
gives no location, so it is left to the Claude lookup.
"""

from __future__ import annotations

import logging

from bs4 import BeautifulSoup

from ..base import BasePortfolioScraper, CompanyEntry, stage_from_year

logger = logging.getLogger(__name__)

_URL = "https://seedcamp.com/our-companies/"

# Sector classes → the labels the Claude lookup uses, so leads from every
# source share one vocabulary. "enterprise" is deliberately dropped: like
# "B2B SaaS" it covers most startups and says nothing; the lookup fills in
# a real domain instead.
_SECTORS = {
    "ai": "AI/ML",
    "fintech": "fintech",
    "health-bio": "health-tech",
    "developer-tools": "developer tools",
    "consumer": "consumer",
    "security": "security",
    "climate": "climate",
    "marketplaces": "marketplaces",
    "crypto": "crypto",
}


class SeedcampScraper(BasePortfolioScraper):
    vc_name = "seedcamp"
    default_location = "Europe"

    async def fetch(self) -> list[CompanyEntry]:
        async with self._client() as client:
            r = await client.get(_URL)
            r.raise_for_status()

        soup = BeautifulSoup(r.text, "html.parser")
        entries = _parse(soup, self.default_location)
        logger.debug("[seedcamp] parsed %d companies", len(entries))
        return entries


def _parse(soup: BeautifulSoup, default_location: str) -> list[CompanyEntry]:
    entries: list[CompanyEntry] = []
    seen: set[str] = set()
    for item in soup.select("div.company__item"):
        name_el = item.select_one(".company__item__name")
        if not name_el:
            continue
        name = name_el.get_text(strip=True)
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())

        link = item.select_one("a.company__item__link[href]")
        year = item.select_one(".company__item__year")
        blurb = item.select_one(".company__item__description__content")
        sectors = [_SECTORS[c] for c in item.get("class", []) if c in _SECTORS]

        entries.append(CompanyEntry(
            name=name,
            url=str(link["href"]) if link else "",
            blurb=blurb.get_text(strip=True) if blurb else "",
            location=default_location,
            stage=stage_from_year(year.get_text(strip=True) if year else ""),
            sectors=sectors,
        ))
    return entries

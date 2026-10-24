"""
Antler portfolio scraper.

Page: https://www.antler.co/portfolio
Format: Webflow CMS list, 52 companies per page across ~22 pages, paginated
        via a "?<id>_page=N" next link. Each .portco_card has
        fs-cmsfilter-field="name" and "description", a direct link to the
        company's website, and three .tag_small_wrap tags whose own
        fs-cmsfilter-field says what they are: "sector", "year", or — for the
        location — the location value itself ("UK"). The inner text elements
        are all labelled "location", so tags are read by their wrapper.

Antler is global (~1,100 companies, three-quarters outside Europe), so only
UK and European companies are kept.
"""

from __future__ import annotations

import logging
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from ..base import BasePortfolioScraper, CompanyEntry, stage_from_year
from ..heuristic import is_european

logger = logging.getLogger(__name__)

_URL = "https://www.antler.co/portfolio"
_MAX_PAGES = 60  # guard against a pagination loop if the markup changes


class AntlerScraper(BasePortfolioScraper):
    vc_name = "antler"
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
                entries.extend(_parse(soup, seen))
                next_link = soup.select_one("a.w-pagination-next[href]")
                url = urljoin(_URL, str(next_link["href"])) if next_link else None

        logger.debug("[antler] parsed %d European companies", len(entries))
        return entries


def _parse(soup: BeautifulSoup, seen: set[str]) -> list[CompanyEntry]:
    entries: list[CompanyEntry] = []
    for card in soup.select(".portco_card"):
        name = _text(card.select_one('[fs-cmsfilter-field="name"]'))
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())

        sector, year, location = "", "", ""
        for tag in card.select(".tag_small_wrap"):
            kind = tag.get("fs-cmsfilter-field", "")
            if kind == "sector":
                sector = _text(tag)
            elif kind == "year":
                year = _text(tag)
            else:
                location = _text(tag)

        if not is_european(location):
            continue

        link = card.select_one("a.clickable_link[href]")
        entries.append(CompanyEntry(
            name=name,
            url=str(link["href"]) if link else "",
            blurb=_text(card.select_one('[fs-cmsfilter-field="description"]')),
            location=location,
            stage=stage_from_year(year),
            # "B2B Software" is Antler's equivalent of "B2B SaaS": too broad to
            # be useful, so leave the sector to the lookup instead.
            sectors=[sector] if sector and sector != "B2B Software" else [],
        ))
    return entries


def _text(el: Tag | None) -> str:
    return el.get_text(strip=True) if el else ""

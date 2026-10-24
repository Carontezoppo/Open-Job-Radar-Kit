"""
Entrepreneur First portfolio scraper.

Page: https://www.joinef.com/portfolio/ (moved from /companies/)
Format: WordPress. The page HTML holds the featured companies plus the first
        page of the rest; further pages come from the same admin-ajax
        "loadmore" action the page's own Load more button calls, fed the
        query blob the page embeds in `php_variables`. Pages run until one
        comes back empty. Each .tile--company has the name and slug, a city
        tag (.locationtag), sector tags (.categorytag), a description and a
        "Founded" year. The company website is only in the details overlay,
        fetched with the "getcompany" action — see website_for.

EF runs cohorts worldwide, so only UK and European companies are kept.
"""

from __future__ import annotations

import json
import logging
import re
from urllib.parse import parse_qs, urlparse

import httpx
from bs4 import BeautifulSoup, Tag

from ..base import BasePortfolioScraper, CompanyEntry, stage_from_year
from ..heuristic import is_european

logger = logging.getLogger(__name__)

_URL = "https://www.joinef.com/portfolio/"
_AJAX_URL = "https://www.joinef.com/wp-admin/admin-ajax.php"
_MAX_PAGES = 60  # guard against an endless loop if the endpoint changes


class EFScraper(BasePortfolioScraper):
    vc_name = "entrepreneur first"
    default_location = "Europe"

    async def fetch(self) -> list[CompanyEntry]:
        seen: set[str] = set()
        async with self._client() as client:
            r = await client.get(_URL)
            r.raise_for_status()
            entries = _parse(r.text, seen)

            m = re.search(r"var php_variables = (\{.*?\});", r.text)
            query = json.loads(m.group(1))["posts"] if m else None
            for page in range(1, _MAX_PAGES + 1):
                if not query:
                    break
                r = await client.post(_AJAX_URL, data={
                    "action": "loadmore", "query": query, "page": page, "format": "default",
                })
                r.raise_for_status()
                if "data-companyslug" not in r.text:
                    break
                entries.extend(_parse(r.text, seen))

        logger.debug("[ef] parsed %d European companies", len(entries))
        return entries

    async def website_for(self, client: httpx.AsyncClient, url: str) -> str:
        slug = parse_qs(urlparse(url).query).get("company", [""])[0]
        if not slug:
            return ""
        r = await client.post(_AJAX_URL, data={"action": "getcompany", "company": slug})
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        label = soup.find(string=re.compile(r"^\s*Website\s*$"))
        link = label.find_next("a", href=True) if label else None
        return str(link["href"]) if link else ""


def _parse(html: str, seen: set[str]) -> list[CompanyEntry]:
    soup = BeautifulSoup(html, "html.parser")
    entries: list[CompanyEntry] = []
    for tile in soup.select(".tile--company"):
        head = tile.select_one("[data-companyslug]")
        if not head:
            continue
        name = head.get("data-companyname", "").strip()
        slug = head.get("data-companyslug", "")
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())

        location = ", ".join(_text(t) for t in tile.select(".locationtag"))
        if not is_european(location):
            continue

        entries.append(CompanyEntry(
            name=name,
            # A real link: the portfolio page opens this company's overlay
            url=f"{_URL}?company={slug}",
            blurb=_text(tile.select_one(".tile__description")),
            location=location,
            stage=stage_from_year(_founded(tile)),
            # "Enterprise Services" is EF's "B2B SaaS": too broad to be useful
            sectors=[
                _text(t) for t in tile.select(".categorytag")
                if _text(t) != "Enterprise Services"
            ][:3],
        ))
    return entries


def _founded(tile: Tag) -> str:
    # Rows are "label|value": "CEO|<name>", "Founded|2021"
    for row in tile.select(".meta__row"):
        parts = row.get_text("|", strip=True).split("|")
        if parts[0] == "Founded" and len(parts) > 1:
            return parts[1]
    return ""


def _text(el: Tag | None) -> str:
    return el.get_text(strip=True) if el else ""

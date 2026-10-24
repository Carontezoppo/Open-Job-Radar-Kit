"""
Index Ventures portfolio scraper.

Page: https://www.indexventures.com/companies/
Format: static HTML grid; each portfolio company is an <a> linking to
        /companies/<slug>. Public and acquired companies carry a
        <span class="ticker-symbol"> inside the link holding the ticker
        ("NASDAQ: HOOD") or the acquirer ("Discord").

We first try to extract __NEXT_DATA__ JSON (richer, includes website URLs
and stage tags). If absent, we fall back to HTML link parsing with strict
filters to exclude navigation/filter CTA links that also contain /companies/.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from ..base import BasePortfolioScraper, CompanyEntry

logger = logging.getLogger(__name__)

_URL = "https://www.indexventures.com/companies/"

# Words that only appear in navigation labels, never in company names
_NAV_PREFIXES = re.compile(
    r"^(see|view|browse|all|filter|show|load|explore)\b",
    re.IGNORECASE,
)
_NAV_WORDS_IN_NAME = re.compile(
    r"\bcompanies\b|\bportfolio\b|\bseed companies\b",
    re.IGNORECASE,
)
# Ticker span contents for listed companies, e.g. "NASDAQ:DDOG", "NYSE: RBLX".
# Anything else in that span is an acquirer name, apart from "f.k.a." aliases.
_TICKER = re.compile(r"^[A-Z]+\s*:\s*[A-Z0-9.]+$")


class IndexVenturesScraper(BasePortfolioScraper):
    vc_name = "index ventures"
    default_location = "Europe"

    async def fetch(self) -> list[CompanyEntry]:
        async with self._client() as client:
            r = await client.get(_URL)
            r.raise_for_status()

        soup = BeautifulSoup(r.text, "html.parser")

        # Try __NEXT_DATA__ first — Next.js embeds the full page props as JSON.
        # If present it's richer (website URL, stage, description) and avoids
        # the need to filter nav links out of the HTML.
        entries = _parse_next_data(soup, self.default_location)
        if entries:
            logger.debug("[index ventures] parsed %d companies from __NEXT_DATA__", len(entries))
            return entries

        # Fall back to HTML link parsing with strict nav-link filtering
        entries = _parse_html(soup, self.default_location)
        logger.debug("[index ventures] parsed %d companies from HTML", len(entries))
        return entries

    async def website_for(self, client: httpx.AsyncClient, url: str) -> str:
        """The detail page's JSON-LD Organization carries the company's site."""
        r = await client.get(url)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
            except json.JSONDecodeError:
                continue
            for node in data.get("@graph", []) if isinstance(data, dict) else []:
                if (
                    node.get("@type") == "Organization"
                    and "indexventures.com" not in node.get("url", "")
                ):
                    return node.get("url", "")
        return ""


def _parse_next_data(soup: BeautifulSoup, default_location: str) -> list[CompanyEntry]:
    """Try to extract company data from Next.js hydration JSON."""
    script = soup.find("script", id="__NEXT_DATA__")
    if not script or not script.string:
        return []
    try:
        data = json.loads(script.string)
    except (json.JSONDecodeError, AttributeError):
        return []

    # Search the JSON tree for any list that looks like portfolio companies.
    # Index's Next.js structure varies — we walk the tree looking for objects
    # with a "name" and ("url" or "website") key.
    companies = _find_company_list(data)
    if not companies:
        return []

    entries: list[CompanyEntry] = []
    seen: set[str] = set()
    for item in companies:
        name = item.get("name") or item.get("title") or item.get("companyName") or ""
        name = str(name).strip()
        if not name or len(name) < 2:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)

        url = (
            item.get("websiteUrl") or item.get("website") or item.get("url")
            or item.get("externalUrl") or ""
        )
        stage = str(item.get("stage") or item.get("fundingStage") or "unknown")
        location = str(item.get("location") or item.get("country") or default_location)
        blurb = str(item.get("description") or item.get("blurb") or "")[:300]
        sectors_raw = item.get("sectors") or item.get("tags") or item.get("industries") or []
        sectors = [str(s) for s in sectors_raw if s][:5]

        entries.append(CompanyEntry(
            name=name,
            url=url,
            blurb=blurb,
            location=location or default_location,
            stage=stage,
            sectors=sectors,
        ))

    return entries


def _find_company_list(obj, _depth: int = 0) -> list[dict]:
    """Recursively walk JSON looking for a list of company-like objects."""
    if _depth > 8:
        return []
    if isinstance(obj, list) and len(obj) >= 5:
        sample = [x for x in obj if isinstance(x, dict)][:3]
        if sample and any("name" in s or "title" in s or "companyName" in s for s in sample):
            return [x for x in obj if isinstance(x, dict)]
    if isinstance(obj, dict):
        for v in obj.values():
            result = _find_company_list(v, _depth + 1)
            if result:
                return result
    elif isinstance(obj, list):
        for item in obj:
            result = _find_company_list(item, _depth + 1)
            if result:
                return result
    return []


def _parse_html(soup: BeautifulSoup, default_location: str) -> list[CompanyEntry]:
    """HTML fallback: find /companies/<slug> links, reject nav labels."""
    entries: list[CompanyEntry] = []
    seen: set[str] = set()

    for a in soup.find_all("a", href=True):
        href: str = a["href"]
        if "/companies/" not in href:
            continue

        # Reject query strings (filter links like /companies/?stage=seed)
        # and hash anchors
        if "?" in href or "#" in href:
            continue

        slug = href.rstrip("/").split("/companies/")[-1].strip("/")
        # Nested paths (e.g. /companies/backed/all-seed/) are section pages, not companies
        if not slug or slug in ("all", "portfolio", "companies") or "/" in slug:
            continue

        # Acquired companies no longer hire independently, so they aren't leads
        ticker = a.find("span", class_="ticker-symbol")
        if ticker:
            label = ticker.get_text(strip=True)
            if not _TICKER.match(label) and not label.lower().startswith("f.k.a."):
                continue
            ticker.extract()

        name = _extract_text(a)
        if not name or len(name) < 2 or len(name) > 50:
            continue

        # Reject navigation labels
        if _NAV_PREFIXES.match(name) or _NAV_WORDS_IN_NAME.search(name):
            continue

        key = name.lower().strip()
        if key in seen:
            continue
        seen.add(key)

        entries.append(CompanyEntry(
            name=name,
            url=f"https://www.indexventures.com{href}" if href.startswith("/") else href,
            location=default_location,
        ))

    return entries


def _extract_text(tag) -> str:
    text = tag.get_text(separator=" ", strip=True)
    for noise in (" View all", " Learn more", " →", " ↗"):
        text = text.removesuffix(noise)
    # Strip pipe-separated alternative names ("KRY | LIVI" → "KRY")
    if " | " in text:
        text = text.split(" | ")[0]
    return text.strip()

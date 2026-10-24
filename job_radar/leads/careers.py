"""
Find a company's careers page from its website.

Best-effort, no LLM: fetch the homepage and look for a link to a known ATS
board (the most direct route to open roles) or a link labelled like a careers
page; failing that, probe /careers and /jobs. Sites that block bots or render
everything client-side simply yield "", and the lead keeps its website link.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_ATS_HOSTS = (
    "ashbyhq.com", "greenhouse.io", "lever.co", "workable.com", "teamtailor.com",
    "personio.", "recruitee.com", "smartrecruiters.com", "bamboohr.com",
    "welcometothejungle.com", "breezy.hr", "join.com", "pinpointhq.com",
)
_CAREERS_TEXT = re.compile(
    r"^(careers?|jobs|join us|join the team|we'?re hiring|open roles|work with us)$",
    re.IGNORECASE,
)
_CAREERS_PATH = re.compile(r"/(careers?|jobs|join-us|open-roles)(/|$)", re.IGNORECASE)
_CAREERS_SUBDOMAIN = re.compile(r"^(careers?|jobs)\.", re.IGNORECASE)


async def find_careers(client: httpx.AsyncClient, website: str) -> str:
    try:
        r = await client.get(website)
        r.raise_for_status()
    except httpx.HTTPError as e:
        logger.debug("careers: %s unreachable: %s", website, e)
        return ""

    base = str(r.url)
    soup = BeautifulSoup(r.text, "html.parser")
    labelled = ""
    for a in soup.find_all("a", href=True):
        href = urljoin(base, str(a["href"]))
        if not href.startswith("http"):
            continue
        parts = urlparse(href)
        if any(h in parts.netloc for h in _ATS_HOSTS):
            return href
        if not labelled and (
            _CAREERS_TEXT.match(a.get_text(" ", strip=True))
            or _CAREERS_PATH.search(parts.path)
            or _CAREERS_SUBDOMAIN.match(parts.netloc)
        ):
            labelled = href
    if labelled:
        return labelled

    for path in ("/careers", "/jobs"):
        url = urljoin(base, path)
        try:
            probe = await client.get(url)
        except httpx.HTTPError:
            continue
        # A redirect back to the homepage means there is no such page
        if probe.status_code == 200 and _CAREERS_PATH.search(probe.url.path):
            return str(probe.url)
    return ""

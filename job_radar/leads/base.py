"""
Base types for portfolio page scrapers.

Each scraper fetches a VC's portfolio page and returns a list of CompanyEntry
objects. The runner handles snapshotting and diffing — scrapers just fetch.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import httpx


@dataclass
class CompanyEntry:
    name: str
    url: str = ""
    blurb: str = ""
    location: str = ""
    stage: str = "unknown"
    sectors: list[str] = field(default_factory=list)


def stage_from_year(year: str) -> str:
    """
    Seed-focused programmes (Seedcamp, Antler, EF) publish the year they backed
    a company, not its current stage. Backed this year or last means it is
    almost certainly still pre-seed/seed; older than that we can't tell.
    """
    from datetime import date
    return "seed" if year.isdigit() and int(year) >= date.today().year - 1 else "unknown"


class BasePortfolioScraper(ABC):
    vc_name: str = ""
    default_location: str = "Europe"

    def __init__(self, *, timeout: float = 20.0):
        self._timeout = timeout

    @abstractmethod
    async def fetch(self) -> list[CompanyEntry]:
        ...

    async def website_for(self, client: httpx.AsyncClient, url: str) -> str:
        """
        The company's own website, given the CompanyEntry url. By default that
        url already is the website; scrapers whose url points at a VC detail
        page override this to read the site from that page.
        """
        return url

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self._timeout,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-GB,en;q=0.9",
            },
            follow_redirects=True,
        )

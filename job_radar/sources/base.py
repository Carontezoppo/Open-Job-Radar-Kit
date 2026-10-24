"""
Base contract for any source.

Every scraper takes its own config in __init__ and exposes one async method:
    fetch() -> list[Job]

That's the whole interface. Adding a new source = subclass + implement fetch.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import httpx

from ..models import Job


class BaseScraper(ABC):
    """Abstract base for every job source."""

    # Subclasses set this so the runner can log nicely
    source_name: str = "base"

    def __init__(self, label: str = "", *, timeout: float = 20.0):
        self.label = label
        self._timeout = timeout

    @abstractmethod
    async def fetch(self) -> list[Job]:
        """Pull jobs from the source and return normalised Job objects."""
        ...

    # --- Helpers shared by subclasses -----------------------------------------
    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self._timeout,
            headers={
                "User-Agent": "job-radar/0.1 (personal job aggregator)",
                "Accept": "application/json, text/html",
            },
            follow_redirects=True,
        )

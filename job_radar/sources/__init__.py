"""Source adapters: each returns a list[Job] from a different job board."""

from .base import BaseScraper
from .greenhouse import GreenhouseScraper
from .lever import LeverScraper
from .getro import GetroScraper, GetroJsonScraper
from .consider import ConsiderScraper
from .ashby import AshbyScraper

__all__ = [
    "BaseScraper",
    "GreenhouseScraper",
    "LeverScraper",
    "GetroScraper",
    "GetroJsonScraper",
    "ConsiderScraper",
    "AshbyScraper",
]

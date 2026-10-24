from .antler import AntlerScraper
from .ef import EFScraper
from .index import IndexVenturesScraper
from .northzone import NorthzoneScraper
from .seedcamp import SeedcampScraper

PORTFOLIO_SCRAPERS = [
    IndexVenturesScraper(),
    NorthzoneScraper(),
    SeedcampScraper(),
    EFScraper(),
    AntlerScraper(),
]

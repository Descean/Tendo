from app.services.scraping.base import BaseScraper
from app.services.scraping.marches_publics_bj import MarchesPublicsBJScraper
from app.services.scraping.armp import ARMPScraper
from app.services.scraping.gouv_bj import GouvBJScraper
from app.services.scraping.adpme import ADPMEScraper
from app.services.scraping.abe import ABEScraper
from app.services.scraping.generic import GenericScraper

ALL_SCRAPERS = {
    "marches-publics.bj": MarchesPublicsBJScraper,
    "ARMP": ARMPScraper,
    "gouv.bj": GouvBJScraper,
    "ADPME": ADPMEScraper,
    "ABE": ABEScraper,
}

__all__ = [
    "BaseScraper", "MarchesPublicsBJScraper", "ARMPScraper",
    "GouvBJScraper", "ADPMEScraper", "ABEScraper", "GenericScraper",
    "ALL_SCRAPERS",
]

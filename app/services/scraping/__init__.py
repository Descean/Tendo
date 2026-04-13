from app.services.scraping.base import BaseScraper

# ── Scrapers Benin (prioritaires) ──
from app.services.scraping.marches_publics_bj import MarchesPublicsBJScraper
from app.services.scraping.armp import ARMPScraper
from app.services.scraping.gouv_bj import GouvBJScraper
from app.services.scraping.adpme import ADPMEScraper
from app.services.scraping.abe import ABEScraper
from app.services.scraping.jnmp import JNMPScraper

# ── Scrapers Bailleurs internationaux ──
from app.services.scraping.bad import BADScraper
from app.services.scraping.afd import AFDScraper
from app.services.scraping.banque_mondiale import BanqueMondialesScraper
from app.services.scraping.ungm import UNGMScraper
from app.services.scraping.unfpa import UNFPAScraper
from app.services.scraping.tns import TNSScraper

# ── Scrapers Regionaux ──
from app.services.scraping.uemoa import UEMOAScraper
from app.services.scraping.j360 import J360Scraper

# ── ONG et organisations ──
from app.services.scraping.care_benin import CAREBeninScraper
from app.services.scraping.ong_benin import ASINScraper, GIZBeninScraper, ENABELBeninScraper

# ── Generique ──
from app.services.scraping.generic import GenericScraper

ALL_SCRAPERS = {
    # Benin — quotidien
    # "marches-publics.bj": MarchesPublicsBJScraper,  # DESACTIVE : site Angular SPA, scrape les menus de navigation
    "ARMP": ARMPScraper,
    "gouv.bj": GouvBJScraper,
    "ADPME": ADPMEScraper,
    "ABE": ABEScraper,
    "JNMP": JNMPScraper,
    # Bailleurs — quotidien/hebdo
    "BAD": BADScraper,
    "AFD": AFDScraper,
    "Banque Mondiale": BanqueMondialesScraper,
    "UNGM": UNGMScraper,
    "UNFPA": UNFPAScraper,
    "TNS": TNSScraper,
    # Regional — 2-3 fois/semaine
    "UEMOA": UEMOAScraper,
    "J360": J360Scraper,
    # ONG et organisations — hebdo
    "CARE Benin": CAREBeninScraper,
    "ASIN": ASINScraper,
    "GIZ": GIZBeninScraper,
    "ENABEL": ENABELBeninScraper,
}

__all__ = [
    "BaseScraper", "ALL_SCRAPERS", "GenericScraper",
    "MarchesPublicsBJScraper", "ARMPScraper", "GouvBJScraper",
    "ADPMEScraper", "ABEScraper", "JNMPScraper",
    "BADScraper", "AFDScraper", "BanqueMondialesScraper",
    "UNGMScraper", "UNFPAScraper", "TNSScraper",
    "UEMOAScraper", "J360Scraper",
    "CAREBeninScraper", "ASINScraper", "GIZBeninScraper", "ENABELBeninScraper",
]

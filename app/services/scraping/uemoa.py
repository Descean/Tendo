"""Scraper UEMOA – Portail des appels d'offres de l'UEMOA."""

from typing import List
from datetime import datetime

from app.services.scraping.base import BaseScraper
from app.utils.logger import logger


class UEMOAScraper(BaseScraper):
    """Scraper pour les appels d'offres regionaux UEMOA."""

    name = "UEMOA"
    source = "UEMOA"
    base_url = "https://uemoa.switch-maker.net/pt/appel-d-offre"

    def scrape(self) -> List[dict]:
        publications = []

        try:
            soup = self.fetch_page(self.base_url)

            # Chercher les appels d'offres listes
            items = soup.select(
                ".views-row, .node--type-appel, article, "
                "table tbody tr, .ao-item, .tender-item"
            )

            if not items:
                # Essayer les liens directement
                items = soup.select("a[href*='appel'], a[href*='offre']")

            for item in items[:30]:
                if item.name == "a":
                    link = item
                else:
                    link = item.select_one("a")

                if not link:
                    continue

                title = self.clean_text(link.get_text())
                if not title or len(title) < 10:
                    continue

                href = link.get("href", "")
                if href and not href.startswith("http"):
                    href = f"https://uemoa.switch-maker.net{href}"

                reference = self.generate_reference("UEMOA", title, href)

                # Detecter le pays dans le titre
                country = self._detect_country(title)

                publications.append({
                    "source": self.source,
                    "reference": reference,
                    "title": title[:500],
                    "summary": "",
                    "budget": None,
                    "deadline": None,
                    "pdf_url": href,
                    "html_content": "",
                    "category": "marche",
                    "sectors": self._detect_sectors(title),
                    "regions": [],
                    "published_date": self.now_utc(),
                    "authority_name": "UEMOA",
                    "authority_email": None,
                    "document_type": "AAO",
                    "financing_source": "UEMOA",
                    "country": country,
                })

        except Exception as e:
            logger.error(f"[{self.name}] Erreur scraping: {e}")

        return publications

    def _detect_country(self, title: str) -> str:
        title_lower = title.lower()
        countries = {
            "Benin": ["benin", "cotonou"],
            "Senegal": ["senegal", "dakar"],
            "Togo": ["togo", "lome"],
            "Niger": ["niger", "niamey"],
            "Burkina Faso": ["burkina", "ouagadougou"],
            "Cote d'Ivoire": ["ivoire", "abidjan"],
            "Mali": ["mali", "bamako"],
            "Guinee-Bissau": ["guinee-bissau", "bissau"],
        }
        for country, keywords in countries.items():
            if any(kw in title_lower for kw in keywords):
                return country
        return "UEMOA"

    def _detect_sectors(self, title: str) -> list:
        title_lower = title.lower()
        sectors = []
        if any(w in title_lower for w in ("travaux", "construction", "infrastructure")):
            sectors.append("BTP")
        if any(w in title_lower for w in ("fourniture", "equipement")):
            sectors.append("Fournitures")
        if any(w in title_lower for w in ("service", "consultant", "etude")):
            sectors.append("Services")
        return sectors or ["Autres"]

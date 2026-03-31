"""Scraper J360 – Agregateur francophone d'appels d'offres Afrique."""

from typing import List
from datetime import datetime

from app.services.scraping.base import BaseScraper
from app.utils.logger import logger


class J360Scraper(BaseScraper):
    """Scraper pour J360 – appels d'offres Afrique francophone."""

    name = "J360"
    source = "J360"
    base_url = "https://www.j360.info/appels-d-offres/afrique/"

    def scrape(self) -> List[dict]:
        publications = []

        try:
            soup = self.fetch_page(self.base_url)

            # Chercher les appels d'offres
            articles = soup.select(
                "article, .post, .entry, .ao-item, "
                ".tender-item, .views-row, .card"
            )

            if not articles:
                # Essayer les liens
                articles = soup.select("a[href*='appel'], a[href*='offre'], a[href*='tender']")

            for article in articles[:30]:
                if article.name == "a":
                    link = article
                    title = self.clean_text(link.get_text())
                else:
                    link = article.select_one("h2 a, h3 a, .title a, a")
                    title = self.clean_text(link.get_text()) if link else ""

                if not title or len(title) < 15:
                    continue

                href = link.get("href", "") if link else ""
                if href and not href.startswith("http"):
                    href = f"https://www.j360.info{href}"

                reference = self.generate_reference("J360", title, href)
                country = self._detect_country(title)

                # Ne garder que les pays UEMOA / Afrique de l'Ouest
                if country not in (
                    "Benin", "Senegal", "Togo", "Niger", "Burkina Faso",
                    "Cote d'Ivoire", "Mali", "Guinee", "Afrique",
                ):
                    continue

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
                    "authority_name": "",
                    "authority_email": None,
                    "document_type": "AAO",
                    "financing_source": None,
                    "country": country,
                })

        except Exception as e:
            logger.error(f"[{self.name}] Erreur scraping: {e}")

        return publications

    def _detect_country(self, title: str) -> str:
        title_lower = title.lower()
        countries = {
            "Benin": ["benin", "cotonou", "porto-novo"],
            "Senegal": ["senegal", "dakar"],
            "Togo": ["togo", "lome"],
            "Niger": ["niger", "niamey"],
            "Burkina Faso": ["burkina", "ouagadougou"],
            "Cote d'Ivoire": ["ivoire", "abidjan"],
            "Mali": ["mali", "bamako"],
            "Guinee": ["guinee", "conakry"],
            "Cameroun": ["cameroun", "douala", "yaounde"],
        }
        for country, kws in countries.items():
            if any(kw in title_lower for kw in kws):
                return country
        return "Afrique"

    def _detect_sectors(self, title: str) -> list:
        title_lower = title.lower()
        sectors = []
        if any(w in title_lower for w in ("travaux", "construction", "route", "batiment")):
            sectors.append("BTP")
        if any(w in title_lower for w in ("fourniture", "equipement", "materiel")):
            sectors.append("Fournitures")
        if any(w in title_lower for w in ("service", "consultant", "etude", "audit")):
            sectors.append("Services")
        if any(w in title_lower for w in ("sante", "medical")):
            sectors.append("Sante")
        return sectors or ["Autres"]

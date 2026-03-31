"""Scraper TNS – The New School / buy.tns.org."""

from typing import List
from datetime import datetime

from app.services.scraping.base import BaseScraper
from app.utils.logger import logger


class TNSScraper(BaseScraper):
    """Scraper pour le portail TNS des opportunites publiques."""

    name = "TNS"
    source = "TNS"
    base_url = "https://buy.tns.org/public-opportunity-portal"

    def scrape(self) -> List[dict]:
        publications = []

        try:
            soup = self.fetch_page(self.base_url)

            # Chercher les opportunites listees
            items = soup.select(
                "table tbody tr, .opportunity-item, .card, "
                ".list-group-item, article, .views-row"
            )

            for item in items[:30]:
                link = item.select_one("a")
                if not link:
                    continue

                title = self.clean_text(link.get_text())
                if not title or len(title) < 10:
                    continue

                href = link.get("href", "")
                if href and not href.startswith("http"):
                    href = f"https://buy.tns.org{href}"

                reference = self.generate_reference("TNS", title, href)

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
                    "sectors": ["Services"],
                    "regions": [],
                    "published_date": self.now_utc(),
                    "authority_name": "TNS",
                    "authority_email": None,
                    "document_type": "AAO",
                    "financing_source": "International",
                    "country": None,
                })

        except Exception as e:
            logger.error(f"[{self.name}] Erreur scraping: {e}")

        return publications

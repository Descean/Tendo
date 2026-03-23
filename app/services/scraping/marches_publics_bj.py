"""Scraper pour le Portail des Marchés Publics du Bénin.

Note: Le site principal www.marches-publics.bj est un SPA Angular qui renvoie 404
sur /avis côté serveur. Le vrai contenu est sur SIGMAP (sigmap.marches-publics.bj)
qui nécessite un compte. On utilise plutôt gouv.bj comme source fiable.

Ce scraper essaie la page d'accueil et les pages disponibles côté serveur.
"""

from typing import List
from urllib.parse import urljoin

from app.services.scraping.base import BaseScraper
from app.utils.logger import logger


class MarchesPublicsBJScraper(BaseScraper):
    name = "marches-publics.bj"
    base_url = "https://www.marches-publics.bj"
    source = "marches-publics.bj"

    def scrape(self) -> List[dict]:
        publications = []

        # Le site est un SPA Angular – les données sont chargées côté client
        # On essaie la page d'accueil qui peut contenir des avis récents
        try:
            soup = self.fetch_page(self.base_url)

            # Chercher les liens vers des avis/publications dans le HTML rendu
            links = soup.select("a[href]")
            seen_urls = set()

            for link in links:
                href = link.get("href", "")
                text = self.clean_text(link.get_text())

                # Filtrer les liens pertinents (avis, appel d'offres, etc.)
                if not text or len(text) < 10:
                    continue
                full_url = urljoin(self.base_url, href)
                if full_url in seen_urls:
                    continue
                seen_urls.add(full_url)

                # Seuls les liens qui pointent vers le domaine
                if "marches-publics.bj" not in full_url:
                    continue

                pub = {
                    "source": self.source,
                    "reference": self.generate_reference(self.source, text, full_url),
                    "title": text[:500],
                    "summary": "",
                    "budget": None,
                    "deadline": None,
                    "pdf_url": full_url if full_url.endswith(".pdf") else None,
                    "html_content": "",
                    "category": "marché",
                    "sectors": [],
                    "regions": ["Bénin"],
                    "published_date": "",
                    "authority_email": None,
                    "authority_name": None,
                }
                publications.append(pub)

        except Exception as e:
            logger.warning(f"[{self.name}] Erreur: {e}")

        return publications

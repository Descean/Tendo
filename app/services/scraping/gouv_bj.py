"""Scraper pour le portail Gouvernement du Bénin – Marchés Publics.

Ce scraper est le plus fiable : gouv.bj utilise du HTML server-rendered
avec des balises <article> bien structurées.
"""

from typing import List
from urllib.parse import urljoin

from app.services.scraping.base import BaseScraper
from app.utils.logger import logger


class GouvBJScraper(BaseScraper):
    name = "gouv.bj"
    base_url = "https://www.gouv.bj/opportunites/marches-publics/"
    source = "gouv.bj"

    def scrape(self) -> List[dict]:
        publications = []

        try:
            soup = self.fetch_page(self.base_url)

            # Le site utilise des <article> avec class "flex row bg-gray news"
            articles = soup.select("article")

            for article in articles[:50]:
                pub = self._parse_article(article)
                if pub:
                    publications.append(pub)

        except Exception as e:
            logger.error(f"[{self.name}] Erreur: {e}")

        return publications

    def _parse_article(self, article) -> dict | None:
        """Parse un article de gouv.bj."""
        try:
            # Titre dans un h3 ou h2
            title_el = article.select_one("h3, h2, h4")
            if not title_el:
                return None
            title = self.clean_text(title_el.get_text())
            if not title or len(title) < 10:
                return None

            # Lien
            link_el = article.select_one("a[href]")
            href = link_el.get("href", "") if link_el else ""
            full_url = urljoin("https://www.gouv.bj", href) if href else ""

            # Catégorie (span dans le header de l'article)
            category_el = article.select_one("span.upper")
            category = self.clean_text(category_el.get_text()) if category_el else "marché"

            # Date
            date_el = article.select_one(".date, time, span.date")
            date_text = self.clean_text(date_el.get_text()) if date_el else ""

            # Résumé (paragraphe dans l'article)
            summary_el = article.select_one("p:not(.upper)")
            summary = self.clean_text(summary_el.get_text())[:300] if summary_el else ""

            # PDF
            pdf_link = article.select_one("a[href$='.pdf']")
            pdf_url = urljoin("https://www.gouv.bj", pdf_link["href"]) if pdf_link else None

            reference = self.generate_reference(self.source, title, full_url)

            return {
                "source": self.source,
                "reference": reference,
                "title": title[:500],
                "summary": summary,
                "budget": None,
                "deadline": None,
                "pdf_url": pdf_url,
                "html_content": "",
                "category": category.lower() if category else "marché",
                "sectors": [],
                "regions": ["Bénin"],
                "published_date": date_text,
                "authority_email": None,
                "authority_name": "Gouvernement du Bénin",
            }
        except Exception as e:
            logger.warning(f"[{self.name}] Erreur parsing article: {e}")
            return None

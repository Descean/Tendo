"""Scraper pour ABE Bénin (Agence Béninoise de l'Environnement) – Appels d'offres."""

from typing import List
from urllib.parse import urljoin

from app.services.scraping.base import BaseScraper
from app.utils.logger import logger


class ABEScraper(BaseScraper):
    name = "ABE"
    base_url = "https://www.abe.bj/appels-doffres/"
    source = "ABE"

    def scrape(self) -> List[dict]:
        publications = []

        try:
            soup = self.fetch_page(self.base_url)
            items = soup.select(
                "article, .post, .entry, .card, "
                ".appel-item, .list-group-item"
            )

            for item in items[:20]:
                pub = self._parse_item(item)
                if pub:
                    publications.append(pub)

        except Exception as e:
            logger.error(f"[{self.name}] Erreur: {e}")

        return publications

    def _parse_item(self, item) -> dict | None:
        try:
            link_el = item.select_one("a[href]")
            title_el = item.select_one("h2, h3, h4, .title, .entry-title")

            title = self.clean_text(title_el.get_text()) if title_el else ""
            if not title and link_el:
                title = self.clean_text(link_el.get_text())
            if not title:
                return None

            href = link_el.get("href", "") if link_el else ""
            full_url = urljoin(self.base_url, href) if href else ""

            date_el = item.select_one("time, .date, .entry-date")
            date_text = self.clean_text(date_el.get_text()) if date_el else ""

            summary_el = item.select_one("p, .excerpt, .entry-content")
            summary = self.clean_text(summary_el.get_text())[:300] if summary_el else ""

            pdf_link = item.select_one("a[href$='.pdf']")
            pdf_url = urljoin(self.base_url, pdf_link["href"]) if pdf_link else None

            reference = self.generate_reference(self.source, title, full_url)

            return {
                "source": self.source,
                "reference": reference,
                "title": title,
                "summary": summary,
                "budget": None,
                "deadline": None,
                "pdf_url": pdf_url,
                "html_content": "",
                "category": "marché",
                "sectors": ["Environnement"],
                "regions": ["Bénin"],
                "published_date": date_text,
                "authority_email": None,
                "authority_name": "ABE Bénin",
            }
        except Exception as e:
            logger.warning(f"[{self.name}] Erreur parsing: {e}")
            return None

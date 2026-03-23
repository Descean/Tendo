"""Scraper pour ADPME Bénin – Appels d'offres PME."""

from typing import List
from urllib.parse import urljoin

from app.services.scraping.base import BaseScraper
from app.utils.logger import logger


class ADPMEScraper(BaseScraper):
    name = "ADPME"
    base_url = "https://epme.adpme.bj/category/ao/"
    source = "ADPME"

    def scrape(self) -> List[dict]:
        publications = []

        try:
            soup = self.fetch_page(self.base_url)

            # WordPress-style articles
            items = soup.select(
                "article, .post, .entry, .type-post, "
                ".category-ao .post-item"
            )

            for item in items[:20]:
                pub = self._parse_item(item)
                if pub:
                    publications.append(pub)

            # Pagination
            next_page = soup.select_one("a.next.page-numbers, .nav-next a")
            page = 2
            while next_page and page <= 3:
                try:
                    soup = self.fetch_page(urljoin(self.base_url, next_page["href"]))
                    items = soup.select("article, .post, .entry")
                    for item in items:
                        pub = self._parse_item(item)
                        if pub:
                            publications.append(pub)
                    next_page = soup.select_one("a.next.page-numbers, .nav-next a")
                    page += 1
                except Exception:
                    break

        except Exception as e:
            logger.error(f"[{self.name}] Erreur: {e}")

        return publications

    def _parse_item(self, item) -> dict | None:
        try:
            title_el = item.select_one("h2 a, h3 a, .entry-title a")
            if not title_el:
                return None

            title = self.clean_text(title_el.get_text())
            href = title_el.get("href", "")
            if not title:
                return None

            date_el = item.select_one("time, .entry-date, .posted-on")
            date_text = self.clean_text(date_el.get_text()) if date_el else ""

            excerpt_el = item.select_one(".entry-content, .excerpt, .entry-summary p")
            summary = self.clean_text(excerpt_el.get_text())[:300] if excerpt_el else ""

            reference = self.generate_reference(self.source, title, href)

            return {
                "source": self.source,
                "reference": reference,
                "title": title,
                "summary": summary,
                "budget": None,
                "deadline": None,
                "pdf_url": None,
                "html_content": "",
                "category": "appel à projets",
                "sectors": ["PME"],
                "regions": ["Bénin"],
                "published_date": date_text,
                "authority_email": None,
                "authority_name": "ADPME Bénin",
            }
        except Exception as e:
            logger.warning(f"[{self.name}] Erreur parsing: {e}")
            return None

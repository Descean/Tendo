"""Scraper générique – template pour ajouter de nouvelles sources facilement."""

from typing import List, Optional
from urllib.parse import urljoin

from app.services.scraping.base import BaseScraper
from app.utils.logger import logger


class GenericScraper(BaseScraper):
    """Scraper configurable pour toute source web d'appels d'offres.

    Utilisation :
        scraper = GenericScraper(
            name="BAD",
            base_url="https://eprocurement.afdb.org/appels",
            source="BAD",
            list_selector="table tbody tr",
            title_selector="td:first-child a",
            date_selector="td:nth-child(2)",
            link_selector="td:first-child a",
            summary_selector="td:nth-child(3)",
            default_category="financement",
            default_regions=["Afrique de l'Ouest"],
        )
        publications = scraper.run()
    """

    def __init__(
        self,
        name: str,
        base_url: str,
        source: str,
        list_selector: str = "article, .card, tr",
        title_selector: str = "h2 a, h3 a, .title a",
        date_selector: str = "time, .date",
        link_selector: str = "a[href]",
        summary_selector: str = "p, .excerpt",
        pdf_selector: str = "a[href$='.pdf']",
        pagination_selector: Optional[str] = "a.next, a[rel='next']",
        max_pages: int = 3,
        default_category: str = "marché",
        default_sectors: Optional[List[str]] = None,
        default_regions: Optional[List[str]] = None,
    ):
        super().__init__()
        self.name = name
        self.base_url = base_url
        self.source = source
        self.list_selector = list_selector
        self.title_selector = title_selector
        self.date_selector = date_selector
        self.link_selector = link_selector
        self.summary_selector = summary_selector
        self.pdf_selector = pdf_selector
        self.pagination_selector = pagination_selector
        self.max_pages = max_pages
        self.default_category = default_category
        self.default_sectors = default_sectors or []
        self.default_regions = default_regions or []

    def scrape(self) -> List[dict]:
        publications = []
        current_url = self.base_url
        page = 1

        while current_url and page <= self.max_pages:
            try:
                soup = self.fetch_page(current_url)
                items = soup.select(self.list_selector)

                for item in items:
                    pub = self._parse_item(item)
                    if pub:
                        publications.append(pub)

                # Pagination
                if self.pagination_selector:
                    next_link = soup.select_one(self.pagination_selector)
                    current_url = urljoin(self.base_url, next_link["href"]) if next_link else None
                else:
                    current_url = None

                page += 1

            except Exception as e:
                logger.error(f"[{self.name}] Erreur page {page}: {e}")
                break

        return publications

    def _parse_item(self, item) -> dict | None:
        try:
            # Titre
            title_el = item.select_one(self.title_selector)
            if not title_el:
                return None
            title = self.clean_text(title_el.get_text())
            if not title:
                return None

            # Lien
            link_el = item.select_one(self.link_selector)
            href = link_el.get("href", "") if link_el else ""
            full_url = urljoin(self.base_url, href) if href else ""

            # Date
            date_el = item.select_one(self.date_selector)
            date_text = self.clean_text(date_el.get_text()) if date_el else ""

            # Résumé
            summary_el = item.select_one(self.summary_selector)
            summary = self.clean_text(summary_el.get_text())[:300] if summary_el else ""

            # PDF
            pdf_el = item.select_one(self.pdf_selector)
            pdf_url = urljoin(self.base_url, pdf_el["href"]) if pdf_el else None

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
                "category": self.default_category,
                "sectors": self.default_sectors,
                "regions": self.default_regions,
                "published_date": date_text,
                "authority_email": None,
                "authority_name": None,
            }
        except Exception as e:
            logger.warning(f"[{self.name}] Erreur parsing: {e}")
            return None

"""Scraper UNFPA Benin – Fonds des Nations Unies pour la Population."""

from typing import List
from datetime import datetime

from app.services.scraping.base import BaseScraper
from app.utils.logger import logger


class UNFPAScraper(BaseScraper):
    """Scraper pour les appels a soumission de l'UNFPA Benin."""

    name = "UNFPA"
    source = "UNFPA Benin"
    base_url = "https://benin.unfpa.org/fr/call-for-submissions"

    def scrape(self) -> List[dict]:
        publications = []

        try:
            soup = self.fetch_page(self.base_url)

            # Les articles sont dans des blocs .views-row ou .node--type-*
            articles = soup.select(".views-row, .node--type-call-for-submission, article")

            for article in articles[:30]:
                # Titre
                title_tag = article.select_one("h2 a, h3 a, .field--name-title a, a.node__title")
                if not title_tag:
                    title_tag = article.select_one("h2, h3, .title")
                if not title_tag:
                    continue

                title = self.clean_text(title_tag.get_text())
                if not title:
                    continue

                # Lien
                href = title_tag.get("href", "")
                if href and not href.startswith("http"):
                    href = f"https://benin.unfpa.org{href}"

                reference = self.generate_reference("UNFPA", title, href)

                # Date
                date_tag = article.select_one(
                    ".field--name-field-date, .date, time, .datetime"
                )
                pub_date = None
                if date_tag:
                    date_text = date_tag.get("datetime") or date_tag.get_text()
                    pub_date = self._parse_date(date_text)

                # Resume
                summary_tag = article.select_one(
                    ".field--name-body, .summary, .field--name-field-summary, p"
                )
                summary = self.clean_text(summary_tag.get_text()) if summary_tag else ""

                publications.append({
                    "source": self.source,
                    "reference": reference,
                    "title": title[:500],
                    "summary": summary[:1000],
                    "budget": None,
                    "deadline": None,
                    "pdf_url": href,
                    "html_content": "",
                    "category": "marche",
                    "sectors": self._detect_sectors(title + " " + summary),
                    "regions": ["Benin"],
                    "published_date": pub_date or self.now_utc(),
                    "authority_name": "UNFPA Benin",
                    "authority_email": None,
                    "document_type": "AAO",
                    "financing_source": "UNFPA/ONU",
                    "country": "Benin",
                })

        except Exception as e:
            logger.error(f"[{self.name}] Erreur scraping: {e}")

        return publications

    def _parse_date(self, date_str: str):
        if not date_str:
            return None
        formats = ["%Y-%m-%d", "%d/%m/%Y", "%d %B %Y", "%Y-%m-%dT%H:%M:%S"]
        for fmt in formats:
            try:
                return datetime.strptime(date_str.strip()[:10], fmt[:len(date_str.strip()[:10])])
            except (ValueError, TypeError):
                continue
        return None

    def _detect_sectors(self, text: str) -> list:
        text_lower = text.lower()
        sectors = []
        if any(w in text_lower for w in ("sante", "medical", "reproductive", "VIH")):
            sectors.append("Sante")
        if any(w in text_lower for w in ("education", "formation", "jeune")):
            sectors.append("Education")
        if any(w in text_lower for w in ("fourniture", "equipement", "materiel")):
            sectors.append("Fournitures")
        if any(w in text_lower for w in ("consultant", "etude", "evaluation")):
            sectors.append("Services")
        return sectors or ["Sante"]

"""Scraper BAD -- Banque Africaine de Developpement.

Source : https://www.afdb.org/en/documents/procurement-notices
La BAD publie des avis de marches pour ses projets en Afrique.
On filtre par pays (Benin) et par region (Afrique de l'Ouest).
"""

from typing import List, Optional
from datetime import datetime

from app.services.scraping.base import BaseScraper
from app.utils.logger import logger


class BADScraper(BaseScraper):
    """Scraper pour la Banque Africaine de Developpement (BAD/AfDB)."""

    name = "BAD"
    base_url = "https://www.afdb.org/en/documents/procurement-notices"
    source = "BAD"

    # URL de l'API JSON utilisee par le site (plus fiable que le HTML)
    API_URL = "https://www.afdb.org/api/search/documents"

    def scrape(self) -> List[dict]:
        """Scrape les avis de marches de la BAD."""
        publications = []

        try:
            # Methode 1: API JSON (plus fiable)
            publications = self._scrape_via_api()
            if publications:
                return publications
        except Exception as e:
            logger.warning(f"[BAD] API indisponible, tentative HTML: {e}")

        try:
            # Methode 2: Scraping HTML
            publications = self._scrape_html()
        except Exception as e:
            logger.error(f"[BAD] Erreur scraping HTML: {e}")

        return publications

    def _scrape_via_api(self) -> List[dict]:
        """Scrape via l'API JSON de la BAD."""
        params = {
            "type": "procurement-notices",
            "country": "benin",
            "limit": 20,
            "sort": "date",
            "order": "desc",
        }

        response = self.session.get(self.API_URL, params=params, timeout=30)
        if response.status_code != 200:
            return []

        data = response.json()
        results = data.get("results", data.get("documents", []))

        publications = []
        for item in results:
            title = item.get("title", "").strip()
            if not title:
                continue

            url = item.get("url", "")
            if url and not url.startswith("http"):
                url = f"https://www.afdb.org{url}"

            pub_date = item.get("date", item.get("published_date"))
            deadline = item.get("deadline", item.get("closing_date"))

            publications.append({
                "source": self.source,
                "reference": self.generate_reference("BAD", title, url),
                "title": self.clean_text(title),
                "summary": self.clean_text(item.get("description", item.get("summary", ""))),
                "published_date": self._parse_date(pub_date),
                "deadline": self._parse_date(deadline),
                "pdf_url": item.get("file_url", item.get("pdf_url")) or None,
                "html_content": item.get("description", ""),
                "category": "marche",
                "sectors": self._detect_sectors(title),
                "regions": ["Benin"],
                "authority_name": "Banque Africaine de Developpement (BAD)",
                "authority_email": None,
            })

        return publications

    def _scrape_html(self) -> List[dict]:
        """Scrape la page HTML des avis de marches BAD."""
        soup = self.fetch_page(self.base_url)
        publications = []

        # Chercher les elements de liste d'avis
        items = soup.select(".views-row, .document-item, .procurement-item, article.node")

        for item in items[:20]:
            title_el = item.select_one("h3 a, h2 a, .title a, .field--name-title a")
            if not title_el:
                continue

            title = self.clean_text(title_el.get_text())
            url = title_el.get("href", "")
            if url and not url.startswith("http"):
                url = f"https://www.afdb.org{url}"

            date_el = item.select_one(".date, .field--name-field-date, time")
            pub_date = self._parse_date(date_el.get_text().strip() if date_el else None)

            summary_el = item.select_one(".field--name-body, .summary, .description, p")
            summary = self.clean_text(summary_el.get_text()) if summary_el else ""

            pdf_el = item.select_one("a[href$='.pdf']")
            pdf_url = pdf_el.get("href") if pdf_el else None
            if pdf_url and not pdf_url.startswith("http"):
                pdf_url = f"https://www.afdb.org{pdf_url}"

            publications.append({
                "source": self.source,
                "reference": self.generate_reference("BAD", title, url),
                "title": title,
                "summary": summary[:500],
                "published_date": pub_date,
                "deadline": None,
                "pdf_url": pdf_url,
                "html_content": summary,
                "category": "marche",
                "sectors": self._detect_sectors(title),
                "regions": ["Benin"],
                "authority_name": "Banque Africaine de Developpement (BAD)",
                "authority_email": None,
            })

        return publications

    def _parse_date(self, date_str: Optional[str]) -> Optional[datetime]:
        """Parse une date dans differents formats."""
        if not date_str:
            return None

        formats = [
            "%Y-%m-%d",
            "%d/%m/%Y",
            "%d %B %Y",
            "%B %d, %Y",
            "%Y-%m-%dT%H:%M:%S",
            "%d-%m-%Y",
        ]

        for fmt in formats:
            try:
                return datetime.strptime(date_str.strip(), fmt)
            except (ValueError, AttributeError):
                continue

        return None

    def _detect_sectors(self, title: str) -> list:
        """Detecte les secteurs a partir du titre."""
        title_lower = title.lower()
        sectors = []

        sector_keywords = {
            "BTP": ["construction", "route", "batiment", "infrastructure", "pont", "barrage"],
            "Fournitures": ["fourniture", "equipement", "materiel", "vehicule", "mobilier"],
            "Services": ["service", "conseil", "consultant", "etude", "audit", "formation"],
            "TIC": ["informatique", "logiciel", "systeme", "numerique", "reseau", "telecom"],
            "Sante": ["sante", "medical", "hopital", "medicament", "pharmaceutique"],
            "Education": ["education", "ecole", "formation", "universitaire", "scolaire"],
            "Agriculture": ["agricole", "agriculture", "irrigation", "rural", "elevage"],
            "Environnement": ["environnement", "eau", "assainissement", "dechet", "energie solaire"],
            "Transport": ["transport", "routier", "aerien", "maritime", "ferroviaire"],
            "Energie": ["energie", "electrique", "electricite", "solaire", "petrole", "gaz"],
        }

        for sector, keywords in sector_keywords.items():
            if any(kw in title_lower for kw in keywords):
                sectors.append(sector)

        return sectors or ["Services"]

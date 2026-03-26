"""Scraper AFD -- Agence Francaise de Developpement.

Source : https://afd.dgmarket.com/
L'AFD publie ses marches via DgMarket. On filtre par Benin et Afrique de l'Ouest.
"""

from typing import List, Optional
from datetime import datetime

from app.services.scraping.base import BaseScraper
from app.utils.logger import logger


class AFDScraper(BaseScraper):
    """Scraper pour l'Agence Francaise de Developpement (AFD)."""

    name = "AFD"
    base_url = "https://afd.dgmarket.com/tenders/np-notice-list"
    source = "AFD"

    def scrape(self) -> List[dict]:
        """Scrape les avis de marches finances par l'AFD."""
        publications = []

        try:
            publications = self._scrape_dgmarket()
        except Exception as e:
            logger.error(f"[AFD] Erreur scraping DgMarket: {e}")

        if not publications:
            try:
                publications = self._scrape_afd_direct()
            except Exception as e:
                logger.error(f"[AFD] Erreur scraping AFD direct: {e}")

        return publications

    def _scrape_dgmarket(self) -> List[dict]:
        """Scrape DgMarket (portail AFD pour les marches)."""
        params = {
            "loc": "BJ",  # Benin
            "language": "fr",
            "order_by": "notice_date",
            "order": "desc",
        }

        soup = self.fetch_page(self.base_url, params=params)
        publications = []

        # DgMarket utilise des tableaux ou des listes d'avis
        rows = soup.select("table.list tr, .tender-item, .notice-row, .search-result")

        for row in rows[:20]:
            # Chercher le lien du titre
            title_el = row.select_one("a.title, td a, h3 a, .tender-title a")
            if not title_el:
                continue

            title = self.clean_text(title_el.get_text())
            if not title or len(title) < 10:
                continue

            url = title_el.get("href", "")
            if url and not url.startswith("http"):
                url = f"https://afd.dgmarket.com{url}"

            # Chercher la date
            date_el = row.select_one(".date, td.date, .notice-date")
            pub_date = self._parse_date(date_el.get_text().strip() if date_el else None)

            # Chercher la deadline
            deadline_el = row.select_one(".deadline, td.deadline, .closing-date")
            deadline = self._parse_date(deadline_el.get_text().strip() if deadline_el else None)

            # Chercher le pays/region
            country_el = row.select_one(".country, td.country, .location")
            country = self.clean_text(country_el.get_text()) if country_el else "Benin"

            # Chercher le secteur
            sector_el = row.select_one(".sector, td.sector, .category")
            sector_text = self.clean_text(sector_el.get_text()) if sector_el else ""

            publications.append({
                "source": self.source,
                "reference": self.generate_reference("AFD", title, url),
                "title": title,
                "summary": f"Marche finance par l'AFD - {country}. {sector_text}".strip(),
                "published_date": pub_date,
                "deadline": deadline,
                "pdf_url": None,
                "html_content": "",
                "category": "marche",
                "sectors": self._detect_sectors(title + " " + sector_text),
                "regions": self._detect_regions(country),
                "authority_name": "Agence Francaise de Developpement (AFD)",
                "authority_email": None,
            })

        return publications

    def _scrape_afd_direct(self) -> List[dict]:
        """Scrape directement le site AFD pour les appels a projets."""
        afd_url = "https://www.afd.fr/fr/page-thematique-afd/appels-a-projets"
        publications = []

        try:
            soup = self.fetch_page(afd_url)

            items = soup.select("article, .node--type-call-for-proposals, .views-row")

            for item in items[:15]:
                title_el = item.select_one("h2 a, h3 a, .field--name-title a")
                if not title_el:
                    continue

                title = self.clean_text(title_el.get_text())
                url = title_el.get("href", "")
                if url and not url.startswith("http"):
                    url = f"https://www.afd.fr{url}"

                summary_el = item.select_one(".field--name-body, .summary, p")
                summary = self.clean_text(summary_el.get_text())[:500] if summary_el else ""

                date_el = item.select_one("time, .date, .field--name-field-date")
                pub_date = None
                if date_el:
                    date_attr = date_el.get("datetime", date_el.get_text().strip())
                    pub_date = self._parse_date(date_attr)

                publications.append({
                    "source": self.source,
                    "reference": self.generate_reference("AFD", title, url),
                    "title": title,
                    "summary": summary,
                    "published_date": pub_date,
                    "deadline": None,
                    "pdf_url": None,
                    "html_content": summary,
                    "category": "appel a projets",
                    "sectors": self._detect_sectors(title),
                    "regions": ["Benin", "CEDEAO"],
                    "authority_name": "Agence Francaise de Developpement (AFD)",
                    "authority_email": None,
                })

        except Exception as e:
            logger.error(f"[AFD] Erreur scraping direct: {e}")

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
            "%Y-%m-%dT%H:%M:%SZ",
            "%d-%m-%Y",
            "%d %b %Y",
        ]

        for fmt in formats:
            try:
                return datetime.strptime(date_str.strip()[:19], fmt)
            except (ValueError, AttributeError):
                continue

        return None

    def _detect_sectors(self, text: str) -> list:
        """Detecte les secteurs a partir du texte."""
        text_lower = text.lower()
        sectors = []

        sector_keywords = {
            "BTP": ["construction", "route", "batiment", "infrastructure", "rehabilitation"],
            "Fournitures": ["fourniture", "equipement", "materiel", "vehicule"],
            "Services": ["service", "conseil", "consultant", "etude", "audit", "assistance"],
            "TIC": ["informatique", "logiciel", "numerique", "digital"],
            "Sante": ["sante", "medical", "hopital", "medicament"],
            "Education": ["education", "ecole", "formation", "universitaire"],
            "Agriculture": ["agricole", "agriculture", "irrigation", "rural"],
            "Environnement": ["environnement", "eau", "assainissement", "climat"],
            "Transport": ["transport", "routier", "mobilite"],
            "Energie": ["energie", "electrique", "solaire", "renouvelable"],
        }

        for sector, keywords in sector_keywords.items():
            if any(kw in text_lower for kw in keywords):
                sectors.append(sector)

        return sectors or ["Services"]

    def _detect_regions(self, country_text: str) -> list:
        """Detecte les regions a partir du texte pays."""
        text_lower = country_text.lower()
        if "benin" in text_lower or "bénin" in text_lower:
            return ["Benin"]
        if any(w in text_lower for w in ("ouest", "cedeao", "ecowas", "uemoa")):
            return ["CEDEAO"]
        return ["CEDEAO"]

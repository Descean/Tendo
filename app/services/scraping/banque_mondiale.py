"""Scraper Banque Mondiale – projets et opportunites de procurement."""

from typing import List
from app.services.scraping.base import BaseScraper
from app.utils.logger import logger


class BanqueMondialesScraper(BaseScraper):
    """Scraper pour les appels d'offres finances par la Banque Mondiale."""

    name = "BanqueMondiale"
    source = "Banque Mondiale"
    base_url = "https://projects.banquemondiale.org/fr/projects-operations/opportunities"

    # API endpoint (plus fiable que le scraping HTML)
    API_URL = "https://search.worldbank.org/api/v2/procnotices"

    def scrape(self) -> List[dict]:
        publications = []

        try:
            # Utiliser l'API de recherche de la Banque Mondiale
            params = {
                "format": "json",
                "countrycode_exact": "BJ",  # Benin
                "rows": 50,
                "os": 0,
                "srt": "new",  # Tri par date
            }
            response = self.session.get(self.API_URL, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()

            notices = data.get("procnotices", {})
            for key, notice in notices.items():
                if not isinstance(notice, dict):
                    continue

                title = notice.get("notice_text", notice.get("project_name", ""))
                if not title:
                    continue

                ref = notice.get("noticenum", "") or notice.get("id", "")
                reference = self.generate_reference("BM", title, ref)

                deadline_str = notice.get("deadline_date", "")
                deadline = None
                if deadline_str:
                    try:
                        from datetime import datetime
                        deadline = datetime.strptime(deadline_str[:10], "%Y-%m-%d")
                    except (ValueError, TypeError):
                        pass

                pub_date_str = notice.get("notice_postingdate", "")
                pub_date = None
                if pub_date_str:
                    try:
                        from datetime import datetime
                        pub_date = datetime.strptime(pub_date_str[:10], "%Y-%m-%d")
                    except (ValueError, TypeError):
                        pass

                publications.append({
                    "source": self.source,
                    "reference": reference,
                    "title": self.clean_text(title)[:500],
                    "summary": self.clean_text(notice.get("notice_text", ""))[:1000],
                    "budget": None,
                    "deadline": deadline,
                    "pdf_url": notice.get("notice_url", ""),
                    "html_content": "",
                    "category": "marche",
                    "sectors": self._detect_sectors(title),
                    "regions": ["Benin"],
                    "published_date": pub_date,
                    "authority_name": notice.get("borrower", ""),
                    "authority_email": None,
                    "document_type": self._detect_doc_type(notice),
                    "financing_source": "Banque Mondiale",
                    "country": "Benin",
                })

        except Exception as e:
            logger.error(f"[{self.name}] Erreur API: {e}")
            # Fallback : scraping HTML
            publications.extend(self._scrape_html())

        return publications

    def _scrape_html(self) -> List[dict]:
        """Fallback scraping HTML si l'API ne repond pas."""
        try:
            soup = self.fetch_page(self.base_url)
            # Parser les resultats de la page
            # (Structure a adapter selon le site)
            return []
        except Exception as e:
            logger.error(f"[{self.name}] Erreur scraping HTML: {e}")
            return []

    def _detect_sectors(self, title: str) -> list:
        title_lower = title.lower()
        sectors = []
        sector_map = {
            "BTP": ["construction", "travaux", "route", "batiment", "infrastructure"],
            "Fournitures": ["fourniture", "equipement", "materiel"],
            "Services": ["consultant", "service", "etude", "assistance"],
            "TIC": ["informatique", "numerique", "logiciel"],
            "Sante": ["sante", "medical", "hopital"],
            "Education": ["education", "formation", "ecole"],
            "Agriculture": ["agriculture", "irrigation", "rural"],
            "Energie": ["energie", "electrique", "solaire"],
        }
        for sector, keywords in sector_map.items():
            if any(kw in title_lower for kw in keywords):
                sectors.append(sector)
        return sectors or ["Autres"]

    def _detect_doc_type(self, notice: dict) -> str:
        notice_type = notice.get("notice_type", "").lower()
        if "award" in notice_type:
            return "PV_ATTRIBUTION"
        if "expression" in notice_type or "eoi" in notice_type:
            return "AMI"
        if "prequalification" in notice_type:
            return "AMI"
        return "AAO"

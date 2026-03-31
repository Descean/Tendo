"""Scraper UNGM – United Nations Global Marketplace."""

from typing import List
from datetime import datetime

from app.services.scraping.base import BaseScraper
from app.utils.logger import logger


class UNGMScraper(BaseScraper):
    """Scraper pour les appels d'offres du systeme ONU (UNGM)."""

    name = "UNGM"
    source = "UNGM"
    base_url = "https://www.ungm.org/Public/Notice"

    def scrape(self) -> List[dict]:
        publications = []

        try:
            # UNGM a une API de recherche
            search_url = "https://www.ungm.org/Public/Notice/Search"
            payload = {
                "PageIndex": 0,
                "PageSize": 30,
                "SortField": "DatePublished",
                "SortAscending": False,
                "isPrev498Ede": False,
                "Description": "",
                "Title": "",
                "DeadlineFrom": "",
                "PublishedFrom": "",
                "Countries": ["BEN"],  # Benin
            }

            response = self.session.post(search_url, json=payload, timeout=30)
            if response.status_code == 200:
                data = response.json()
                for notice in data.get("Results", []):
                    title = notice.get("Title", "")
                    if not title:
                        continue

                    ref_id = notice.get("Reference", str(notice.get("Id", "")))
                    reference = self.generate_reference("UNGM", title, ref_id)

                    deadline = None
                    deadline_str = notice.get("Deadline", "")
                    if deadline_str:
                        try:
                            deadline = datetime.fromisoformat(deadline_str.replace("Z", "+00:00"))
                        except (ValueError, TypeError):
                            pass

                    pub_date = None
                    pub_date_str = notice.get("PublishedDate", "")
                    if pub_date_str:
                        try:
                            pub_date = datetime.fromisoformat(pub_date_str.replace("Z", "+00:00"))
                        except (ValueError, TypeError):
                            pass

                    publications.append({
                        "source": self.source,
                        "reference": reference,
                        "title": self.clean_text(title)[:500],
                        "summary": self.clean_text(notice.get("Description", ""))[:1000],
                        "budget": None,
                        "deadline": deadline,
                        "pdf_url": f"https://www.ungm.org/Public/Notice/{notice.get('Id', '')}",
                        "html_content": "",
                        "category": "marche",
                        "sectors": self._detect_sectors(title),
                        "regions": ["Benin"],
                        "published_date": pub_date,
                        "authority_name": notice.get("AgencyName", "Nations Unies"),
                        "authority_email": None,
                        "document_type": self._detect_type(notice),
                        "financing_source": "ONU",
                        "country": "Benin",
                    })
            else:
                # Fallback scraping HTML
                publications.extend(self._scrape_html())

        except Exception as e:
            logger.error(f"[{self.name}] Erreur: {e}")
            publications.extend(self._scrape_html())

        return publications

    def _scrape_html(self) -> List[dict]:
        """Fallback scraping HTML."""
        try:
            soup = self.fetch_page(self.base_url)
            rows = soup.select("table.table tbody tr")
            pubs = []

            for row in rows[:30]:
                cols = row.select("td")
                if len(cols) < 4:
                    continue

                link_tag = row.select_one("a")
                title = self.clean_text(link_tag.get_text()) if link_tag else ""
                if not title:
                    continue

                href = link_tag.get("href", "") if link_tag else ""
                url = f"https://www.ungm.org{href}" if href.startswith("/") else href

                reference = self.generate_reference("UNGM", title, url)

                pubs.append({
                    "source": self.source,
                    "reference": reference,
                    "title": title[:500],
                    "summary": "",
                    "budget": None,
                    "deadline": None,
                    "pdf_url": url,
                    "html_content": "",
                    "category": "marche",
                    "sectors": self._detect_sectors(title),
                    "regions": ["Benin"],
                    "published_date": self.now_utc(),
                    "authority_name": "Nations Unies",
                    "authority_email": None,
                    "document_type": "AAO",
                    "financing_source": "ONU",
                    "country": "Benin",
                })

            return pubs
        except Exception as e:
            logger.error(f"[{self.name}] Erreur HTML fallback: {e}")
            return []

    def _detect_sectors(self, title: str) -> list:
        title_lower = title.lower()
        sectors = []
        if any(w in title_lower for w in ("construct", "works", "building", "road")):
            sectors.append("BTP")
        if any(w in title_lower for w in ("supply", "equipment", "goods", "fourniture")):
            sectors.append("Fournitures")
        if any(w in title_lower for w in ("consult", "service", "study", "technical")):
            sectors.append("Services")
        if any(w in title_lower for w in ("health", "medical", "vaccine", "sante")):
            sectors.append("Sante")
        if any(w in title_lower for w in ("education", "training", "school")):
            sectors.append("Education")
        return sectors or ["Autres"]

    def _detect_type(self, notice: dict) -> str:
        notice_type = (notice.get("NoticeType", "") or "").lower()
        if "rfq" in notice_type:
            return "RFQ"
        if "rfp" in notice_type or "proposal" in notice_type:
            return "RFP"
        if "eoi" in notice_type or "expression" in notice_type:
            return "AMI"
        return "AAO"

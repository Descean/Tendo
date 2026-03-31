"""Scraper CARE Benin/Togo – ONG internationale, appels d'offres et consultations."""

from typing import List

from app.services.scraping.base import BaseScraper
from app.utils.logger import logger


class CAREBeninScraper(BaseScraper):
    """Scraper pour CARE International Benin/Togo – opportunites d'affaires."""

    name = "CAREBenin"
    source = "CARE Benin"
    base_url = "https://www.care.org.bj"

    # URLs alternatives a essayer
    ALT_URLS = [
        "https://www.care.org.bj/appels-doffres",
        "https://www.care.org.bj/opportunites",
        "https://www.care.org.bj/category/appels-doffres",
        "https://www.care.org.bj/procurement",
    ]

    def scrape(self) -> List[dict]:
        publications = []

        # Essayer l'URL principale puis les alternatives
        urls_to_try = [self.base_url] + self.ALT_URLS

        for url in urls_to_try:
            try:
                soup = self.fetch_page(url)
                pubs = self._extract_from_page(soup, url)
                if pubs:
                    publications.extend(pubs)
                    break  # URL trouvee, pas besoin d'essayer les autres
            except Exception as e:
                logger.debug(f"[{self.name}] URL {url} indisponible: {e}")
                continue

        if not publications:
            logger.warning(f"[{self.name}] Aucune URL accessible, scraping generique")
            publications = self._scrape_generic()

        return publications

    def _extract_from_page(self, soup, base_url: str) -> List[dict]:
        """Extrait les publications d'une page HTML."""
        publications = []

        # Chercher les articles/posts (WordPress ou autre CMS)
        items = soup.select(
            "article, .post, .entry, .card, .views-row, "
            ".tender-item, .ao-item, .opportunity-item"
        )

        if not items:
            # Essayer les liens directs
            items = soup.select(
                "a[href*='appel'], a[href*='offre'], a[href*='consultation'], "
                "a[href*='tender'], a[href*='procurement']"
            )

        for item in items[:30]:
            if item.name == "a":
                link = item
                title = self.clean_text(link.get_text())
            else:
                link = item.select_one("h2 a, h3 a, h4 a, .title a, a")
                title_el = item.select_one("h2, h3, h4, .title, .entry-title")
                title = self.clean_text(title_el.get_text()) if title_el else ""
                if not title and link:
                    title = self.clean_text(link.get_text())

            if not title or len(title) < 15:
                continue

            href = link.get("href", "") if link else ""
            if href and not href.startswith("http"):
                href = f"{base_url.rstrip('/')}/{href.lstrip('/')}"

            # Chercher la date
            date_el = item.select_one("time, .date, .entry-date") if item.name != "a" else None
            date_text = self.clean_text(date_el.get_text()) if date_el else ""

            # Chercher le resume
            summary_el = item.select_one("p, .excerpt, .summary") if item.name != "a" else None
            summary = self.clean_text(summary_el.get_text())[:300] if summary_el else ""

            # Chercher PDF
            pdf_el = item.select_one("a[href$='.pdf']") if item.name != "a" else None
            pdf_url = pdf_el.get("href") if pdf_el else None

            reference = self.generate_reference("CARE", title, href)

            publications.append({
                "source": self.source,
                "reference": reference,
                "title": title[:500],
                "summary": summary,
                "budget": None,
                "deadline": None,
                "pdf_url": pdf_url or href,
                "html_content": "",
                "category": "marche",
                "sectors": self._detect_sectors(title),
                "regions": ["Benin"],
                "published_date": date_text or self.now_utc(),
                "authority_name": "CARE International Benin/Togo",
                "authority_email": None,
                "document_type": self._detect_doc_type(title),
                "financing_source": "CARE International",
                "country": "Benin",
            })

        return publications

    def _scrape_generic(self) -> List[dict]:
        """Scraping generique via Google-like search."""
        try:
            # Essayer le site principal pour trouver n'importe quel contenu
            soup = self.fetch_page(self.base_url)
            links = soup.select("a[href]")
            publications = []

            keywords = ("appel", "offre", "consultation", "recrutement", "tender", "procurement")

            for link in links:
                href = link.get("href", "")
                text = self.clean_text(link.get_text())
                if not text or len(text) < 15:
                    continue
                if not any(kw in text.lower() or kw in href.lower() for kw in keywords):
                    continue

                if not href.startswith("http"):
                    href = f"{self.base_url.rstrip('/')}/{href.lstrip('/')}"

                reference = self.generate_reference("CARE", text, href)
                publications.append({
                    "source": self.source,
                    "reference": reference,
                    "title": text[:500],
                    "summary": "",
                    "budget": None,
                    "deadline": None,
                    "pdf_url": href,
                    "html_content": "",
                    "category": "marche",
                    "sectors": self._detect_sectors(text),
                    "regions": ["Benin"],
                    "published_date": self.now_utc(),
                    "authority_name": "CARE International Benin/Togo",
                    "authority_email": None,
                    "document_type": "AAO",
                    "financing_source": "CARE International",
                    "country": "Benin",
                })

            return publications
        except Exception as e:
            logger.error(f"[{self.name}] Erreur scraping generique: {e}")
            return []

    def _detect_sectors(self, title: str) -> list:
        title_lower = title.lower()
        sectors = []
        if any(w in title_lower for w in ("sante", "medical", "nutrition")):
            sectors.append("Sante")
        if any(w in title_lower for w in ("education", "formation", "ecole")):
            sectors.append("Education")
        if any(w in title_lower for w in ("eau", "assainissement", "hygiene", "wash")):
            sectors.append("Eau et Assainissement")
        if any(w in title_lower for w in ("agriculture", "securite alimentaire", "rural")):
            sectors.append("Agriculture")
        if any(w in title_lower for w in ("construction", "travaux", "batiment")):
            sectors.append("BTP")
        if any(w in title_lower for w in ("fourniture", "equipement", "materiel")):
            sectors.append("Fournitures")
        if any(w in title_lower for w in ("service", "consultant", "etude", "audit")):
            sectors.append("Services")
        return sectors or ["Autres"]

    def _detect_doc_type(self, title: str) -> str:
        title_lower = title.lower()
        if any(w in title_lower for w in ("manifestation", "interet", "ami", "eoi")):
            return "AMI"
        if any(w in title_lower for w in ("cotation", "devis", "rfq")):
            return "RFQ"
        if any(w in title_lower for w in ("proposition", "rfp")):
            return "RFP"
        if any(w in title_lower for w in ("consultant", "recrutement")):
            return "AMI"
        return "AAO"

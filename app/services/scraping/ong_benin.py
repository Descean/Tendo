"""Scrapers pour organisations et agences au Benin – ASIN, GIZ, ENABEL, etc.

Regroupe plusieurs petites sources dans un seul fichier.
"""

from typing import List

from app.services.scraping.base import BaseScraper
from app.utils.logger import logger


class ASINScraper(BaseScraper):
    """Scraper pour l'ASIN (Agence du Service Informatique de l'Etat – Benin).

    Site : https://asin.bj — publie des appels d'offres en informatique,
    numerique et services IT pour l'Etat beninois.
    """

    name = "ASIN"
    source = "ASIN"
    base_url = "https://asin.bj/opportunites/appels-offres/"

    ALT_URLS = [
        "https://asin.bj/opportunites/appels-offres",
        "https://asin.bj/opportunites",
        "https://asin.bj/appels-doffres",
    ]

    def scrape(self) -> List[dict]:
        publications = []

        urls_to_try = [self.base_url] + self.ALT_URLS

        for url in urls_to_try:
            try:
                soup = self.fetch_page(url)
                pubs = self._extract_publications(soup, url)
                if pubs:
                    publications.extend(pubs)
                    break
            except Exception as e:
                logger.debug(f"[{self.name}] URL {url} indisponible: {e}")
                continue

        return publications

    def _extract_publications(self, soup, base_url: str) -> List[dict]:
        publications = []

        items = soup.select(
            "article, .post, .entry, .card, .views-row, "
            ".tender-item, .ao-item"
        )

        if not items:
            items = soup.select(
                "a[href*='appel'], a[href*='offre'], a[href*='tender']"
            )

        for item in items[:20]:
            if item.name == "a":
                link = item
                title = self.clean_text(link.get_text())
            else:
                link = item.select_one("h2 a, h3 a, .title a, a")
                title_el = item.select_one("h2, h3, .title, .entry-title")
                title = self.clean_text(title_el.get_text()) if title_el else ""
                if not title and link:
                    title = self.clean_text(link.get_text())

            if not title or len(title) < 15:
                continue

            href = link.get("href", "") if link else ""
            if href and not href.startswith("http"):
                href = f"{base_url.rstrip('/')}/{href.lstrip('/')}"

            reference = self.generate_reference("ASIN", title, href)

            publications.append({
                "source": self.source,
                "reference": reference,
                "title": title[:500],
                "summary": "",
                "budget": None,
                "deadline": None,
                "pdf_url": href,
                "html_content": "",
                "category": "marche",
                "sectors": self._detect_sectors(title),
                "regions": ["Benin"],
                "published_date": self.now_utc(),
                "authority_name": "ASIN (Agence du Service Informatique de l'Etat)",
                "authority_email": None,
                "document_type": "AAO",
                "financing_source": "Etat du Benin",
                "country": "Benin",
            })

        return publications

    def _detect_sectors(self, title: str) -> list:
        title_lower = title.lower()
        sectors = []
        if any(w in title_lower for w in ("informatique", "logiciel", "numerique", "digital", "reseau", "telecom", "systeme", "plateforme")):
            sectors.append("TIC")
        if any(w in title_lower for w in ("fourniture", "equipement", "materiel")):
            sectors.append("Fournitures")
        if any(w in title_lower for w in ("service", "consultant", "etude", "audit", "maintenance")):
            sectors.append("Services")
        if any(w in title_lower for w in ("travaux", "construction", "infrastructure")):
            sectors.append("BTP")
        return sectors or ["TIC"]


class GIZBeninScraper(BaseScraper):
    """Scraper pour GIZ Benin – Cooperation allemande."""

    name = "GIZ"
    source = "GIZ Benin"
    base_url = "https://www.giz.de/en/worldwide/328.html"

    def scrape(self) -> List[dict]:
        publications = []

        try:
            soup = self.fetch_page(self.base_url)

            items = soup.select(
                "article, .teaser, .content-item, .views-row, "
                "a[href*='tender'], a[href*='procurement']"
            )

            for item in items[:20]:
                if item.name == "a":
                    link = item
                    title = self.clean_text(link.get_text())
                else:
                    link = item.select_one("a[href]")
                    title_el = item.select_one("h2, h3, h4, .title")
                    title = self.clean_text(title_el.get_text()) if title_el else ""
                    if not title and link:
                        title = self.clean_text(link.get_text())

                if not title or len(title) < 15:
                    continue

                href = link.get("href", "") if link else ""
                if href and not href.startswith("http"):
                    href = f"https://www.giz.de{href}"

                # Filtrer les liens pertinents
                relevant_keywords = ("tender", "procurement", "appel", "offre", "consultation")
                if not any(kw in title.lower() or kw in href.lower() for kw in relevant_keywords):
                    continue

                reference = self.generate_reference("GIZ", title, href)

                publications.append({
                    "source": self.source,
                    "reference": reference,
                    "title": title[:500],
                    "summary": "",
                    "budget": None,
                    "deadline": None,
                    "pdf_url": href,
                    "html_content": "",
                    "category": "marche",
                    "sectors": self._detect_sectors(title),
                    "regions": ["Benin"],
                    "published_date": self.now_utc(),
                    "authority_name": "GIZ Benin",
                    "authority_email": None,
                    "document_type": "AAO",
                    "financing_source": "GIZ",
                    "country": "Benin",
                })

        except Exception as e:
            logger.warning(f"[{self.name}] Erreur: {e}")

        return publications

    def _detect_sectors(self, title: str) -> list:
        title_lower = title.lower()
        sectors = []
        if any(w in title_lower for w in ("energie", "solaire", "electrique", "renouvelable")):
            sectors.append("Energie")
        if any(w in title_lower for w in ("agriculture", "rural", "decentralisation")):
            sectors.append("Agriculture")
        if any(w in title_lower for w in ("formation", "education", "emploi")):
            sectors.append("Education")
        if any(w in title_lower for w in ("sante", "social")):
            sectors.append("Sante")
        if any(w in title_lower for w in ("service", "consultant", "etude")):
            sectors.append("Services")
        return sectors or ["Services"]


class ENABELBeninScraper(BaseScraper):
    """Scraper pour ENABEL (Cooperation belge) au Benin."""

    name = "ENABEL"
    source = "ENABEL Benin"
    base_url = "https://www.enabel.be/fr/content/benin"

    PROCUREMENT_URL = "https://www.enabel.be/fr/marches-publics"

    def scrape(self) -> List[dict]:
        publications = []

        try:
            soup = self.fetch_page(self.PROCUREMENT_URL)

            items = soup.select(
                "article, .views-row, .node, .tender-item, "
                "table tbody tr, .card"
            )

            for item in items[:30]:
                if item.name == "tr":
                    cells = item.select("td")
                    if len(cells) < 2:
                        continue
                    link = cells[0].select_one("a") or cells[1].select_one("a")
                    title = self.clean_text(cells[0].get_text() or cells[1].get_text())
                else:
                    link = item.select_one("h2 a, h3 a, .title a, a")
                    title_el = item.select_one("h2, h3, .title")
                    title = self.clean_text(title_el.get_text()) if title_el else ""
                    if not title and link:
                        title = self.clean_text(link.get_text())

                if not title or len(title) < 15:
                    continue

                href = link.get("href", "") if link else ""
                if href and not href.startswith("http"):
                    href = f"https://www.enabel.be{href}"

                # Filtrer par pays (Benin)
                if "benin" not in title.lower() and "bénin" not in title.lower():
                    # Accepter quand meme si pas de filtre pays dans le titre
                    pass

                reference = self.generate_reference("ENABEL", title, href)

                publications.append({
                    "source": self.source,
                    "reference": reference,
                    "title": title[:500],
                    "summary": "",
                    "budget": None,
                    "deadline": None,
                    "pdf_url": href,
                    "html_content": "",
                    "category": "marche",
                    "sectors": self._detect_sectors(title),
                    "regions": ["Benin"],
                    "published_date": self.now_utc(),
                    "authority_name": "ENABEL",
                    "authority_email": None,
                    "document_type": "AAO",
                    "financing_source": "ENABEL (Cooperation belge)",
                    "country": "Benin",
                })

        except Exception as e:
            logger.warning(f"[{self.name}] Erreur: {e}")

        return publications

    def _detect_sectors(self, title: str) -> list:
        title_lower = title.lower()
        sectors = []
        if any(w in title_lower for w in ("sante", "medical")):
            sectors.append("Sante")
        if any(w in title_lower for w in ("agriculture", "rural")):
            sectors.append("Agriculture")
        if any(w in title_lower for w in ("education", "formation")):
            sectors.append("Education")
        if any(w in title_lower for w in ("eau", "assainissement")):
            sectors.append("Eau et Assainissement")
        if any(w in title_lower for w in ("numerique", "digital", "informatique")):
            sectors.append("TIC")
        if any(w in title_lower for w in ("travaux", "construction", "infrastructure")):
            sectors.append("BTP")
        if any(w in title_lower for w in ("fourniture", "equipement")):
            sectors.append("Fournitures")
        if any(w in title_lower for w in ("service", "consultant", "etude")):
            sectors.append("Services")
        return sectors or ["Services"]

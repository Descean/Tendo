"""Scraper JNMP – Journal National des Marches Publics du Benin."""

from typing import List
from datetime import datetime

from app.services.scraping.base import BaseScraper
from app.utils.logger import logger


class JNMPScraper(BaseScraper):
    """Scraper pour le Journal National des Marches Publics du Benin."""

    name = "JNMP"
    source = "JNMP"
    base_url = "https://jnmp.marches-publics.bj/appel-offre-liste"

    def scrape(self) -> List[dict]:
        publications = []

        try:
            soup = self.fetch_page(self.base_url)

            # Chercher les lignes de tableau ou les cartes d'appels d'offres
            rows = soup.select(
                "table tbody tr, .card, .ao-item, .list-group-item, "
                ".views-row, article, .appel-offre"
            )

            if not rows:
                # Essayer une structure de page differente
                rows = soup.select("div.content a, main a[href*='appel'], a[href*='offre']")

            for row in rows[:50]:
                title = ""
                href = ""

                # Essayer plusieurs structures
                link = row.select_one("a") if row.name != "a" else row
                if link:
                    title = self.clean_text(link.get_text())
                    href = link.get("href", "")
                else:
                    cols = row.select("td")
                    if cols:
                        title = self.clean_text(cols[0].get_text())
                        link_in_col = row.select_one("a")
                        if link_in_col:
                            href = link_in_col.get("href", "")

                if not title or len(title) < 10:
                    continue

                if href and not href.startswith("http"):
                    href = f"https://jnmp.marches-publics.bj{href}"

                reference = self.generate_reference("JNMP", title, href)

                # Date limite (chercher dans les colonnes ou elements)
                deadline = None
                date_tags = row.select("td, .date, .deadline, time, span")
                for dt in date_tags:
                    text = dt.get_text().strip()
                    deadline = self._try_parse_date(text)
                    if deadline:
                        break

                publications.append({
                    "source": self.source,
                    "reference": reference,
                    "title": title[:500],
                    "summary": "",
                    "budget": None,
                    "deadline": deadline,
                    "pdf_url": href,
                    "html_content": "",
                    "category": "marche",
                    "sectors": self._detect_sectors(title),
                    "regions": self._detect_regions(title),
                    "published_date": self.now_utc(),
                    "authority_name": "",
                    "authority_email": None,
                    "document_type": "AAO",
                    "financing_source": "Budget National",
                    "country": "Benin",
                })

        except Exception as e:
            logger.error(f"[{self.name}] Erreur scraping: {e}")

        return publications

    def _try_parse_date(self, text: str):
        if not text or len(text) < 8:
            return None
        formats = [
            "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d",
            "%d %B %Y", "%d %b %Y",
        ]
        # Mois francais
        months_fr = {
            "janvier": "01", "fevrier": "02", "mars": "03", "avril": "04",
            "mai": "05", "juin": "06", "juillet": "07", "aout": "08",
            "septembre": "09", "octobre": "10", "novembre": "11", "decembre": "12",
        }
        text_clean = text.strip().lower()
        for mois, num in months_fr.items():
            text_clean = text_clean.replace(mois, num)

        for fmt in formats:
            try:
                return datetime.strptime(text_clean[:10], fmt)
            except (ValueError, TypeError):
                continue
        return None

    def _detect_sectors(self, title: str) -> list:
        title_lower = title.lower()
        sectors = []
        if any(w in title_lower for w in ("travaux", "construction", "route", "batiment")):
            sectors.append("BTP")
        if any(w in title_lower for w in ("fourniture", "equipement", "materiel")):
            sectors.append("Fournitures")
        if any(w in title_lower for w in ("service", "prestation", "consultant", "etude")):
            sectors.append("Services")
        if any(w in title_lower for w in ("informatique", "numerique", "logiciel")):
            sectors.append("TIC")
        return sectors or ["Autres"]

    def _detect_regions(self, title: str) -> list:
        title_lower = title.lower()
        regions = []
        region_map = {
            "Cotonou": ["cotonou"], "Porto-Novo": ["porto-novo"],
            "Parakou": ["parakou"], "Bohicon": ["bohicon"],
            "Abomey-Calavi": ["calavi"], "National": ["national"],
        }
        for region, keywords in region_map.items():
            if any(kw in title_lower for kw in keywords):
                regions.append(region)
        return regions or ["National"]

"""Scraper pour l'ARMP Bénin (Autorité de Régulation des Marchés Publics).

Le site armp.bj est un WordPress avec un thème custom. Les catégories avec
du contenu utile sont : recueils-de-decisions (PDFs), communiqués, et avis.
"""

from typing import List
from urllib.parse import urljoin

from app.services.scraping.base import BaseScraper
from app.utils.logger import logger


class ARMPScraper(BaseScraper):
    name = "ARMP"
    base_url = "https://armp.bj"
    source = "ARMP"

    # Catégories réelles du site WordPress
    CATEGORIES = [
        "/category/actualites/appels-doffres/",
        "/category/actualites/recueils-de-decisions/",
        "/category/actualites/communiques/",
        "/category/documentation/avis/",
    ]

    def scrape(self) -> List[dict]:
        publications = []

        for cat_path in self.CATEGORIES:
            url = f"{self.base_url}{cat_path}"
            try:
                soup = self.fetch_page(url)
                pubs = self._extract_publications(soup, cat_path)
                publications.extend(pubs)
            except Exception as e:
                logger.warning(f"[{self.name}] Erreur {cat_path}: {e}")

        return publications

    def _extract_publications(self, soup, category_path: str) -> List[dict]:
        """Extrait les publications d'une page de catégorie ARMP."""
        results = []

        # Méthode 1: Chercher les liens datés (format /storage/YYYY/MM/ ou /YYYY/)
        pdf_links = soup.select("a[href*='/storage/']")
        for link in pdf_links:
            href = link.get("href", "")
            text = self.clean_text(link.get_text())
            if not href.endswith(".pdf"):
                continue

            full_url = urljoin(self.base_url, href)
            # Extraire un titre du nom de fichier si pas de texte
            if not text or any(x in text.lower() for x in ("pièce jointe", "télécharger", "attachment")):
                # Déduire le titre du nom de fichier PDF
                filename = href.split("/")[-1].replace(".pdf", "").replace("-", " ").replace("_", " ")
                text = f"ARMP – {filename.title()}"

            if len(text) < 5:
                continue

            reference = self.generate_reference(self.source, text, full_url)
            results.append({
                "source": self.source,
                "reference": reference,
                "title": text[:500],
                "summary": f"Document ARMP – {category_path.split('/')[-2].replace('-', ' ').title()}",
                "budget": None,
                "deadline": None,
                "pdf_url": full_url,
                "html_content": "",
                "category": "décision" if "decision" in category_path else "marché",
                "sectors": [],
                "regions": ["Bénin"],
                "published_date": "",
                "authority_email": None,
                "authority_name": "ARMP Bénin",
            })

        # Méthode 2: Chercher les articles/posts WordPress classiques
        for selector in ["article a", "h2 a", "h3 a", ".entry-title a"]:
            for link in soup.select(selector):
                href = link.get("href", "")
                text = self.clean_text(link.get_text())
                if not text or len(text) < 10:
                    continue
                if "armp.bj" not in href and not href.startswith("/"):
                    continue

                full_url = urljoin(self.base_url, href)
                reference = self.generate_reference(self.source, text, full_url)

                # Éviter les doublons
                if any(p["reference"] == reference for p in results):
                    continue

                results.append({
                    "source": self.source,
                    "reference": reference,
                    "title": text[:500],
                    "summary": "",
                    "budget": None,
                    "deadline": None,
                    "pdf_url": full_url if full_url.endswith(".pdf") else None,
                    "html_content": "",
                    "category": "marché",
                    "sectors": [],
                    "regions": ["Bénin"],
                    "published_date": "",
                    "authority_email": None,
                    "authority_name": "ARMP Bénin",
                })

        return results

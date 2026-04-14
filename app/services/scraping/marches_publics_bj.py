"""Scraper pour le Portail des Marchés Publics du Bénin.

Utilise l'API JSON publique du portail :
  https://api.marches-publics.bj/v2/api/portail/appelsoffres

L'ancien scraper HTML a été abandonné car le site est une SPA Angular
qui ne rend rien côté serveur.
"""

from datetime import datetime, timezone
from typing import List, Optional

from app.services.scraping.base import BaseScraper
from app.utils.logger import logger


class MarchesPublicsBJScraper(BaseScraper):
    name = "marches-publics.bj"
    base_url = "https://www.marches-publics.bj"
    source = "marches-publics.bj"

    # ── API backend réelle (découverte via n8n + DevTools) ──
    API_URL = "https://api.marches-publics.bj/v2/api/portail/appelsoffres"
    PAGE_SIZE = 100  # max items par page

    def scrape(self) -> List[dict]:
        publications = []
        page = 0

        while True:
            try:
                items, total_pages = self._fetch_page(page)
            except Exception as e:
                logger.error(f"[{self.name}] Erreur API page {page}: {e}")
                break

            for item in items:
                pub = self._parse_item(item)
                if pub:
                    publications.append(pub)

            page += 1
            if page >= total_pages:
                break

        logger.info(f"[{self.name}] {len(publications)} appels d'offres récupérés via API")
        return publications

    # ──────────────────────────────────────────────
    def _fetch_page(self, page: int) -> tuple:
        """Appelle l'API JSON et retourne (items, total_pages)."""
        params = {
            "page": page,
            "size": self.PAGE_SIZE,
            "search": "",
            "status": 1,  # 1 = en cours
        }
        headers = {
            "Accept": "application/json",
            "Origin": "https://www.marches-publics.bj",
            "Referer": "https://www.marches-publics.bj/",
        }

        resp = self.session.get(self.API_URL, params=params, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        items = data.get("content", [])
        total_pages = data.get("totalPages", 1)
        total_elements = data.get("totalElements", 0)

        logger.info(
            f"[{self.name}] Page {page + 1}/{total_pages} — "
            f"{len(items)} items (total: {total_elements})"
        )
        return items, total_pages

    # ──────────────────────────────────────────────
    def _parse_item(self, item: dict) -> Optional[dict]:
        """Transforme un objet API en publication Tendo."""
        try:
            ao = item.get("appelsoffres", {})
            ac = item.get("autoriteContractante", {})

            # ── Champs essentiels ──
            title = ao.get("apoObjet", "").strip()
            reference = item.get("dosReference") or ao.get("apoReference") or ""
            if not title and not reference:
                return None

            # ── PDF ──
            pdf_url = item.get("dosFichier") or None

            # ── Dates ──
            deadline = self._parse_date(item.get("dosDateLimiteDepot"))
            published_date = self._parse_date(item.get("dosDatePublication"))

            # ── Autorité contractante ──
            authority_name = ac.get("denomination", "").strip() or None
            authority_email = ac.get("email", "").strip() or None

            # ── Type de marché → secteur ──
            type_marche = ao.get("typemarche", {})
            type_code = type_marche.get("code", "")  # T, F, S
            type_libelle = type_marche.get("libelle", "")  # Travaux, Fournitures, Services

            # ── Détection secteurs ──
            sectors = self.detect_sectors(title)
            if type_libelle and type_libelle not in sectors:
                sectors.insert(0, type_libelle)

            # ── Résumé enrichi ──
            summary_parts = []
            if type_libelle:
                summary_parts.append(f"Type: {type_libelle}")
            if authority_name:
                summary_parts.append(f"Autorité contractante: {authority_name}")
            lieu = (item.get("doslieuacquisitiondao") or "").strip()
            if lieu:
                summary_parts.append(f"Lieu d'acquisition du DAO: {lieu}")
            nb_lots = item.get("dosNombreLots")
            if nb_lots:
                divisible = item.get("dosLotDivisible", "")
                summary_parts.append(f"Nombre de lots: {nb_lots} (divisible: {divisible})")
            heure_limite = item.get("dosHeurelimitedepot")
            if heure_limite and deadline:
                summary_parts.append(f"Heure limite de dépôt: {heure_limite}")
            heure_ouverture = item.get("dosHeureOuvertureDesPlis")
            date_ouverture = item.get("dosDateOuvertueDesplis")
            if date_ouverture:
                summary_parts.append(
                    f"Ouverture des plis: {date_ouverture}"
                    + (f" à {heure_ouverture}" if heure_ouverture else "")
                )

            summary = "\n".join(summary_parts)

            # ── Document type ──
            doc_type = "AAO"  # Avis d'Appel d'Offres par défaut

            # ── Référence unique pour déduplication ──
            unique_ref = f"MPBJ-{reference}" if reference else self.generate_reference(
                self.source, title, pdf_url or ""
            )

            return {
                "source": self.source,
                "reference": unique_ref,
                "title": title,
                "summary": summary,
                "budget": None,
                "deadline": deadline,
                "pdf_url": pdf_url,
                "html_content": "",
                "category": type_libelle.lower() if type_libelle else "marché",
                "sectors": sectors,
                "regions": ["Bénin"],
                "published_date": published_date,
                "authority_email": authority_email,
                "authority_name": authority_name,
                "document_type": doc_type,
                "financing_source": "BN",  # Budget National (portail gouvernemental)
                "country": "Benin",
            }

        except Exception as e:
            logger.warning(f"[{self.name}] Erreur parsing item: {e}")
            return None

    # ──────────────────────────────────────────────
    @staticmethod
    def _parse_date(date_str: Optional[str]) -> Optional[datetime]:
        """Parse une date ISO (YYYY-MM-DD) en datetime timezone-aware."""
        if not date_str:
            return None
        try:
            dt = datetime.strptime(date_str[:10], "%Y-%m-%d")
            return dt.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return None

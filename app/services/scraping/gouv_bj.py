"""Scraper pour le portail Gouvernement du Bénin – Marchés Publics.

Ce scraper est le plus fiable : gouv.bj utilise du HTML server-rendered
avec des balises <article> bien structurées.
"""

import re
from typing import List, Optional
from urllib.parse import urljoin

from app.services.scraping.base import BaseScraper
from app.utils.logger import logger


class GouvBJScraper(BaseScraper):
    name = "gouv.bj"
    base_url = "https://www.gouv.bj/opportunites/marches-publics/"
    source = "gouv.bj"

    def scrape(self) -> List[dict]:
        publications = []

        try:
            soup = self.fetch_page(self.base_url)
            articles = soup.select("article")

            for article in articles[:50]:
                pub = self._parse_article(article)
                if pub:
                    publications.append(pub)

        except Exception as e:
            logger.error(f"[{self.name}] Erreur: {e}")

        return publications

    def _parse_article(self, article) -> dict | None:
        """Parse un article de gouv.bj."""
        try:
            title_el = article.select_one("h3, h2, h4")
            if not title_el:
                return None
            title = self.clean_text(title_el.get_text())
            if not title or len(title) < 10:
                return None

            link_el = article.select_one("a[href]")
            href = link_el.get("href", "") if link_el else ""
            full_url = urljoin("https://www.gouv.bj", href) if href else ""

            category_el = article.select_one("span.upper")
            category = self.clean_text(category_el.get_text()) if category_el else "marché"

            date_el = article.select_one(".date, time, span.date")
            date_text = self.clean_text(date_el.get_text()) if date_el else ""

            summary_el = article.select_one("p:not(.upper)")
            summary = self.clean_text(summary_el.get_text())[:300] if summary_el else ""

            pdf_link = article.select_one("a[href$='.pdf']")
            pdf_url = urljoin("https://www.gouv.bj", pdf_link["href"]) if pdf_link else None

            reference = self.generate_reference(self.source, title, full_url)

            pub = {
                "source": self.source,
                "reference": reference,
                "title": title[:500],
                "summary": summary,
                "budget": None,
                "deadline": None,
                "pdf_url": pdf_url,
                "html_content": "",
                "category": category.lower() if category else "marché",
                "sectors": [],
                "regions": ["Bénin"],
                "published_date": date_text,
                "authority_email": None,
                "authority_name": None,
                "document_type": "AAO",
                "financing_source": None,
                "country": "Benin",
            }

            # Suivre le lien de detail pour enrichir les champs
            if full_url and not full_url.endswith(".pdf"):
                self._enrich_from_detail(pub, full_url)

            # Extraire l'autorité contractante du titre
            if not pub["authority_name"]:
                pub["authority_name"] = self._extract_authority(title, summary)

            return pub
        except Exception as e:
            logger.warning(f"[{self.name}] Erreur parsing article: {e}")
            return None

    def _enrich_from_detail(self, pub: dict, url: str):
        """Visite la page de detail pour extraire champs supplementaires."""
        try:
            soup = self.fetch_page(url)
            text = soup.get_text(" ", strip=True).lower()

            # Extraire deadline (date limite / date de depot)
            deadline_match = re.search(
                r"(?:date\s+(?:limite|de\s+d[ée]p[oô]t|de\s+remise))\s*[:\-]?\s*"
                r"(\d{1,2}[\s/\-]\w+[\s/\-]\d{4}(?:\s+[àa]\s+\d{1,2}[h:]\d{0,2})?)",
                text,
            )
            if deadline_match and not pub["deadline"]:
                pub["deadline"] = self._parse_date(deadline_match.group(1))

            # Budget / Montant garantie
            budget_match = re.search(
                r"(?:garantie\s+de\s+soumission|montant\s+de\s+la\s+garantie|caution)\s*[:\-]?\s*"
                r"([\d\s.,]+)\s*(?:fcfa|f\s*cfa|xof)",
                text,
            )
            if budget_match and not pub["budget"]:
                amount_str = budget_match.group(1).replace(" ", "").replace(".", "").replace(",", "")
                try:
                    pub["budget"] = float(amount_str)
                except ValueError:
                    pass

            # Autorité contractante
            auth_match = re.search(
                r"(?:autorit[ée]\s+contractante|ma[iî]tre\s+d.ouvrage|organisme)\s*[:\-]?\s*([^\n.]{5,100})",
                text,
            )
            if auth_match:
                pub["authority_name"] = auth_match.group(1).strip().title()

            # Type de marché
            type_match = re.search(
                r"(?:type\s+de\s+march[ée]|nature\s+du\s+march[ée])\s*[:\-]?\s*(travaux|fournitures?|services?|prestations?\s+intellectuelles?|consultance)",
                text,
            )
            if type_match:
                pub["document_type"] = type_match.group(1).strip().title()

            # PDF dans la page de detail
            if not pub["pdf_url"]:
                pdf_link = soup.select_one("a[href$='.pdf']")
                if pdf_link:
                    pub["pdf_url"] = urljoin(url, pdf_link["href"])

            # Contenu HTML pour analyse ulterieure
            content_el = soup.select_one(".entry-content, .post-content, article, .content")
            if content_el:
                pub["html_content"] = content_el.get_text(" ", strip=True)[:2000]

        except Exception as e:
            logger.debug(f"[{self.name}] Erreur detail {url}: {e}")

    def _parse_date(self, date_str: str) -> Optional[str]:
        """Parse une date francaise en datetime string.

        Formats supportes :
        - 03/01/2026, 03-01-2026
        - 03 janvier 2026
        - 03 janvier 2026 a 10h00
        - 03 janvier 2026 a 10:00
        - vendredi 03 janvier 2026
        - 3 janv. 2026
        - 2026-01-03 (ISO)
        """
        import re
        from datetime import datetime

        if not date_str:
            return None

        MONTHS_FR = {
            "janvier": 1, "janv": 1, "jan": 1,
            "février": 2, "fevrier": 2, "fev": 2, "fevr": 2,
            "mars": 3, "mar": 3,
            "avril": 4, "avr": 4,
            "mai": 5,
            "juin": 6, "jun": 6,
            "juillet": 7, "juil": 7, "jul": 7,
            "août": 8, "aout": 8, "aou": 8,
            "septembre": 9, "sept": 9, "sep": 9,
            "octobre": 10, "oct": 10,
            "novembre": 11, "nov": 11,
            "décembre": 12, "decembre": 12, "dec": 12,
        }

        try:
            date_str = date_str.strip().lower()
            # Nettoyer les points des abbreviations (janv. -> janv)
            date_str = date_str.replace(".", " ").strip()

            hour, minute = 0, 0

            # Extraire l'heure si presente (10h00, 10:00, 10 h 00, 10h)
            time_match = re.search(r'(\d{1,2})\s*[h:]\s*(\d{2})?', date_str)
            if time_match:
                hour = int(time_match.group(1))
                minute = int(time_match.group(2) or 0)

            # Format ISO : 2026-01-03
            iso_match = re.match(r'(\d{4})-(\d{2})-(\d{2})', date_str)
            if iso_match:
                return datetime(int(iso_match.group(1)), int(iso_match.group(2)), int(iso_match.group(3)), hour, minute).isoformat()

            # Format JJ/MM/AAAA ou JJ-MM-AAAA
            num_match = re.match(r'(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})', date_str)
            if num_match:
                d, m, y = int(num_match.group(1)), int(num_match.group(2)), int(num_match.group(3))
                if 1 <= d <= 31 and 1 <= m <= 12 and 2020 <= y <= 2035:
                    return datetime(y, m, d, hour, minute).isoformat()

            # Format texte : [jour_semaine] JJ mois AAAA [a HHhMM]
            # Supprimer le jour de la semaine s'il est present
            date_str = re.sub(r'^(lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche)\s+', '', date_str)
            # Supprimer "le" en debut
            date_str = re.sub(r'^le\s+', '', date_str)

            # Extraire jour, mois texte, annee
            text_match = re.match(r'(\d{1,2})\s+(\w+)\s+(\d{4})', date_str)
            if text_match:
                day = int(text_match.group(1))
                month_str = text_match.group(2).rstrip(".")
                year = int(text_match.group(3))
                month = MONTHS_FR.get(month_str, 0)
                if month and 1 <= day <= 31 and 2020 <= year <= 2035:
                    return datetime(year, month, day, hour, minute).isoformat()

        except Exception:
            pass
        return None

    @staticmethod
    def _extract_authority(title: str, summary: str) -> Optional[str]:
        """Essaie d'extraire l'autorite contractante du titre."""
        text = f"{title} {summary}".lower()
        # Patterns courants dans les titres d'AO béninois
        patterns = [
            r"(?:commune\s+(?:de\s+|d')[\w\s-]+)",
            r"(?:minist[eè]re\s+(?:de\s+|d[eu]\s+)[\w\s'-]+)",
            r"(?:direction\s+(?:de\s+|d[eu]\s+|g[ée]n[ée]rale\s+)[\w\s'-]+)",
            r"(?:agence\s+[\w\s'-]+)",
            r"(?:office\s+[\w\s'-]+)",
            r"(?:soci[ée]t[ée]\s+[\w\s'-]+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(0).strip().title()
        return None

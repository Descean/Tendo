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
        """Parse une date francaise en datetime string."""
        from datetime import datetime
        months_fr = {
            "janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4,
            "mai": 5, "juin": 6, "juillet": 7, "août": 8, "aout": 8,
            "septembre": 9, "octobre": 10, "novembre": 11, "décembre": 12, "decembre": 12,
        }
        try:
            # Format: "31 mars 2026" ou "31/03/2026"
            date_str = date_str.strip().lower()
            # Essayer format jour/mois/année
            for fmt in ("%d/%m/%Y", "%d-%m-%Y"):
                try:
                    return datetime.strptime(date_str.split()[0] if " à " in date_str else date_str, fmt).isoformat()
                except ValueError:
                    continue

            # Essayer format "31 mars 2026"
            parts = date_str.replace("à", "").split()
            if len(parts) >= 3:
                day = int(parts[0])
                month = months_fr.get(parts[1], 0)
                year = int(parts[2])
                if month and 1 <= day <= 31 and 2020 <= year <= 2030:
                    return datetime(year, month, day).isoformat()
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

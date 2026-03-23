"""Classe de base pour tous les scrapers."""

import abc
import hashlib
from datetime import datetime, timezone
from typing import List, Optional

import requests
from bs4 import BeautifulSoup

from app.utils.logger import logger


class BaseScraper(abc.ABC):
    """Classe abstraite pour les scrapers d'appels d'offres."""

    name: str = "base"
    base_url: str = ""
    source: str = ""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Tendo/1.0 (Assistant Marchés Publics; +https://shiftup.bj)",
            "Accept-Language": "fr-FR,fr;q=0.9",
        })

    def run(self) -> List[dict]:
        """Point d'entrée principal. Retourne une liste de publications."""
        try:
            logger.info(f"[{self.name}] Démarrage du scraping: {self.base_url}")
            publications = self.scrape()
            logger.info(f"[{self.name}] {len(publications)} publications trouvées")
            return publications
        except Exception as e:
            logger.error(f"[{self.name}] Erreur scraping: {e}")
            return []

    @abc.abstractmethod
    def scrape(self) -> List[dict]:
        """Méthode à implémenter par chaque scraper."""
        ...

    def fetch_page(self, url: str, params: Optional[dict] = None) -> BeautifulSoup:
        """Récupère et parse une page HTML."""
        response = self.session.get(url, params=params, timeout=30)
        response.raise_for_status()
        return BeautifulSoup(response.content, "lxml")

    def generate_reference(self, source: str, title: str, url: str = "") -> str:
        """Génère une référence unique à partir des données."""
        raw = f"{source}-{title}-{url}"
        hash_hex = hashlib.md5(raw.encode()).hexdigest()[:8]
        return f"AO-{source.upper()[:4]}-{hash_hex}"

    @staticmethod
    def clean_text(text: Optional[str]) -> str:
        """Nettoie le texte extrait."""
        if not text:
            return ""
        return " ".join(text.split()).strip()

    @staticmethod
    def now_utc() -> datetime:
        return datetime.now(timezone.utc)

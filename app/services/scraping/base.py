"""Classe de base pour tous les scrapers."""

import abc
import hashlib
from datetime import datetime, timezone
from typing import List, Optional

import requests
from requests.adapters import HTTPAdapter
from bs4 import BeautifulSoup

from app.utils.logger import logger


class IPv4Adapter(HTTPAdapter):
    """Force les connexions en IPv4 pour eviter les problemes IPv6 sur Contabo."""

    def init_poolmanager(self, *args, **kwargs):
        import urllib3
        kwargs["socket_options"] = urllib3.connection.HTTPConnection.default_socket_options
        super().init_poolmanager(*args, **kwargs)

    def send(self, request, *args, **kwargs):
        import socket
        # Monkey-patch temporaire pour forcer IPv4
        old_getaddrinfo = socket.getaddrinfo

        def ipv4_getaddrinfo(host, port, family=0, *a, **kw):
            return old_getaddrinfo(host, port, socket.AF_INET, *a, **kw)

        socket.getaddrinfo = ipv4_getaddrinfo
        try:
            return super().send(request, *args, **kwargs)
        finally:
            socket.getaddrinfo = old_getaddrinfo


class BaseScraper(abc.ABC):
    """Classe abstraite pour les scrapers d'appels d'offres."""

    name: str = "base"
    base_url: str = ""
    source: str = ""

    def __init__(self):
        self.session = requests.Session()
        self.session.mount("https://", IPv4Adapter(max_retries=3))
        self.session.mount("http://", IPv4Adapter(max_retries=3))
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Gecko/20100101 Firefox/120.0",
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

    @staticmethod
    def detect_sectors(text: str) -> list:
        """Detecte les secteurs a partir d'un texte (titre, resume)."""
        text_lower = text.lower()
        sectors = []
        sector_keywords = {
            "BTP": ["construction", "route", "batiment", "infrastructure", "pont", "barrage", "travaux"],
            "Fournitures": ["fourniture", "equipement", "materiel", "vehicule", "mobilier"],
            "Services": ["service", "conseil", "consultant", "etude", "audit", "formation"],
            "TIC": ["informatique", "logiciel", "systeme", "numerique", "reseau", "telecom"],
            "Sante": ["sante", "medical", "hopital", "medicament", "pharmaceutique"],
            "Education": ["education", "ecole", "formation", "universitaire", "scolaire"],
            "Agriculture": ["agricole", "agriculture", "irrigation", "rural", "elevage"],
            "Environnement": ["environnement", "eau", "assainissement", "dechet", "energie solaire"],
            "Transport": ["transport", "routier", "aerien", "maritime", "ferroviaire"],
            "Energie": ["energie", "electrique", "electricite", "solaire", "petrole", "gaz"],
        }
        for sector, keywords in sector_keywords.items():
            if any(kw in text_lower for kw in keywords):
                sectors.append(sector)
        return sectors

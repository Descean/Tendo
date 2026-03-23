"""Azure Function – Scraping quotidien marches-publics.bj."""

import logging
import json

import azure.functions as func

# Note: En déploiement Azure Functions, configurer le PYTHONPATH pour inclure le dossier parent
from app.services.scraping.marches_publics_bj import MarchesPublicsBJScraper


def main(mytimer: func.TimerRequest) -> None:
    logging.info("Azure Function: Scraping marches-publics.bj démarré")

    scraper = MarchesPublicsBJScraper()
    publications = scraper.run()

    logging.info(f"marches-publics.bj: {len(publications)} publications trouvées")

    # Les publications sont insérées en base via l'API REST ou directement
    # via une connexion DB configurée dans les settings Azure
    if publications:
        _insert_publications(publications)


def _insert_publications(publications: list):
    """Insère les publications via l'API Tendo."""
    import requests
    import os

    api_url = os.getenv("TENDO_API_URL", "https://api.shiftup.bj")
    api_key = os.getenv("TENDO_API_KEY", "")

    for pub in publications:
        try:
            requests.post(
                f"{api_url}/publications/ingest",
                json=pub,
                headers={"X-API-Key": api_key},
                timeout=10,
            )
        except Exception as e:
            logging.error(f"Erreur insertion publication: {e}")

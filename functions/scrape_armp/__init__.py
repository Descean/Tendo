"""Azure Function – Scraping quotidien ARMP Bénin."""

import logging
import azure.functions as func

from app.services.scraping.armp import ARMPScraper


def main(mytimer: func.TimerRequest) -> None:
    logging.info("Azure Function: Scraping ARMP démarré")

    scraper = ARMPScraper()
    publications = scraper.run()

    logging.info(f"ARMP: {len(publications)} publications trouvées")

    if publications:
        _insert_publications(publications)


def _insert_publications(publications: list):
    import requests, os
    api_url = os.getenv("TENDO_API_URL", "https://api.shiftup.bj")
    api_key = os.getenv("TENDO_API_KEY", "")
    for pub in publications:
        try:
            requests.post(f"{api_url}/publications/ingest", json=pub,
                         headers={"X-API-Key": api_key}, timeout=10)
        except Exception as e:
            logging.error(f"Erreur insertion: {e}")

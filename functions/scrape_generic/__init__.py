"""Azure Function – Template générique pour ajouter de nouvelles sources.

Pour ajouter une nouvelle source :
1. Copiez ce dossier avec un nouveau nom (ex: scrape_bad/)
2. Modifiez les paramètres du GenericScraper ci-dessous
3. Ajoutez un function.json avec le timer souhaité
4. Déployez sur Azure Functions
"""

import logging
import azure.functions as func

from app.services.scraping.generic import GenericScraper


# Configuration du scraper – à personnaliser
SCRAPER_CONFIG = {
    "name": "exemple",
    "base_url": "https://example.com/appels-offres",
    "source": "Exemple",
    "list_selector": "article, .card",
    "title_selector": "h2 a, h3 a",
    "date_selector": "time, .date",
    "link_selector": "a[href]",
    "summary_selector": "p, .excerpt",
    "default_category": "marché",
    "default_regions": ["Afrique de l'Ouest"],
}


def main(mytimer: func.TimerRequest) -> None:
    source = SCRAPER_CONFIG["name"]
    logging.info(f"Azure Function: Scraping {source} démarré")

    scraper = GenericScraper(**SCRAPER_CONFIG)
    publications = scraper.run()

    logging.info(f"{source}: {len(publications)} publications trouvées")

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

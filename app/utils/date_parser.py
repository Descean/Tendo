"""Utilitaire partage pour parser les dates francaises.

Utilise par scheduler.py, les scrapers, et deepseek_reader.py.
Evite d'importer GouvBJScraper juste pour parser une date.
"""

import re
from datetime import datetime
from typing import Optional

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


def parse_date_fr(date_str: str) -> Optional[str]:
    """Parse une date francaise en ISO string.

    Formats supportes :
    - 03/01/2026, 03-01-2026
    - 03 janvier 2026
    - 03 janvier 2026 a 10h00
    - 03 janvier 2026 a 10:00
    - vendredi 03 janvier 2026
    - 3 janv. 2026
    - 2026-01-03 (ISO)

    Returns:
        ISO datetime string ou None si parsing echoue.
    """
    if not date_str:
        return None

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
            return datetime(
                int(iso_match.group(1)), int(iso_match.group(2)),
                int(iso_match.group(3)), hour, minute,
            ).isoformat()

        # Format JJ/MM/AAAA ou JJ-MM-AAAA
        num_match = re.match(r'(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})', date_str)
        if num_match:
            d, m, y = int(num_match.group(1)), int(num_match.group(2)), int(num_match.group(3))
            if 1 <= d <= 31 and 1 <= m <= 12 and 2020 <= y <= 2035:
                return datetime(y, m, d, hour, minute).isoformat()

        # Format texte : [jour_semaine] JJ mois AAAA [a HHhMM]
        date_str = re.sub(r'^(lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche)\s+', '', date_str)
        date_str = re.sub(r'^le\s+', '', date_str)

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

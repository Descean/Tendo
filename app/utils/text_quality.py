"""Controle qualite des contenus avant envoi aux abonnes.

- clean_authority : rejette les "autorites" polluees par la navigation des sites
  (ex. "S D'Adoption En Savoir Plus Offres D'Emploi Cdd ...", ", Invite Les Cabinets ...").
- is_generic_title : detecte les titres de menu ("Marchés publics", "Appels d'offres").
- outdated_reason : dit si une publication n'est plus d'actualite, meme quand
  le champ deadline n'a pas ete renseigne (date limite trouvee dans le texte,
  annee depassee, publication trop ancienne).
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from app.utils.date_parser import parse_date_fr


def strip_accents(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", text or "") if unicodedata.category(c) != "Mn"
    )


def _norm(text: str) -> str:
    text = strip_accents(text or "").lower()
    text = re.sub(r"[’'`]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# ── Autorite contractante ──────────────────────────────────────────

# Morceaux de menus / formulaires / phrases d'avis qui ne sont jamais un nom d'autorite
_AUTHORITY_NOISE = (
    "en savoir plus", "offres d emploi", "offre d emploi", "cdd", "cdi",
    "invite les", "invite le", "interesse", "cliquez", "lire la suite",
    "accueil", "newsletter", "abonnez", "connexion", "rechercher",
    "avis d appel a candidature", "recrutement du", "pour les prestations",
    "ci-dessus", "ci dessus", "telecharger", "partager", "s adoption",
    "manifester leur interet", "voir plus", "suivez-nous", "copyright",
)

_AUTHORITY_HINTS = (
    "ministere", "mairie", "commune", "agence", "direction", "office", "societe",
    "universite", "centre", "chu", "cnhu", "hopital", "prmp", "programme", "projet",
    "institut", "autorite", "conseil", "fonds", "caisse", "port", "banque", "asin",
    "armp", "sbee", "soneb", "ecole", "lycee", "zone", "departement", "prefecture",
    "assemblee", "cour", "tribunal", "presidence", "ambassade", "unite", "cellule",
)


def clean_authority(name: Optional[str]) -> Optional[str]:
    """Retourne un nom d'autorite presentable, ou None s'il est douteux."""
    if not name:
        return None
    name = re.sub(r"\s+", " ", str(name)).strip(" ,;:-–—.\t\n")
    if len(name) < 3 or "@" in name or "http" in name.lower():
        return None
    n = _norm(name)
    if any(noise in n for noise in _AUTHORITY_NOISE):
        return None
    words = n.split()
    # Un nom d'autorite tient rarement en plus de 14 mots / 110 caracteres
    if len(words) > 14 or len(name) > 110:
        return None
    # Phrase verbale typique d'un avis ("invite", "lance", "sollicite"...)
    if re.search(r"\b(invite|lance|sollicite|informe|porte a la connaissance)\b", n):
        return None
    # Sigles courts acceptes (ASIN, ARMP, SBEE...) ; sinon exiger un indice de structure
    if len(words) <= 2 and re.fullmatch(r"[A-Z0-9\-\.&' ]{2,20}", name):
        return name
    if len(words) > 6 and not any(h in n for h in _AUTHORITY_HINTS):
        return None
    # Retablir une casse correcte si tout est en Title Case "Mairie De La Commune De"
    name = re.sub(r"\b(De|Du|Des|La|Le|Les|Et|En|Au|Aux|L|D)\b(?=\s|')",
                  lambda m: m.group(1).lower(), name)
    return name[0].upper() + name[1:]


# ── Titres ─────────────────────────────────────────────────────────

_GENERIC_TITLES = {
    "marches publics", "marche public", "appels d offres", "appel d offres",
    "avis d appel d offres", "opportunites", "actualites", "publications",
    "avis", "annonces", "recrutements", "appels a candidature", "voir toutes",
    "marches publics et opportunites", "nos appels d offres",
}


def is_generic_title(title: Optional[str]) -> bool:
    n = _norm(title or "")
    if not n:
        return True
    if n in _GENERIC_TITLES:
        return True
    # Titre de 1 a 3 mots sans chiffre ni objet precis
    words = n.split()
    return len(words) <= 3 and not re.search(r"\d", n)


# ── Actualite ──────────────────────────────────────────────────────

_MOIS = (
    r"janvier|janv|f[ée]vrier|fevr|f[ée]v|mars|avril|avr|mai|juin|juillet|juil|"
    r"ao[uû]t|septembre|sept|octobre|oct|novembre|nov|d[ée]cembre|d[ée]c"
)
_DATE_RX = rf"(\d{{1,2}}(?:er)?\s+(?:{_MOIS})\.?\s+\d{{4}}|\d{{1,2}}[/\-.]\d{{1,2}}[/\-.]\d{{4}})"
_DEADLINE_KEYWORDS = (
    r"date\s+limite|au\s+plus\s+tard|cl[ôo]ture|date\s+de\s+d[ée]p[ôo]t|"
    r"date\s+de\s+remise|d[ée]p[ôo]t\s+des\s+(?:offres|dossiers|candidatures|propositions)|"
    r"avant\s+le|jusqu.au|deadline|closing\s+date"
)
_DEADLINE_RX = re.compile(rf"(?:{_DEADLINE_KEYWORDS})[^0-9]{{0,40}}{_DATE_RX}", re.I)
_YEAR_RX = re.compile(r"\b(20[12]\d)\b")


def _parse(date_txt: str) -> Optional[datetime]:
    txt = re.sub(r"(\d)er\b", r"\1", date_txt).replace(".", " ")
    txt = re.sub(r"(\d{1,2})[/\-\s](\d{1,2})[/\-\s](\d{4})", r"\1/\2/\3", txt)
    iso = parse_date_fr(txt)
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def deadline_from_text(*texts: Optional[str]) -> Optional[datetime]:
    """Plus tardive date trouvee juste apres un mot-cle de date limite."""
    found = []
    for text in texts:
        if not text:
            continue
        for m in _DEADLINE_RX.finditer(text):
            d = _parse(m.group(1))
            if d:
                found.append(d)
    return max(found) if found else None


def outdated_reason(
    *,
    deadline: Optional[datetime],
    published_date: Optional[datetime],
    texts: Iterable[Optional[str]],
    now: Optional[datetime] = None,
    max_age_days: int = 60,
) -> Optional[str]:
    """Retourne la raison pour laquelle l'avis n'est plus d'actualite, sinon None."""
    now = now or datetime.now(timezone.utc)
    texts = [t for t in texts if t]

    def _aware(d: Optional[datetime]) -> Optional[datetime]:
        if d is None or not isinstance(d, datetime):
            return None
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)

    deadline = _aware(deadline)
    published_date = _aware(published_date)

    if deadline:
        return "date limite depassee" if deadline < now - timedelta(hours=12) else None

    text_deadline = deadline_from_text(*texts)
    if text_deadline:
        return "date limite (texte) depassee" if text_deadline < now - timedelta(hours=12) else None

    if published_date and published_date < now - timedelta(days=max_age_days):
        return f"publiee il y a plus de {max_age_days} jours"

    years = [int(y) for t in texts for y in _YEAR_RX.findall(t)]
    years = [y for y in years if 2010 <= y <= now.year + 2]
    if years:
        latest = max(years)
        # En janvier, un avis peut encore citer l'annee precedente uniquement
        if latest < now.year - 1 or (latest == now.year - 1 and now.month > 1):
            return f"ne mentionne que {latest} ou avant"

    return None

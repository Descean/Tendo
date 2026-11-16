"""Tri des avis selon le profil de l'abonné (secteurs et régions).

Règles volontairement prudentes : en cas de doute (secteur ou lieu non identifiable),
l'avis est envoyé plutôt que perdu.
"""

from __future__ import annotations

import re

from app.services.scraping.base import BaseScraper
from app.utils.text_quality import strip_accents

# Villes / communes rattachées à chaque choix de région proposé à l'inscription
_REGIONS = {
    "Cotonou": ["cotonou", "littoral"],
    "Porto-Novo": ["porto-novo", "porto novo", "oueme", "seme", "adjarra", "avrankou", "akpro", "dangbo", "aguegues", "sakete", "pobe", "ketou"],
    "Parakou": ["parakou", "borgou", "nikki", "tchaourou", "kandi", "alibori", "malanville", "banikoara", "segbana", "gogounou", "bembereke", "kalale", "n'dali", "ndali"],
    "Abomey": ["abomey", "zou", "djidja", "cove", "zagnanado", "zogbodomey", "agbangnizoun", "za-kpota"],
    "Bohicon": ["bohicon"],
    "Djougou": ["djougou", "donga", "bassila", "ouake", "copargo"],
    "Natitingou": ["natitingou", "atacora", "boukombe", "tanguieta", "kouande", "pehunco", "kerou", "materi", "toucountouna", "cobly"],
    "Lokossa": ["lokossa", "mono", "couffo", "come", "grand-popo", "athieme", "bopa", "houeyogbe", "aplahoue", "dogbo", "klouekanme", "djakotomey", "toviklin", "lalo"],
}
_TOUT = {"tout le benin", "benin", "tout", "toutes"}


def _n(t: str) -> str:
    return strip_accents(t or "").lower()


def _g(obj, attr, defaut=None):
    return getattr(obj, attr, defaut) or defaut


def secteurs_de(pub) -> list[str]:
    secteurs = list(_g(pub, "sectors", []))
    if not secteurs:
        secteurs = BaseScraper.detect_sectors(_n(f"{_g(pub, 'title', '')} {_g(pub, 'summary', '')}"))
    return secteurs


def correspond_secteurs(user, pub) -> bool:
    voulus = {_n(s) for s in _g(user, "sectors", []) if s}
    if not voulus:
        return True
    trouves = {_n(s) for s in secteurs_de(pub)}
    if not trouves:
        return True  # secteur inconnu : on envoie
    return bool(voulus & trouves)


def correspond_regions(user, pub) -> bool:
    voulues = [r for r in _g(user, "regions", []) if r]
    if not voulues or any(_n(r) in _TOUT for r in voulues):
        return True
    texte = _n(
        f"{_g(pub, 'title', '')} {_g(pub, 'summary', '')} {' '.join(_g(pub, 'regions', []))} {_g(pub, 'pdf_content', '')[:3000]}"
    )
    pays = _n(_g(pub, "country", "benin"))
    if "cedeao" in {_n(r) for r in voulues} and "benin" not in pays:
        return True
    lieux_cites = {nom for nom, mots in _REGIONS.items() if any(re.search(rf"\b{re.escape(m)}\b", texte) for m in mots)}
    if not lieux_cites:
        return True  # avis national ou lieu non précisé : on envoie
    return bool(lieux_cites & set(voulues))


def correspond_sources(user, pub) -> bool:
    voulues = {_n(s) for s in _g(user, "preferred_sources", []) if s}
    if not voulues or voulues & {"toutes", "tous"}:
        return True
    return _n(_g(pub, "source", "")) in voulues


def correspond_profil(user, pub) -> bool:
    return correspond_secteurs(user, pub) and correspond_regions(user, pub) and correspond_sources(user, pub)

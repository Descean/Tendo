"""Veille des résultats des dossiers déposés par SHIFT UP pour ses clients.

Chaque jour :
1. Tendo récupère sur shiftup.bj la liste des offres en attente de résultat
   (GET /api/tendo/soumissions, clé SHIFTUP_API_KEY).
2. Il cherche, parmi les PV / avis d'attribution qu'il a collectés depuis le dépôt,
   celui qui correspond au marché (référence, sinon autorité + objet).
3. Il identifie l'attributaire : d'abord par le texte (nom de l'entreprise près de
   « attributaire », « attribué à », « retenu »), sinon en demandant à l'IA.
4. Il renvoie le résultat à shiftup.bj (POST /api/tendo/soumissions/:id), qui met à jour
   les chiffres publics, puis prévient l'équipe sur WhatsApp.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.publication import Publication
from app.utils.logger import logger
from app.utils.text_quality import strip_accents

TYPES_RESULTAT = ("PV_ATTRIBUTION", "AVIS_ATTRIBUTION", "RAPPORT_EVALUATION")
_MOTS_VIDES = {
    "de", "du", "des", "la", "le", "les", "l", "d", "et", "en", "a", "au", "aux", "pour", "sur",
    "un", "une", "dans", "par", "avec", "marche", "marches", "lot", "lots", "travaux", "fourniture",
    "fournitures", "acquisition", "prestation", "prestations", "services", "service",
}
_FORMES = r"\b(sarl|sa|sas|sasu|ets|etablissements?|ste|societe|entreprise|ei|gie|group|groupe|sarlu)\b"


def _norm(t: str) -> str:
    t = strip_accents(t or "").lower()
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _mots(t: str) -> set[str]:
    return {m for m in _norm(t).split() if len(m) > 2 and m not in _MOTS_VIDES}


def _nom_court(nom: str) -> str:
    """Raison sociale sans forme juridique : « ETS BKK & FILS SARL » -> « bkk fils »."""
    return re.sub(r"\s+", " ", re.sub(_FORMES, " ", _norm(nom))).strip()


def _texte(pub: Publication) -> str:
    return " ".join(x for x in (pub.title, pub.summary, pub.technical_summary, pub.pdf_content) if x)


def correspond_au_marche(sou: dict, pub: Publication) -> float:
    """Score 0..1 : la publication porte-t-elle sur ce marché ?"""
    texte = _norm(_texte(pub))
    ref = _norm(sou.get("reference") or "")
    if ref and len(ref) >= 6 and ref in texte:
        return 1.0
    objet = _mots(sou.get("objet", ""))
    if not objet:
        return 0.0
    commun = len(objet & set(texte.split())) / len(objet)
    autorite = _mots(sou.get("autorite", ""))
    bonus = 0.2 if autorite and len(autorite & set(texte.split())) / len(autorite) >= 0.6 else 0.0
    return min(1.0, commun * 0.8 + bonus)


def entreprise_designee(sou: dict, texte: str) -> bool:
    """Le nom (ou le sigle) de l'entreprise apparaît-il comme attributaire dans le texte ?"""
    t = _norm(texte)
    noms = [n for n in (_nom_court(sou.get("entrepriseNom", "")), _norm(sou.get("entrepriseSigle") or "")) if len(n) >= 3]
    # « Attributaire : X », « attribué à X » : le nom suit le mot-clé de près
    for m in re.finditer(r"attributaire|attribue a|attribuee a|titulaire|adjudicataire", t):
        fenetre = t[m.end(): m.end() + 100]
        if any(re.search(rf"\b{re.escape(n)}\b", fenetre) for n in noms):
            return True
    # « X est retenu(e) » : le nom précède de près
    for m in re.finditer(r"est retenue?\b|sont retenus", t):
        fenetre = t[max(0, m.start() - 80): m.start()]
        if any(re.search(rf"\b{re.escape(n)}\b", fenetre) for n in noms):
            return True
    return False


async def _attributaire_par_ia(sou: dict, texte: str) -> Optional[dict]:
    """Demande à l'IA l'attributaire et le montant pour ce marché (et ce lot)."""
    if not settings.groq_api_key:
        return None
    consigne = (
        "Tu lis un procès-verbal ou un avis d'attribution de marché public au Bénin. "
        "Réponds uniquement en JSON : {\"attributaire\": \"nom exact ou null\", \"montant\": nombre en FCFA ou null}."
    )
    question = (
        f"Marché : {sou.get('objet')}\nAutorité : {sou.get('autorite')}\nLot(s) : {sou.get('lots') or 'non précisé'}\n\n"
        f"Texte :\n{texte[:12000]}\n\nQuelle entreprise est attributaire de ce marché (ou de ce lot) ?"
    )
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {settings.groq_api_key}"},
                json={
                    "model": "openai/gpt-oss-120b",
                    "messages": [{"role": "system", "content": consigne}, {"role": "user", "content": question}],
                    "max_tokens": 300,
                    "temperature": 0,
                    "response_format": {"type": "json_object"},
                },
            )
        if r.status_code != 200:
            logger.warning(f"[Résultats] IA {r.status_code}: {r.text[:200]}")
            return None
        data = json.loads(r.json()["choices"][0]["message"]["content"])
        if data.get("attributaire"):
            return data
    except Exception as e:
        logger.warning(f"[Résultats] IA indisponible: {e}")
    return None


def _meme_entreprise(sou: dict, nom: str) -> bool:
    cible = _nom_court(sou.get("entrepriseNom", ""))
    sigle = _norm(sou.get("entrepriseSigle") or "")
    lu = _nom_court(nom)
    if not lu:
        return False
    return bool(cible and (cible in lu or lu in cible)) or bool(sigle and re.search(rf"\b{re.escape(sigle)}\b", lu))


async def _api(methode: str, chemin: str, corps: Optional[dict] = None) -> Optional[dict]:
    base = settings.shiftup_api_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.request(
                methode, f"{base}{chemin}",
                headers={"Authorization": f"Bearer {settings.shiftup_api_key}"},
                json=corps,
            )
        if r.status_code >= 400:
            logger.error(f"[Résultats] shiftup.bj {methode} {chemin} -> {r.status_code}: {r.text[:200]}")
            return None
        return r.json()
    except Exception as e:
        logger.error(f"[Résultats] shiftup.bj injoignable: {e}")
        return None


async def _prevenir_equipe(sou: dict, resultat: str, attributaire: str, montant, lien: Optional[str]):
    from app.services import whatsapp
    titre = "Marché gagné" if resultat == "GAGNE" else "Marché non retenu"
    montant_txt = f"{int(montant):,} FCFA".replace(",", " ") if montant else "montant non lu"
    texte = (
        f"*{titre}* — {sou.get('entrepriseNom')}\n{sou.get('objet')}\n{sou.get('autorite')}\n"
        f"Attributaire : {attributaire} ({montant_txt})" + (f"\nPV : {lien}" if lien else "")
        + "\n\nCorrection possible dans l’espace admin SHIFT UP, rubrique Résultats des dossiers."
    )
    for numero in settings.admin_phones:
        try:
            await whatsapp.send_message(numero if numero.startswith("+") else f"+{numero}", texte)
        except Exception as e:
            logger.warning(f"[Résultats] Notification WhatsApp impossible ({numero}): {e}")


async def verifier_resultats(db: AsyncSession) -> dict:
    """Tâche quotidienne. Retourne un bilan {suivies, gagnes, non_retenus}."""
    bilan = {"suivies": 0, "gagnes": 0, "non_retenus": 0}
    if not settings.shiftup_api_key:
        logger.info("[Résultats] SHIFTUP_API_KEY absente : veille des résultats désactivée")
        return bilan
    reponse = await _api("GET", "/api/tendo/soumissions")
    if not reponse:
        return bilan
    soumissions = reponse.get("soumissions", [])
    bilan["suivies"] = len(soumissions)
    if not soumissions:
        return bilan

    plus_ancien = min(datetime.fromisoformat(s["dateDepot"].replace("Z", "+00:00")) for s in soumissions)
    resultat_pubs = await db.execute(
        select(Publication).where(
            and_(
                Publication.created_at >= plus_ancien - timedelta(days=3),
                or_(
                    Publication.document_type.in_(TYPES_RESULTAT),
                    Publication.title.ilike("%attribution%"),
                ),
            )
        )
    )
    pubs = resultat_pubs.scalars().all()
    logger.info(f"[Résultats] {len(soumissions)} offres suivies, {len(pubs)} PV/avis d'attribution à examiner")

    for sou in soumissions:
        depot = datetime.fromisoformat(sou["dateDepot"].replace("Z", "+00:00"))
        candidates = []
        for pub in pubs:
            cree = pub.created_at if pub.created_at.tzinfo else pub.created_at.replace(tzinfo=timezone.utc)
            if cree < depot - timedelta(days=3):
                continue
            score = correspond_au_marche(sou, pub)
            if score >= 0.6:
                candidates.append((score, pub))
        if not candidates:
            continue
        candidates.sort(key=lambda x: x[0], reverse=True)
        pub = candidates[0][1]
        texte = _texte(pub)
        lien = pub.pdf_url

        if entreprise_designee(sou, texte):
            resultat, attributaire, montant = "GAGNE", sou["entrepriseNom"], None
            ia = await _attributaire_par_ia(sou, texte)
            if ia and ia.get("montant"):
                montant = ia["montant"]
        else:
            ia = await _attributaire_par_ia(sou, texte)
            if not ia:
                continue  # rien de sûr : on réessaiera demain
            attributaire, montant = str(ia["attributaire"])[:300], ia.get("montant")
            resultat = "GAGNE" if _meme_entreprise(sou, attributaire) else "NON_RETENU"

        corps = {"resultat": resultat, "attributaire": attributaire}
        if isinstance(montant, (int, float)) and montant > 0:
            corps["montantAttribue"] = float(montant)
        if lien and lien.startswith("http"):
            corps["preuveUrl"] = lien
        if await _api("POST", f"/api/tendo/soumissions/{sou['id']}", corps) is not None:
            bilan["gagnes" if resultat == "GAGNE" else "non_retenus"] += 1
            logger.info(f"[Résultats] {sou['entrepriseNom']} — {resultat} (pub {pub.id})")
            await _prevenir_equipe(sou, resultat, attributaire, montant, lien)

    return bilan

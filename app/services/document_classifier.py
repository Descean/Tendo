"""Service de classification IA des documents de marchés publics.

Analyse le texte extrait d'un document et le classifie automatiquement
en utilisant le lexique métier et l'IA (Groq/Gemini).
"""

import re
from typing import Optional, Tuple

from app.utils.logger import logger


# ── Lexique de classification par type de document ──────────────────

DOCUMENT_LEXICON = {
    "AAO": {
        "label": "Avis d'Appel d'Offres",
        "keywords_fr": [
            "avis d'appel d'offres", "avis d'appel d'offre",
            "appel d'offres ouvert", "appel d'offres national",
            "appel d'offres international", "appel a concurrence",
            "mise en concurrence", "avis de marche", "avis de consultation",
            "invitation a soumissionner", "avis de publicite",
            "appel d'offres restreint",
        ],
        "keywords_en": [
            "tender notice", "contract notice", "procurement notice",
            "invitation to bid", "call for tenders", "request for bids",
        ],
        "strong_signals": [
            "date limite de depot", "date limite de soumission",
            "retrait des dossiers", "caution de soumission",
            "ouverture des plis", "soumissionnaires",
        ],
    },
    "DAO": {
        "label": "Dossier d'Appel d'Offres",
        "keywords_fr": [
            "dossier d'appel d'offres", "dossier de consultation",
            "instructions aux soumissionnaires", "cahier des charges",
            "conditions particulieres", "conditions generales",
            "bordereau de prix", "detail estimatif",
            "formulaire de soumission", "acte d'engagement",
        ],
        "keywords_en": [
            "bidding documents", "tender documents",
            "solicitation documents", "instructions to bidders",
            "bill of quantities", "terms of reference",
        ],
        "strong_signals": [
            "pieces administratives", "pieces techniques",
            "pieces financieres", "criteres d'evaluation",
            "garantie de soumission", "offre technique",
            "offre financiere", "lot n",
        ],
    },
    "PV_ATTRIBUTION": {
        "label": "PV d'Attribution / Avis d'Attribution",
        "keywords_fr": [
            "proces-verbal d'attribution", "pv d'attribution",
            "avis d'attribution", "attribution provisoire",
            "attribution definitive", "resultat de l'appel d'offres",
            "titulaire du marche", "attributaire",
            "notification d'attribution",
        ],
        "keywords_en": [
            "contract award", "award notice", "recommendation for award",
            "contract award notice",
        ],
        "strong_signals": [
            "est attribue a", "a ete attribue", "montant du marche",
            "soumissionnaire retenu", "offre evaluee",
            "classement des offres",
        ],
    },
    "PV_OUVERTURE": {
        "label": "PV d'Ouverture des Offres",
        "keywords_fr": [
            "proces-verbal d'ouverture", "pv d'ouverture",
            "ouverture des offres", "ouverture des plis",
            "seance d'ouverture", "depouillement",
        ],
        "keywords_en": [
            "bid opening", "tender opening",
        ],
        "strong_signals": [
            "offres recues", "soumissionnaires presents",
            "montant de l'offre", "caution fournie",
        ],
    },
    "DECISION_ARMP": {
        "label": "Décision de l'ARMP",
        "keywords_fr": [
            "decision du conseil de regulation", "decision de l'armp",
            "decision n°", "autorite de regulation des marches publics",
            "recours", "autosaisine", "sanction",
            "exclusion de la commande publique",
        ],
        "keywords_en": [
            "regulatory decision", "appeal decision", "exclusion",
        ],
        "strong_signals": [
            "le conseil de regulation", "vu le recours",
            "vu la denonciation", "declare recevable",
            "declare irrecevable", "annulation de la procedure",
            "exclusion pour une duree",
        ],
    },
    "AMI": {
        "label": "Avis à Manifestation d'Intérêt",
        "keywords_fr": [
            "manifestation d'interet", "appel a manifestation",
            "prequalification", "preselecton", "liste restreinte",
            "expression d'interet",
        ],
        "keywords_en": [
            "expression of interest", "request for information",
            "pre-qualification", "shortlist",
        ],
        "strong_signals": [
            "sont invites a manifester", "liste restreinte",
            "candidatures", "dossier de candidature",
        ],
    },
    "RFQ": {
        "label": "Demande de Cotation / Devis",
        "keywords_fr": [
            "demande de cotation", "demande de prix",
            "demande de devis", "consultation restreinte",
            "bon de commande",
        ],
        "keywords_en": [
            "request for quotation", "rfq", "price inquiry",
        ],
        "strong_signals": [
            "priere de nous communiquer", "vos meilleurs prix",
            "devis", "cotation",
        ],
    },
    "RFP": {
        "label": "Demande de Propositions",
        "keywords_fr": [
            "demande de propositions", "appel a propositions",
            "termes de reference", "consultation",
        ],
        "keywords_en": [
            "request for proposals", "rfp", "call for proposals",
        ],
        "strong_signals": [
            "proposition technique", "proposition financiere",
            "methodologie", "approche technique",
        ],
    },
    "PPM": {
        "label": "Plan de Passation des Marchés",
        "keywords_fr": [
            "plan de passation", "plan annuel",
            "plan previsionnel", "programme de passation",
            "plan d'approvisionnement",
        ],
        "keywords_en": [
            "procurement plan", "annual procurement plan",
        ],
        "strong_signals": [
            "trimestre de lancement", "budget previsionnel",
            "mode de passation prevu",
        ],
    },
    "ADDITIF": {
        "label": "Additif / Corrigendum",
        "keywords_fr": [
            "additif", "addendum", "rectificatif", "corrigendum",
            "modification", "report de date", "prorogation",
            "prolongation de delai",
        ],
        "keywords_en": [
            "addendum", "corrigendum", "amendment", "extension",
        ],
        "strong_signals": [
            "la date limite est reportee", "nouvelle date",
            "est modifie comme suit", "lire au lieu de",
        ],
    },
    "RAPPORT_EVALUATION": {
        "label": "Rapport d'Évaluation",
        "keywords_fr": [
            "rapport d'evaluation", "rapport d'analyse des offres",
            "evaluation des offres", "grille de notation",
            "commission d'evaluation",
        ],
        "keywords_en": [
            "evaluation report", "bid evaluation",
        ],
        "strong_signals": [
            "score technique", "note financiere",
            "soumissionnaire", "elimination", "conformite",
        ],
    },
}

# Secteurs détectables
SECTOR_KEYWORDS = {
    "BTP": ["travaux", "construction", "batiment", "route", "pont", "infrastructure",
            "rehabilitation", "amenagement", "voirie", "assainissement", "genie civil"],
    "Fournitures": ["fourniture", "equipement", "materiel", "mobilier",
                    "vehicule", "informatique", "bureau", "medical"],
    "Services": ["service", "prestation", "conseil", "assistance technique",
                 "formation", "audit", "etude", "consultant"],
    "TIC": ["informatique", "numerique", "logiciel", "reseau", "fibre optique",
            "digital", "systeme d'information", "site web"],
    "Santé": ["sante", "medical", "medicament", "hopital", "vaccin",
              "pharmaceutique", "sanitaire", "clinique"],
    "Éducation": ["education", "formation", "ecole", "universite",
                  "scolaire", "enseignement"],
    "Agriculture": ["agriculture", "agricole", "irrigation", "elevage",
                    "semence", "engrais", "rural", "peche"],
    "Énergie": ["energie", "electrique", "solaire", "electrification",
                "generateur", "centrale", "petrole"],
    "Transport": ["transport", "logistique", "routier", "maritime",
                  "aerien", "portuaire"],
    "Environnement": ["environnement", "dechets", "eau potable", "recyclage",
                      "forage", "hydraulique", "assainissement"],
}

# Régions du Bénin
REGION_KEYWORDS = {
    "Cotonou": ["cotonou"],
    "Porto-Novo": ["porto-novo", "porto novo"],
    "Parakou": ["parakou"],
    "Abomey-Calavi": ["abomey-calavi", "calavi"],
    "Bohicon": ["bohicon"],
    "Natitingou": ["natitingou"],
    "Djougou": ["djougou"],
    "Lokossa": ["lokossa"],
    "Atacora": ["atacora"],
    "Alibori": ["alibori"],
    "Atlantique": ["atlantique"],
    "Borgou": ["borgou"],
    "Collines": ["collines"],
    "Couffo": ["couffo"],
    "Donga": ["donga"],
    "Littoral": ["littoral"],
    "Mono": ["mono"],
    "Oueme": ["oueme"],
    "Plateau": ["plateau"],
    "Zou": ["zou"],
    "National": ["national", "tout le benin", "ensemble du territoire"],
}


def classify_document(text: str) -> dict:
    """Classifie un document basé sur son contenu textuel.

    Utilise un système de scoring par mots-clés avec pondération.

    Returns:
        {
            "document_type": "AAO",
            "document_subtype": None,
            "confidence": 0.85,
            "label": "Avis d'Appel d'Offres",
            "sectors": ["BTP", "Fournitures"],
            "regions": ["Cotonou", "National"],
            "country": "Bénin",
        }
    """
    if not text or len(text.strip()) < 50:
        return {
            "document_type": "INCONNU",
            "document_subtype": None,
            "confidence": 0.0,
            "label": "Document non identifié",
            "sectors": [],
            "regions": [],
            "country": None,
        }

    text_lower = _normalize_text(text.lower())
    scores = {}

    for doc_type, lexicon in DOCUMENT_LEXICON.items():
        score = 0
        # Mots-clés FR (poids 2)
        for kw in lexicon["keywords_fr"]:
            if kw in text_lower:
                score += 2

        # Mots-clés EN (poids 1)
        for kw in lexicon["keywords_en"]:
            if kw in text_lower:
                score += 1

        # Signaux forts (poids 5)
        for signal in lexicon["strong_signals"]:
            if signal in text_lower:
                score += 5

        if score > 0:
            scores[doc_type] = score

    if not scores:
        return {
            "document_type": "AUTRE",
            "document_subtype": None,
            "confidence": 0.1,
            "label": "Document non classifié",
            "sectors": _detect_sectors(text_lower),
            "regions": _detect_regions(text_lower),
            "country": _detect_country(text_lower),
        }

    # Le type avec le meilleur score gagne
    best_type = max(scores, key=scores.get)
    max_score = scores[best_type]

    # Calculer la confiance (normalisé entre 0 et 1)
    # Un score de 20+ = haute confiance
    confidence = min(max_score / 20.0, 1.0)

    # Sous-type pour les décisions ARMP
    subtype = None
    if best_type == "DECISION_ARMP":
        subtype = _detect_armp_subtype(text_lower)

    return {
        "document_type": best_type,
        "document_subtype": subtype,
        "confidence": round(confidence, 2),
        "label": DOCUMENT_LEXICON[best_type]["label"],
        "sectors": _detect_sectors(text_lower),
        "regions": _detect_regions(text_lower),
        "country": _detect_country(text_lower),
    }


async def classify_with_ai(text: str, initial_classification: dict) -> dict:
    """Enrichit la classification avec l'IA pour les cas ambigus.

    Appelé uniquement si la confiance du classifieur lexical est < 0.5.
    """
    from app.services.claude import chat

    prompt = (
        "Tu es un expert des marchés publics au Bénin et en Afrique de l'Ouest.\n"
        "Analyse ce document et classifie-le.\n\n"
        f"Texte (extrait) :\n{text[:3000]}\n\n"
        "Réponds UNIQUEMENT avec un JSON valide :\n"
        '{\n'
        '  "document_type": "AAO|DAO|PV_ATTRIBUTION|PV_OUVERTURE|DECISION_ARMP|AMI|RFQ|RFP|PPM|ADDITIF|RAPPORT_EVALUATION|AUTRE",\n'
        '  "confidence": 0.0 à 1.0,\n'
        '  "objet": "résumé court de l\'objet du document",\n'
        '  "reference": "référence si trouvée ou null",\n'
        '  "deadline": "date limite si trouvée ou null",\n'
        '  "budget": "budget si trouvé ou null",\n'
        '  "autorite_contractante": "nom si trouvé ou null"\n'
        '}'
    )

    try:
        result = await chat(prompt, is_premium=True)
        # Extraire le JSON de la réponse
        import json
        json_match = re.search(r'\{[^}]+\}', result, re.DOTALL)
        if json_match:
            ai_data = json.loads(json_match.group())
            # Fusionner avec la classification initiale
            if ai_data.get("document_type") and ai_data["document_type"] != "AUTRE":
                initial_classification["document_type"] = ai_data["document_type"]
                initial_classification["confidence"] = max(
                    initial_classification["confidence"],
                    ai_data.get("confidence", 0.5),
                )
            # Ajouter les données extraites
            initial_classification["extracted_data"] = {
                k: v for k, v in ai_data.items()
                if k not in ("document_type", "confidence") and v
            }
    except Exception as e:
        logger.error(f"[Classifier] Erreur IA: {e}")

    return initial_classification


def _normalize_text(text: str) -> str:
    """Normalise le texte pour la comparaison (accents, espaces)."""
    # Remplacer les accents courants
    replacements = {
        'é': 'e', 'è': 'e', 'ê': 'e', 'ë': 'e',
        'à': 'a', 'â': 'a', 'ä': 'a',
        'ù': 'u', 'û': 'u', 'ü': 'u',
        'î': 'i', 'ï': 'i',
        'ô': 'o', 'ö': 'o',
        'ç': 'c',
        '\u2019': "'", '\u2018': "'",
        '\u201c': '"', '\u201d': '"',
        '\u2013': '-', '\u2014': '-',
        '\u00a0': ' ',  # espace insécable
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    # Nettoyer les espaces multiples
    text = re.sub(r'\s+', ' ', text)
    return text


def _detect_sectors(text_lower: str) -> list:
    """Détecte les secteurs mentionnés dans le texte."""
    found = []
    for sector, keywords in SECTOR_KEYWORDS.items():
        for kw in keywords:
            if kw in text_lower:
                if sector not in found:
                    found.append(sector)
                break
    return found


def _detect_regions(text_lower: str) -> list:
    """Détecte les régions/villes du Bénin mentionnées."""
    found = []
    for region, keywords in REGION_KEYWORDS.items():
        for kw in keywords:
            if kw in text_lower:
                if region not in found:
                    found.append(region)
                break
    return found


def _detect_country(text_lower: str) -> Optional[str]:
    """Détecte le pays principal du document."""
    country_keywords = {
        "Bénin": ["benin", "republique du benin", "cotonou", "porto-novo"],
        "Sénégal": ["senegal", "dakar"],
        "Ghana": ["ghana", "accra"],
        "Togo": ["togo", "lome"],
        "Niger": ["niger", "niamey"],
        "Burkina Faso": ["burkina", "ouagadougou"],
        "Côte d'Ivoire": ["cote d'ivoire", "abidjan", "cote d ivoire"],
        "Mali": ["mali", "bamako"],
        "UEMOA": ["uemoa", "union economique"],
        "CEDEAO": ["cedeao", "ecowas"],
    }
    for country, keywords in country_keywords.items():
        for kw in keywords:
            if kw in text_lower:
                return country
    return None


def _detect_armp_subtype(text_lower: str) -> Optional[str]:
    """Détecte le sous-type d'une décision ARMP."""
    if any(w in text_lower for w in ("recours", "recevable", "irrecevable")):
        return "recours"
    if any(w in text_lower for w in ("exclusion", "sanction", "exclue", "exclu")):
        return "sanction"
    if any(w in text_lower for w in ("autosaisine", "auto-saisine")):
        return "autosaisine"
    if any(w in text_lower for w in ("infructueux", "annulation")):
        return "annulation"
    return None

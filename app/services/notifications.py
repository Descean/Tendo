"""Service de notifications -- matching et envoi d'alertes WhatsApp.

Logique : pour chaque utilisateur actif, on cherche les publications recentes
qui n'ont PAS encore ete notifiees a cet utilisateur (via la table notifications).

Templates de messages adaptes par document_type selon la spec Tendo :
- AAO/DAO/RFQ/RFP/AMI/ADDITIF : avec delai, actions filtrees par abonnement
- PV_OUVERTURE / PV_ATTRIBUTION / AVIS_ATTRIBUTION / DECISION_ARMP :
  envoyees uniquement aux abonnes Essentiel/Premium
"""

import asyncio
import math
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, and_, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User, SubscriptionStatus
from app.models.publication import Publication
from app.models.notification import Notification
from app.services import whatsapp
from app.config import settings
from app.utils.logger import logger
from app.utils.text_quality import (
    clean_authority, deadline_from_text, is_generic_title, outdated_reason, strip_accents,
)

# ── Configuration ──
SEND_DELAY = 5
MAX_PUBLICATIONS_PER_CYCLE = 50
MAX_PER_USER = 3
PUBLICATION_AGE_DAYS = 7

# Verrou pour empecher l'execution parallele des cycles de notifications
_notification_lock = asyncio.Lock()

# Mode test : charge depuis .env (TEST_PHONE_NUMBERS=+22940809108,+22997714320)
# Liste vide = envoi a TOUS les utilisateurs actifs (mode production)
TEST_PHONE_NUMBERS = set(settings.test_phone_numbers)

# Types de docs "connaissance" : envoyees uniquement aux abonnes
KNOWLEDGE_DOCUMENT_TYPES = {"PV_ATTRIBUTION", "PV_OUVERTURE", "DECISION_ARMP", "AVIS_ATTRIBUTION"}

# Types qui ont un delai de soumission
DEADLINE_TYPES = {"AAO", "DAO", "AMI", "RFQ", "RFP", "DRP", "AON", "AOI", "AOR", "LISTE_RESTREINTE", None}

# Labels lisibles par type de document (pour le header et le champ Type)
_DOC_TYPE_LABELS = {
    "DRP": "Demande de renseignements et de prix (DRP)",
    "RFQ": "Demande de devis (RFQ)",
    "RFP": "Demande de propositions (RFP)",
    "AOI": "Appel d’offres international (AOI)",
    "AOR": "Appel d’offres restreint (AOR)",
    "AON": "Appel d’offres national (AON)",
    "AMI": "Appel à manifestation d’intérêt (AMI)",
    "AAO": "Avis d’appel à concurrence",
    "DAO": "Dossier d’appel d’offres",
    "PV_OUVERTURE": "PV d’ouverture des plis",
    "PV_ATTRIBUTION": "PV d’attribution provisoire",
    "AVIS_ATTRIBUTION": "Avis d’attribution définitive",
    "DECISION_ARMP": "Décision ARMP",
    "ADDITIF": "Addendum / Additif",
    "PPM": "Plan de passation des marchés",
    "Services": "Avis d’appel d’offres",
    "Travaux": "Avis d’appel d’offres – Travaux",
    "Fournitures": "Avis d’appel d’offres – Fournitures",
    "Prestations intellectuelles": "Avis d’appel d’offres",
}

# Headers courts pour le tag du message (max ~40 chars pour WhatsApp)
_DOC_TYPE_HEADERS = {
    "DRP": "DEMANDE DE RENSEIGNEMENTS ET DE PRIX",
    "RFQ": "DEMANDE DE DEVIS (RFQ)",
    "RFP": "DEMANDE DE PROPOSITIONS (RFP)",
    "AOI": "APPEL D’OFFRES INTERNATIONAL",
    "AOR": "APPEL D’OFFRES RESTREINT",
    "AON": "APPEL D’OFFRES NATIONAL",
    "AMI": "APPEL À MANIFESTATION D’INTÉRÊT",
    "AAO": "AVIS D’APPEL D’OFFRES",
    "DAO": "DOSSIER D’APPEL D’OFFRES",
    "Services": "AVIS D’APPEL D’OFFRES",
    "Travaux": "AVIS D’APPEL D’OFFRES – TRAVAUX",
    "Fournitures": "AVIS D’APPEL D’OFFRES – FOURNITURES",
    "Prestations intellectuelles": "AVIS D’APPEL D’OFFRES",
    "Services / Prestations intellectuelles": "AVIS D’APPEL D’OFFRES",
}


def matches_user_preferences(user: User, publication: Publication) -> bool:
    """Secteurs, régions et sources choisis par l'abonné (voir app/services/matching.py)."""
    from app.services.matching import correspond_profil
    return correspond_profil(user, publication)


def _days_remaining(deadline) -> int | None:
    """Calcule le nombre de jours restants avant la deadline.
    Retourne un entier negatif si la deadline est passee.
    """
    if not deadline:
        return None
    now = datetime.now(timezone.utc)
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    delta = deadline - now
    return math.floor(delta.total_seconds() / 86400)


def _is_expired(pub) -> bool:
    """Vrai si la publication n'est plus d'actualite.

    Avant nov. 2026 seul le champ deadline etait teste : les avis sans deadline
    parsee (ex. AMI de 2024 re-scrapes apres la remise a zero de la base)
    etaient envoyes comme [NOUVEAU]. On regarde maintenant aussi la date limite
    ecrite dans le texte, la date de publication et les annees citees.
    """
    reason = outdated_reason(
        deadline=pub.deadline,
        published_date=pub.published_date if isinstance(pub.published_date, datetime) else None,
        texts=(pub.title, pub.summary, pub.technical_summary, (pub.pdf_content or "")[:20000]),
    )
    if reason:
        logger.info(f"[Notifications] pub={pub.id} ecartee : {reason}")
        return True
    # Date limite presente seulement dans le texte : la reporter sur la fiche
    # pour l'afficher dans l'alerte (Date limite / Délai restant).
    if not pub.deadline:
        found = deadline_from_text(pub.title, pub.summary, pub.technical_summary, (pub.pdf_content or "")[:20000])
        if found:
            pub.deadline = found
    return False


def _deadline_tag(days: int | None) -> str:
    """Retourne le tag de statut du delai."""
    if days is None:
        return "[NOUVEAU]"
    if days < 0:
        return "[EXPIRE]"  # Ne devrait jamais etre affiche
    if days == 0:
        return "[DERNIER JOUR]"
    if days <= 3:
        return "[URGENT]"
    return "[NOUVEAU]"


def _user_plan(user: User) -> str:
    """Retourne le niveau d'abonnement effectif : 'gratuit', 'essentiel', 'premium'."""
    status = user.subscription_status
    if status == SubscriptionStatus.TRIAL.value:
        return "essentiel"  # Pendant l'essai, acces essentiel
    if status == SubscriptionStatus.ACTIVE.value:
        plan = (user.subscription_plan or "").lower()
        if plan == "premium":
            return "premium"
        if plan == "essentiel":
            return "essentiel"
        return "essentiel"
    return "gratuit"


# ═══════════════════════════════════════════════════════════════════
#  EXTRACTION DE DONNEES D'AFFICHAGE DEPUIS LE CONTENU PDF
# ═══════════════════════════════════════════════════════════════════

import re

def _extract_real_object(pub: Publication) -> str:
    """Extrait le vrai objet du marche depuis pdf_content ou summary.

    Les titres JNMP sont generiques ('JNMP N627 doc 10').
    Le vrai objet est dans le texte du PDF.
    """
    # Si le titre est deja bon (pas un titre generique JNMP), le garder
    title = pub.title or ""
    if not re.search(r"JNMP\s*N.?\d+.*doc\s*\d+", title, re.I):
        return title

    # Chercher dans le pdf_content
    text = pub.pdf_content or pub.summary or ""
    if not text:
        return title

    # Patterns pour extraire l'objet du marche dans les PDFs beninois
    patterns = [
        r"(?:objet|intitul[eé])\s*(?:du\s*march[eé])?\s*[:]\s*(.{20,300})",
        r"(?:pour|relatif\s+[aà])\s+(?:la\s+|le\s+|l['']\s*|les\s+)?(.{20,250}?)(?:\n|Ref|R[eé]f[eé]rence|Date|Lieu|Source)",
        r"ACCORD[\s\-]CADRE[^.]{10,200}",
        r"(?:acquisition|fourniture|construction|realisation|r[eé]alisation|recrutement|pr[eé]station)[^.]{10,200}",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.I | re.DOTALL)
        if m:
            obj = m.group(1) if m.lastindex else m.group(0)
            obj = re.sub(r"\s+", " ", obj).strip()
            # Tronquer proprement
            # Pas de troncature — WhatsApp supporte 4096 chars par message
            return obj

    # Fallback : premieres lignes significatives du contenu
    lines = [l.strip() for l in text.split("\n") if len(l.strip()) > 20]
    if lines:
        return lines[0][:200]

    return title


def _extract_real_authority(pub: Publication) -> str | None:
    """Extrait le vrai nom de l'autorite contractante (jamais un email ni un bout de menu)."""
    auth = clean_authority(pub.authority_name) or ""

    # Si l'autorite stockee est absente ou douteuse, chercher le vrai nom dans le contenu
    if not auth:
        text = pub.pdf_content or pub.summary or ""
        patterns = [
            r"(?:autorit[eé]\s*contractante|ma[iî]tre\s*d['']\s*ouvrage)\s*[:]\s*(.{5,100}?)(?:\n|T[eé]l|Email|Adresse|Ref)",
            r"(?:Mairie|Commune|Minist[eè]re|Direction|Agence|Office|PRMP)\s+(?:de\s+|du\s+|des\s+)?[A-Z][a-zA-ZéèêëàâùûôîçÉÈ\s\-/]{3,60}",
        ]
        for pat in patterns:
            m = re.search(pat, text, re.I)
            if m:
                name = m.group(1) if m.lastindex else m.group(0)
                name = clean_authority(name)
                if name:
                    return name
        return None

    return auth


def _extract_reference(pub: Publication) -> str | None:
    """Extrait la reference SIGMAP ou DAO du contenu."""
    text = pub.pdf_content or pub.summary or ""
    patterns = [
        r"(?:R[eé]f[eé]rence\s*(?:SIGMAP)?|N[°o]\s*(?:du\s*)?(?:march[eé]|dossier|DAO))\s*[:]\s*([A-Z0-9_\-/\s]{5,40})",
        r"(F[\s_][A-Z]{2,5}[\s_]\d{4,8})",  # Format SIGMAP: F_DSI_116199
    ]
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if m:
            ref = m.group(1).strip()
            return ref
    return None


# ═══════════════════════════════════════════════════════════════════
#  TEMPLATES DE MESSAGES PAR TYPE DE DOCUMENT
# ═══════════════════════════════════════════════════════════════════

def _format_deadline_date(deadline) -> str:
    """Formate la deadline en date lisible: '28 mai 2026'."""
    MOIS = [
        "", "janvier", "février", "mars", "avril", "mai", "juin",
        "juillet", "août", "septembre", "octobre", "novembre", "décembre",
    ]
    if not deadline:
        return ""
    return f"{deadline.day} {MOIS[deadline.month]} {deadline.year}"


def _build_aao_message(pub: Publication, user: User) -> tuple:
    """Avis d'Appel d'Offres / AON / AOI / DAO / RFQ / RFP / DRP.

    Format cible (Annonce 1.txt) :
      Autorite / Objet / Reference / Type / Lots / Date limite / Delai / Delai livraison
    """
    days = _days_remaining(pub.deadline)
    tag = _deadline_tag(days)
    plan = _user_plan(user)

    doc_type = pub.document_type or "AAO"

    # Detecter AMI dans le titre meme si le type est AAO
    title_lower = (pub.title or "").lower()
    if "manifestation" in title_lower or re.search(r"\bami\b", title_lower):
        header_label = "APPEL À MANIFESTATION D’INTÉRÊT"
    else:
        header_label = _DOC_TYPE_HEADERS.get(doc_type, "AVIS D’APPEL D’OFFRES")
    header = f"{tag} {header_label}"

    # Extraire les vraies donnees d'affichage
    real_authority = _extract_real_authority(pub)
    real_object = _extract_real_object(pub)
    real_ref = _extract_reference(pub)

    # Label lisible du type
    type_label = _DOC_TYPE_LABELS.get(doc_type, doc_type)

    parts = []
    if real_authority:
        parts.append(f"Autorité : {real_authority}")
    parts.append(f"Objet : {real_object}")
    if real_ref:
        parts.append(f"Référence : {real_ref}")
    # Type de document
    parts.append(f"Type : {type_label}")
    # Lots
    if pub.lots_detail:
        parts.append(f"Lots : {pub.lots_detail}")
    if pub.budget:
        parts.append(f"Budget : {pub.budget:,.0f} FCFA")
    # Date limite en format lisible
    if pub.deadline:
        parts.append(f"Date limite : {_format_deadline_date(pub.deadline)}")
    # Delai restant
    if days is not None and days >= 0:
        today_str = datetime.now(timezone.utc).strftime("%d/%m/%Y")
        parts.append(
            f"Délai : {days} jour{'s' if days != 1 else ''} restant{'s' if days != 1 else ''}"
            f" (base : {today_str})"
        )
    # Delai de livraison
    if pub.delivery_delay:
        parts.append(f"Délai de livraison : {pub.delivery_delay}")
    if pub.financing_source:
        parts.append(f"Financement : {pub.financing_source}")

    body = "\n".join(parts)

    # Boutons (max 3, titre max 20 chars)
    buttons = [{"id": f"voir_{pub.id}", "title": "Voir l’avis"}]

    if plan in ("essentiel", "premium"):
        buttons.append({"id": f"resume_{pub.id}", "title": "Résumé technique"})
    else:
        buttons.append({"id": f"upsell_resume_{pub.id}", "title": "Résumé (abonnés)"})

    buttons.append({"id": f"confier_{pub.id}", "title": "Confier à SHIFT UP"})

    return body, buttons, header


def _build_ami_message(pub: Publication, user: User) -> tuple:
    """Appel a Manifestation d'Interet / Liste restreinte."""
    days = _days_remaining(pub.deadline)
    tag = _deadline_tag(days)
    plan = _user_plan(user)

    header = f"{tag} APPEL À MANIFESTATION D’INTÉRÊT"

    real_authority = _extract_real_authority(pub)
    real_object = _extract_real_object(pub)
    real_ref = _extract_reference(pub)

    parts = []
    if real_authority:
        parts.append(f"Autorité : {real_authority}")
    parts.append(f"Objet : {real_object}")
    if real_ref:
        parts.append(f"Référence : {real_ref}")
    parts.append("Type : Appel à manifestation d’intérêt (AMI)")
    if pub.lots_detail:
        parts.append(f"Lots : {pub.lots_detail}")
    if pub.deadline:
        parts.append(f"Date limite : {_format_deadline_date(pub.deadline)}")
    if days is not None and days >= 0:
        today_str = datetime.now(timezone.utc).strftime("%d/%m/%Y")
        parts.append(
            f"Délai : {days} jour{'s' if days != 1 else ''} restant{'s' if days != 1 else ''}"
            f" (base : {today_str})"
        )
    if pub.delivery_delay:
        parts.append(f"Délai de livraison : {pub.delivery_delay}")

    body = "\n".join(parts)

    buttons = [{"id": f"voir_{pub.id}", "title": "Voir l’avis"}]
    if plan in ("essentiel", "premium"):
        buttons.append({"id": f"resume_{pub.id}", "title": "Critères sélection"})
    else:
        buttons.append({"id": f"upsell_resume_{pub.id}", "title": "Critères (abonnés)"})
    buttons.append({"id": f"confier_{pub.id}", "title": "Confier à SHIFT UP"})

    return body, buttons, header


def _build_additif_message(pub: Publication, user: User) -> tuple:
    """Addendum / Corrigendum / Modification."""
    days = _days_remaining(pub.deadline)
    plan = _user_plan(user)

    header = "[ADDENDUM]"

    real_object = _extract_real_object(pub)
    real_authority = _extract_real_authority(pub)

    parts = []
    parts.append(f"Marché concerné : {real_object}")
    if real_authority:
        parts.append(f"Autorité : {real_authority}")
    if pub.deadline:
        parts.append(f"Nouvelle date limite : {pub.deadline.strftime('%d/%m/%Y à %Hh%M')}")
    if days is not None and days >= 0:
        parts.append(f"Délai : {days} jour{'s' if days != 1 else ''} restant{'s' if days != 1 else ''}")

    body = "\n".join(parts)

    buttons = [{"id": f"voir_{pub.id}", "title": "Télécharger"}]
    if plan == "premium":
        buttons.append({"id": f"suivre_{pub.id}", "title": "MàJ mon suivi"})

    return body, buttons, header


def _build_pv_ouverture_message(pub: Publication, user: User) -> tuple:
    """Proces-verbal d'ouverture des plis."""
    plan = _user_plan(user)
    header = "[PROCÈS-VERBAL D’OUVERTURE]"

    real_object = _extract_real_object(pub)
    real_authority = _extract_real_authority(pub)

    parts = []
    parts.append(f"Marché : {real_object}")
    if real_authority:
        parts.append(f"Autorité : {real_authority}")

    body = "\n".join(parts)

    buttons = [{"id": f"voir_{pub.id}", "title": "Voir le PV"}]
    if plan == "premium":
        buttons.append({"id": f"suivre_{pub.id}", "title": "Suivre attribution"})

    return body, buttons, header


def _build_pv_attribution_message(pub: Publication, user: User) -> tuple:
    """PV d'attribution provisoire ou definitive."""
    header = "[ATTRIBUTION PROVISOIRE]"

    real_object = _extract_real_object(pub)
    real_authority = _extract_real_authority(pub)

    parts = []
    parts.append(f"Marché : {real_object}")
    if real_authority:
        parts.append(f"Autorité : {real_authority}")
    if pub.budget:
        parts.append(f"Montant : {pub.budget:,.0f} FCFA")

    body = "\n".join(parts)

    buttons = [{"id": f"voir_{pub.id}", "title": "Voir le PV"}]

    return body, buttons, header


def _build_avis_attribution_message(pub: Publication, user: User) -> tuple:
    """Avis d'attribution definitive."""
    header = "[ATTRIBUTION DÉFINITIVE]"

    real_object = _extract_real_object(pub)

    parts = []
    parts.append(f"Marché : {real_object}")
    if pub.authority_name and "@" not in (pub.authority_name or ""):
        parts.append(f"Titulaire : {pub.authority_name}")
    if pub.budget:
        parts.append(f"Montant : {pub.budget:,.0f} FCFA")

    body = "\n".join(parts)

    buttons = [{"id": f"voir_{pub.id}", "title": "Voir l’avis"}]

    return body, buttons, header


def _build_decision_armp_message(pub: Publication, user: User) -> tuple:
    """Decision ARMP (recours, sanction, exclusion)."""
    header = "[DÉCISION ARMP]"

    parts = []
    parts.append(f"Objet : {pub.title}")
    if pub.authority_name and "@" not in (pub.authority_name or ""):
        parts.append(f"Concerne : {pub.authority_name}")
    if pub.summary:
        parts.append(f"\n{pub.summary}")

    body = "\n".join(parts)

    buttons = [{"id": f"voir_{pub.id}", "title": "Lire la décision"}]

    return body, buttons, header


def _build_ppm_message(pub: Publication, user: User) -> tuple:
    """Plan de passation des marches."""
    plan = _user_plan(user)
    header = "[PLAN DE PASSATION]"

    objet = _extract_real_object(pub)
    autorite = _extract_real_authority(pub)
    ref = _extract_reference(pub)

    parts = []
    parts.append(f"Objet : {objet}")
    if autorite:
        parts.append(f"Autorité : {autorite}")
    if ref:
        parts.append(f"Ref : {ref}")
    if isinstance(pub.published_date, datetime):
        parts.append(f"Année : {pub.published_date.strftime('%Y')}")

    body = "\n".join(parts)

    buttons = [{"id": f"voir_{pub.id}", "title": "Télécharger le PPM"}]
    if plan == "premium":
        buttons.append({"id": f"suivre_{pub.id}", "title": "Notifier marchés"})

    return body, buttons, header


def _build_generic_message(pub: Publication, user: User) -> tuple:
    """Document non classifie ou type inconnu."""
    header = f"[DOCUMENT REÇU] {pub.source or ''}"

    objet = _extract_real_object(pub)
    autorite = _extract_real_authority(pub)
    ref = _extract_reference(pub)

    parts = []
    parts.append(f"Objet : {objet}")
    if autorite:
        parts.append(f"Source : {autorite}")
    if ref:
        parts.append(f"Ref : {ref}")
    if pub.summary:
        parts.append(f"\n{pub.summary}")

    body = "\n".join(parts)

    buttons = [{"id": f"voir_{pub.id}", "title": "Voir le document"}]
    buttons.append({"id": f"resume_{pub.id}", "title": "Analyser par l’IA"})

    return body, buttons, header


# Dispatch par document_type
_MESSAGE_BUILDERS = {
    "AAO": _build_aao_message,
    "AOR": _build_aao_message,
    "AON": _build_aao_message,
    "AOI": _build_aao_message,
    "DRP": _build_aao_message,
    "DAO": _build_aao_message,
    "RFQ": _build_aao_message,
    "RFP": _build_aao_message,
    "AMI": _build_ami_message,
    "LISTE_RESTREINTE": _build_ami_message,
    "ADDITIF": _build_additif_message,
    "PV_OUVERTURE": _build_pv_ouverture_message,
    "PV_ATTRIBUTION": _build_pv_attribution_message,
    "AVIS_ATTRIBUTION": _build_avis_attribution_message,
    "DECISION_ARMP": _build_decision_armp_message,
    "RAPPORT_EVALUATION": _build_pv_attribution_message,
    "PPM": _build_ppm_message,
    # Types textuels issus de gouv.bj (mappes vers AAO/AMI)
    "Services": _build_aao_message,
    "Travaux": _build_aao_message,
    "Fournitures": _build_aao_message,
    "Prestations intellectuelles": _build_aao_message,
    "Services / Prestations intellectuelles": _build_aao_message,
}

# Titres qui indiquent du contenu de navigation ou non pertinent
_BLACKLISTED_TITLE_PATTERNS = [
    "voir toutes", "statistiques", "contentieux", "denonciations",
    "referentiel des prix", "liste des dao types", "comprendre les marches",
    "comprendre les march", "0 avis", "0 plans", "0 pv",
    "marches publics",  # titre generique sans detail
]


def _build_alert_message(pub: Publication, user: User) -> tuple:
    """Dispatch vers le bon template de message selon le document_type.

    Retourne (body_text, buttons, header) pour send_interactive_buttons.
    """
    builder = _MESSAGE_BUILDERS.get(pub.document_type, _build_generic_message)
    return builder(pub, user)


# ═══════════════════════════════════════════════════════════════════
#  LOGIQUE D'ENVOI DES NOTIFICATIONS
# ═══════════════════════════════════════════════════════════════════

def _is_quality_publication(pub: Publication) -> bool:
    """Verifie qu'une publication a un contenu minimum pour etre notifiee.

    Rejette : titres trop courts, titres de navigation, pubs sans PDF ni contenu.
    """
    title = (pub.title or "").strip()

    # Titre trop court ou vide
    if len(title) < 15:
        return False

    # Titre blackliste (navigation, contenu generique) — comparaison sans accents
    title_lower = strip_accents(title).lower()
    if is_generic_title(title):
        return False
    for pattern in _BLACKLISTED_TITLE_PATTERNS:
        if pattern in title_lower and len(title_lower) < len(pattern) + 25:
            return False

    # Pas de PDF ET pas de resume/contenu substantiel → rien a montrer
    has_pdf = bool(pub.pdf_url)
    has_content = bool(pub.summary and len(pub.summary.strip()) > 50)
    has_technical = bool(pub.technical_summary and len(pub.technical_summary.strip()) > 50)
    if not has_pdf and not has_content and not has_technical:
        return False

    return True


def _should_send_to_user(user: User, pub: Publication) -> bool:
    """Decide si une publication doit etre envoyee a un utilisateur.

    Filtres :
    1. Qualite minimum (titre, PDF ou contenu)
    2. Delai non expire
    3. PV/decisions : abonnement requis
    """
    # Filtre qualite
    if not _is_quality_publication(pub):
        return False

    # Avis expire : JAMAIS envoyer
    if _is_expired(pub):
        return False

    # Documents connaissance : abonnement requis
    if pub.document_type in KNOWLEDGE_DOCUMENT_TYPES:
        plan = _user_plan(user)
        if plan == "gratuit":
            return False

    return matches_user_preferences(user, pub)


MAX_RECAP = 10  # avis par récapitulatif (limite d'une liste WhatsApp)
MODELE_ALERTE = "tendo_alerte_avis"  # modèle approuvé par Meta, pour écrire hors de la fenêtre de 24 h


def _cle_urgence(pub: Publication):
    d = pub.deadline
    if d is not None and d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return (d is None, d or datetime.max.replace(tzinfo=timezone.utc), -(pub.id or 0))


def _court(t: str, n: int) -> str:
    t = re.sub(r"\s+", " ", t or "").strip()
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


def _ligne_recap(pub: Publication) -> dict:
    """Ligne de liste WhatsApp : titre ≤ 24, description ≤ 72 caractères."""
    days = _days_remaining(pub.deadline)
    delai = "date limite non précisée" if days is None else ("dernier jour" if days == 0 else f"J-{days}")
    autorite = _extract_real_authority(pub) or (pub.source or "")
    return {
        "id": f"voir_{pub.id}",
        "title": _court(_extract_real_object(pub), 24),
        "description": _court(f"{delai} · {autorite}", 72),
    }


def _hors_fenetre(err: Exception) -> bool:
    e = str(err)
    return "131047" in e or "re-engagement" in e.lower() or "window" in e.lower()


async def _envoyer_a_utilisateur(user: User, pubs: list) -> list:
    """Un avis : message à boutons. Plusieurs : un seul récapitulatif en liste.
    Hors de la fenêtre de 24 h : modèle Meta « tendo_alerte_avis » (bouton « Voir mes avis »).
    Retourne les publications effectivement annoncées."""
    try:
        if len(pubs) == 1:
            pub = pubs[0]
            body, buttons, header = _build_alert_message(pub, user)
            if _user_plan(user) == "gratuit":
                body += "\n\nPassez à l’abonnement pour débloquer toutes les actions."
            await whatsapp.send_interactive_buttons(
                user.phone_number, body, buttons, header=header[:60], footer="tendo.shiftup.bj",
            )
        else:
            await whatsapp.send_interactive_list(
                user.phone_number,
                f"{len(pubs)} avis ouverts correspondent à votre activité, du plus urgent au moins urgent. "
                "Choisissez-en un pour recevoir le dossier et son résumé.",
                "Voir les avis",
                [{"title": "Avis du jour", "rows": [_ligne_recap(p) for p in pubs]}],
                header="Vos avis du jour",
                footer="tendo.shiftup.bj",
            )
        return pubs
    except Exception as e:
        if not _hors_fenetre(e):
            raise
    # L'abonné n'a pas écrit depuis plus de 24 h : seul un modèle approuvé passe
    premier = pubs[0]
    date_limite = _format_deadline_date(premier.deadline) if premier.deadline else "non précisée"
    await whatsapp.send_template_message(
        user.phone_number,
        MODELE_ALERTE,
        language="fr",
        components=[
            {"type": "body", "parameters": [
                {"type": "text", "text": str(len(pubs))},
                {"type": "text", "text": _court(_extract_real_object(premier), 120)},
                {"type": "text", "text": date_limite},
            ]},
            {"type": "button", "sub_type": "quick_reply", "index": "0",
             "parameters": [{"type": "payload", "payload": "recap_avis"}]},
        ],
    )
    return pubs


async def recap_recent(db: AsyncSession, user: User) -> None:
    """Renvoie le récapitulatif des avis annoncés ces 3 derniers jours (bouton « Voir mes avis »)."""
    depuis = datetime.now(timezone.utc) - timedelta(days=3)
    res = await db.execute(
        select(Publication)
        .join(Notification, Notification.publication_id == Publication.id)
        .where(and_(Notification.user_id == user.id, Notification.sent_at >= depuis))
    )
    pubs = [p for p in res.scalars().unique().all() if not _is_expired(p)]
    if not pubs:
        await whatsapp.send_message(user.phone_number, "Aucun avis ouvert pour le moment. Les prochains arrivent demain matin.")
        return
    pubs.sort(key=_cle_urgence)
    pubs = pubs[:MAX_RECAP]
    if len(pubs) == 1:
        body, buttons, header = _build_alert_message(pubs[0], user)
        await whatsapp.send_interactive_buttons(user.phone_number, body, buttons, header=header[:60], footer="tendo.shiftup.bj")
    else:
        await whatsapp.send_interactive_list(
            user.phone_number,
            f"{len(pubs)} avis ouverts vous attendent, du plus urgent au moins urgent.",
            "Voir les avis",
            [{"title": "Avis récents", "rows": [_ligne_recap(p) for p in pubs]}],
            header="Vos avis",
            footer="tendo.shiftup.bj",
        )


async def process_new_publications(db: AsyncSession) -> int:
    """Envoie des notifications aux utilisateurs pour les publications non encore notifiees."""
    if _notification_lock.locked():
        logger.warning("[Notifications] Cycle deja en cours, skip")
        return 0
    async with _notification_lock:
        return await _process_new_publications_inner(db)


async def _process_new_publications_inner(db: AsyncSession) -> int:
    """Logique interne d'envoi de notifications (protegee par le lock)."""
    # Utilisateurs actifs
    query = select(User).where(
        and_(
            User.is_active == True,
            User.subscription_status.in_([
                SubscriptionStatus.TRIAL.value,
                SubscriptionStatus.ACTIVE.value,
            ]),
        )
    )
    # Mode test : filtrer par numeros de telephone
    if TEST_PHONE_NUMBERS:
        query = query.where(User.phone_number.in_(TEST_PHONE_NUMBERS))
        logger.info(f"[Notifications] MODE TEST : envoi limite a {len(TEST_PHONE_NUMBERS)} numeros")

    result = await db.execute(query)
    users = result.scalars().all()

    if not users:
        logger.info("[Notifications] Aucun utilisateur actif")
        return 0

    # Publications recentes, exclure les journaux JNMP parents
    cutoff = datetime.now(timezone.utc) - timedelta(days=PUBLICATION_AGE_DAYS)
    result = await db.execute(
        select(Publication)
        .where(
            and_(
                Publication.created_at >= cutoff,
                # Exclure les JNMP parents (journaux complets segmentes)
                ~and_(
                    Publication.source == "JNMP",
                    Publication.html_content.like("[SEGMENTE:%"),
                ),
            )
        )
        .order_by(Publication.created_at.desc())
        .limit(MAX_PUBLICATIONS_PER_CYCLE)
    )
    publications = result.scalars().all()

    if not publications:
        logger.info("Aucune publication recente a notifier")
        return 0

    logger.info(
        f"[Notifications] {len(publications)} publications, {len(users)} utilisateurs"
    )

    sent_count = 0

    for user in users:
        # IDs deja notifies
        notified_result = await db.execute(
            select(Notification.publication_id).where(
                Notification.user_id == user.id
            )
        )
        already_notified = set(notified_result.scalars().all())

        a_envoyer = [
            pub for pub in publications
            if pub.id not in already_notified and _should_send_to_user(user, pub)
        ]
        if not a_envoyer:
            continue
        # Les plus urgents d'abord (date limite la plus proche), puis les plus recents
        a_envoyer.sort(key=_cle_urgence)
        a_envoyer = a_envoyer[:MAX_RECAP]

        try:
            envoyes = await _envoyer_a_utilisateur(user, a_envoyer)
        except Exception as e:
            err_str = str(e)
            logger.error(f"Erreur envoi user={user.id}: {e}")
            if "rate limit" in err_str.lower() or "131056" in err_str:
                logger.warning("[Notifications] Rate limit, pause 30s")
                await asyncio.sleep(30)
            continue

        for pub in envoyes:
            db.add(Notification(user_id=user.id, publication_id=pub.id))
        sent_count += len(envoyes)
        await db.commit()
        await asyncio.sleep(SEND_DELAY)

    logger.info(f"[Notifications] Notifications envoyees: {sent_count}")
    return sent_count

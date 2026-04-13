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
from app.utils.logger import logger

# ── Configuration ──
SEND_DELAY = 5
MAX_PUBLICATIONS_PER_CYCLE = 50
MAX_PER_USER = 3
PUBLICATION_AGE_DAYS = 7

# Mode test : limiter les envois a ces numeros uniquement (vide = tous)
TEST_PHONE_NUMBERS = {
    "+22940809108",
    "+2290140809108",
    "+22997714320",
    "+2290197714320",
    "+22870219235",
}

# Types de docs "connaissance" : envoyees uniquement aux abonnes
KNOWLEDGE_DOCUMENT_TYPES = {"PV_ATTRIBUTION", "PV_OUVERTURE", "DECISION_ARMP", "AVIS_ATTRIBUTION"}

# Types qui ont un delai de soumission
DEADLINE_TYPES = {"AAO", "DAO", "AMI", "RFQ", "RFP", "LISTE_RESTREINTE", None}


def matches_user_preferences(user: User, publication: Publication) -> bool:
    """SUSPENDU : retourne toujours True.
    Le matching sera reactive plus tard."""
    return True


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
    """Verifie si une publication a une deadline passee."""
    if not pub.deadline:
        return False
    days = _days_remaining(pub.deadline)
    return days is not None and days < 0


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
    """Extrait le vrai nom de l'autorite contractante, pas l'email."""
    auth = pub.authority_name or ""

    # Si c'est un email, chercher le vrai nom dans le contenu
    if "@" in auth or not auth or len(auth) < 5:
        text = pub.pdf_content or pub.summary or ""
        patterns = [
            r"(?:autorit[eé]\s*contractante|ma[iî]tre\s*d['']\s*ouvrage)\s*[:]\s*(.{5,100}?)(?:\n|T[eé]l|Email|Adresse|Ref)",
            r"(?:Mairie|Commune|Minist[eè]re|Direction|Agence|Office|PRMP)\s+(?:de\s+|du\s+|des\s+)?[A-Z][a-zA-ZéèêëàâùûôîçÉÈ\s\-/]{3,60}",
        ]
        for pat in patterns:
            m = re.search(pat, text, re.I)
            if m:
                name = m.group(1) if m.lastindex else m.group(0)
                name = re.sub(r"\s+", " ", name).strip()
                if len(name) > 5 and "@" not in name:
                    return name

    # Si l'autorite est propre (pas un email), la garder
    if auth and "@" not in auth and len(auth) > 5:
        return auth

    return None


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

def _build_aao_message(pub: Publication, user: User) -> tuple:
    """Avis d'Appel d'Offres / AON / AOI / DAO / RFQ / RFP."""
    days = _days_remaining(pub.deadline)
    tag = _deadline_tag(days)
    plan = _user_plan(user)

    # Header — detecter le type reel a partir du document_type et du titre
    type_labels = {
        "AAO": "AVIS D APPEL D OFFRES",
        "AOR": "APPEL D OFFRES RESTREINT",
        "DAO": "DOSSIER D APPEL D OFFRES",
        "RFQ": "DEMANDE DE DEVIS (RFQ)",
        "RFP": "DEMANDE DE PROPOSITIONS (RFP)",
        "Services": "AVIS D APPEL D OFFRES",
        "Travaux": "AVIS D APPEL D OFFRES - TRAVAUX",
        "Fournitures": "AVIS D APPEL D OFFRES - FOURNITURES",
        "Prestations intellectuelles": "AVIS D APPEL D OFFRES",
        "Services / Prestations intellectuelles": "AVIS D APPEL D OFFRES",
    }
    doc_type = pub.document_type or "AAO"
    # Detecter AMI dans le titre meme si le type est AAO
    title_lower = (pub.title or "").lower()
    if "manifestation" in title_lower or "ami" in title_lower:
        type_label = "APPEL A MANIFESTATION D INTERET"
    else:
        type_label = type_labels.get(doc_type, "AVIS D APPEL D OFFRES")
    header = f"{tag} {type_label}"

    # Extraire les vraies donnees d'affichage
    real_authority = _extract_real_authority(pub)
    real_object = _extract_real_object(pub)
    real_ref = _extract_reference(pub)

    parts = []
    if real_authority:
        parts.append(f"Autorite : {real_authority}")
    parts.append(f"Objet : {real_object}")
    if real_ref:
        parts.append(f"Ref : {real_ref}")
    if pub.budget:
        parts.append(f"Budget : {pub.budget:,.0f} FCFA")
    if pub.deadline:
        parts.append(f"Date limite : {pub.deadline.strftime('%d/%m/%Y a %Hh%M')}")
    if days is not None and days >= 0:
        parts.append(f"Delai : {days} jour{'s' if days != 1 else ''} restant{'s' if days != 1 else ''}")
    if pub.financing_source:
        parts.append(f"Financement : {pub.financing_source}")

    body = "\n".join(parts)

    # Boutons (max 3, titre max 20 chars)
    buttons = [{"id": f"voir_{pub.id}", "title": "Voir l avis"}]

    if plan in ("essentiel", "premium"):
        buttons.append({"id": f"resume_{pub.id}", "title": "Resume technique"})
    else:
        buttons.append({"id": f"upsell_resume_{pub.id}", "title": "Resume (abonnez)"})

    if plan == "premium":
        buttons.append({"id": f"demander_{pub.id}", "title": "Demander dossier"})
    else:
        buttons.append({"id": f"upsell_dossier_{pub.id}", "title": "Dossier (abonnez)"})

    return body, buttons, header


def _build_ami_message(pub: Publication, user: User) -> tuple:
    """Appel a Manifestation d'Interet / Liste restreinte."""
    days = _days_remaining(pub.deadline)
    tag = _deadline_tag(days)
    plan = _user_plan(user)

    header = f"{tag} APPEL A MANIFESTATION D INTERET"

    real_authority = _extract_real_authority(pub)
    real_object = _extract_real_object(pub)

    parts = []
    if real_authority:
        parts.append(f"Autorite : {real_authority}")
    parts.append(f"Objet : {real_object}")
    if pub.deadline:
        parts.append(f"Date limite : {pub.deadline.strftime('%d/%m/%Y a %Hh%M')}")
    if days is not None and days >= 0:
        parts.append(f"Delai : {days} jour{'s' if days != 1 else ''} restant{'s' if days != 1 else ''}")

    body = "\n".join(parts)

    buttons = [{"id": f"voir_{pub.id}", "title": "Voir l avis"}]
    if plan in ("essentiel", "premium"):
        buttons.append({"id": f"resume_{pub.id}", "title": "Criteres selection"})
    else:
        buttons.append({"id": f"upsell_resume_{pub.id}", "title": "Criteres (abonnez)"})

    return body, buttons, header


def _build_additif_message(pub: Publication, user: User) -> tuple:
    """Addendum / Corrigendum / Modification."""
    days = _days_remaining(pub.deadline)
    plan = _user_plan(user)

    header = "[ADDENDUM]"

    real_object = _extract_real_object(pub)
    real_authority = _extract_real_authority(pub)

    parts = []
    parts.append(f"Marche concerne : {real_object}")
    if real_authority:
        parts.append(f"Autorite : {real_authority}")
    if pub.deadline:
        parts.append(f"Nouvelle date limite : {pub.deadline.strftime('%d/%m/%Y a %Hh%M')}")
    if days is not None and days >= 0:
        parts.append(f"Delai : {days} jour{'s' if days != 1 else ''} restant{'s' if days != 1 else ''}")

    body = "\n".join(parts)

    buttons = [{"id": f"voir_{pub.id}", "title": "Telecharger"}]
    if plan == "premium":
        buttons.append({"id": f"suivre_{pub.id}", "title": "Maj mon suivi"})

    return body, buttons, header


def _build_pv_ouverture_message(pub: Publication, user: User) -> tuple:
    """Proces-verbal d'ouverture des plis."""
    plan = _user_plan(user)
    header = "[PROCES VERBAL D OUVERTURE]"

    real_object = _extract_real_object(pub)
    real_authority = _extract_real_authority(pub)

    parts = []
    parts.append(f"Marche : {real_object}")
    if real_authority:
        parts.append(f"Autorite : {real_authority}")

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
    parts.append(f"Marche : {real_object}")
    if real_authority:
        parts.append(f"Autorite : {real_authority}")
    if pub.budget:
        parts.append(f"Montant : {pub.budget:,.0f} FCFA")

    body = "\n".join(parts)

    buttons = [{"id": f"voir_{pub.id}", "title": "Voir le PV"}]

    return body, buttons, header


def _build_avis_attribution_message(pub: Publication, user: User) -> tuple:
    """Avis d'attribution definitive."""
    header = "[ATTRIBUTION DEFINITIVE]"

    real_object = _extract_real_object(pub)

    parts = []
    parts.append(f"Marche : {real_object}")
    if pub.authority_name and "@" not in (pub.authority_name or ""):
        parts.append(f"Titulaire : {pub.authority_name}")
    if pub.budget:
        parts.append(f"Montant : {pub.budget:,.0f} FCFA")

    body = "\n".join(parts)

    buttons = [{"id": f"voir_{pub.id}", "title": "Voir l avis"}]

    return body, buttons, header


def _build_decision_armp_message(pub: Publication, user: User) -> tuple:
    """Decision ARMP (recours, sanction, exclusion)."""
    header = "[DECISION ARMP]"

    parts = []
    parts.append(f"Objet : {pub.title}")
    if pub.authority_name and "@" not in (pub.authority_name or ""):
        parts.append(f"Concerne : {pub.authority_name}")
    if pub.summary:
        parts.append(f"\n{pub.summary}")

    body = "\n".join(parts)

    buttons = [{"id": f"voir_{pub.id}", "title": "Lire la decision"}]

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
        parts.append(f"Autorite : {autorite}")
    if ref:
        parts.append(f"Ref : {ref}")
    if pub.published_at:
        parts.append(f"Annee : {pub.published_at.strftime('%Y')}")

    body = "\n".join(parts)

    buttons = [{"id": f"voir_{pub.id}", "title": "Telecharger le PPM"}]
    if plan == "premium":
        buttons.append({"id": f"suivre_{pub.id}", "title": "Notifier marches"})

    return body, buttons, header


def _build_generic_message(pub: Publication, user: User) -> tuple:
    """Document non classifie ou type inconnu."""
    header = f"[DOCUMENT RECU] {pub.source or ''}"

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
    buttons.append({"id": f"resume_{pub.id}", "title": "Analyser par IA"})

    return body, buttons, header


# Dispatch par document_type
_MESSAGE_BUILDERS = {
    "AAO": _build_aao_message,
    "AOR": _build_aao_message,
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

    # Titre blackliste (navigation, contenu generique)
    title_lower = title.lower()
    for pattern in _BLACKLISTED_TITLE_PATTERNS:
        if pattern in title_lower:
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


async def process_new_publications(db: AsyncSession) -> int:
    """Envoie des notifications aux utilisateurs pour les publications non encore notifiees."""
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
        user_sent = 0

        # IDs deja notifies
        notified_result = await db.execute(
            select(Notification.publication_id).where(
                Notification.user_id == user.id
            )
        )
        already_notified = set(notified_result.scalars().all())

        for pub in publications:
            if user_sent >= MAX_PER_USER:
                break

            if pub.id in already_notified:
                continue

            if not _should_send_to_user(user, pub):
                continue

            # Construire le message adapte au type
            body, buttons, header = _build_alert_message(pub, user)

            # Ajouter l'invitation a s'abonner pour les gratuits
            plan = _user_plan(user)
            footer = "tendo.shiftup.bj"
            if plan == "gratuit":
                body += "\n\nPassez a l abonnement pour debloquer toutes les actions."

            try:
                await whatsapp.send_interactive_buttons(
                    user.phone_number, body, buttons,
                    header=header, footer=footer,
                )

                notification = Notification(
                    user_id=user.id,
                    publication_id=pub.id,
                )
                db.add(notification)
                sent_count += 1
                user_sent += 1

                await asyncio.sleep(SEND_DELAY)

            except Exception as e:
                err_str = str(e)
                if "131047" in err_str or "window" in err_str.lower():
                    try:
                        await whatsapp.send_proactive_message(user.phone_number, body)
                        notification = Notification(user_id=user.id, publication_id=pub.id)
                        db.add(notification)
                        sent_count += 1
                        user_sent += 1
                        await asyncio.sleep(SEND_DELAY)
                        continue
                    except Exception:
                        pass
                logger.error(f"Erreur envoi user={user.id} pub={pub.id}: {e}")
                if "rate limit" in err_str.lower() or "131056" in err_str:
                    logger.warning("[Notifications] Rate limit, pause 30s")
                    await asyncio.sleep(30)
                    break

        if user_sent > 0:
            await db.commit()

    logger.info(f"[Notifications] Notifications envoyees: {sent_count}")
    return sent_count

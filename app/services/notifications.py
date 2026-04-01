"""Service de notifications -- matching et envoi d'alertes WhatsApp + email.

Logique : pour chaque utilisateur actif, on cherche les publications recentes
qui n'ont PAS encore ete notifiees a cet utilisateur (via la table notifications).
Cela permet aux nouveaux utilisateurs de recevoir les AO existants et aux
anciennes publications d'etre re-envoyees si elles n'ont pas ete notifiees.
"""

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, and_, func, exists
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User, SubscriptionStatus
from app.models.publication import Publication
from app.models.notification import Notification
from app.services import whatsapp
from app.utils.logger import logger

# Rate limiting
SEND_DELAY = 3
# Max publications par cycle
MAX_PUBLICATIONS_PER_CYCLE = 15
# Max notifications par utilisateur par cycle
MAX_PER_USER = 5
# Ne notifier que les publications des N derniers jours
PUBLICATION_AGE_DAYS = 7
# Types de documents qui sont de la connaissance (pas de notification)
KNOWLEDGE_DOCUMENT_TYPES = {"PV_ATTRIBUTION", "PV_OUVERTURE", "DECISION_ARMP"}


def matches_user_preferences(user: User, publication: Publication) -> bool:
    """Verifie si une publication correspond aux preferences d'un utilisateur."""
    if not user.sectors and not user.regions and not user.preferred_sources:
        return True

    if user.sectors and publication.sectors:
        if any(s in user.sectors for s in publication.sectors):
            return True

    if user.regions and publication.regions:
        if any(r in user.regions for r in publication.regions):
            return True

    if user.preferred_sources:
        if publication.source in user.preferred_sources:
            return True

    if user.sectors or user.regions or user.preferred_sources:
        return False

    return True


async def process_new_publications(db: AsyncSession) -> int:
    """Envoie des notifications aux utilisateurs pour les publications non encore notifiees.

    Pour chaque utilisateur actif, on selectionne les publications recentes
    qui n'ont pas encore fait l'objet d'une notification pour cet utilisateur.
    """
    # Utilisateurs actifs
    result = await db.execute(
        select(User).where(
            and_(
                User.is_active == True,
                User.subscription_status.in_([
                    SubscriptionStatus.TRIAL.value,
                    SubscriptionStatus.ACTIVE.value,
                ]),
            )
        )
    )
    users = result.scalars().all()

    if not users:
        logger.info("[Notifications] Aucun utilisateur actif")
        return 0

    # Publications recentes (derniers N jours) avec deadline future ou sans deadline
    cutoff = datetime.now(timezone.utc) - timedelta(days=PUBLICATION_AGE_DAYS)
    result = await db.execute(
        select(Publication)
        .where(
            and_(
                Publication.created_at >= cutoff,
                # Exclure les publications JNMP parent (journaux complets)
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

        # Recuperer les IDs deja notifies pour cet utilisateur
        notified_result = await db.execute(
            select(Notification.publication_id).where(
                Notification.user_id == user.id
            )
        )
        already_notified = set(notified_result.scalars().all())

        for pub in publications:
            if user_sent >= MAX_PER_USER:
                break

            # Deja notifie ?
            if pub.id in already_notified:
                continue

            # Exclure PV/Decisions (connaissance, pas notification)
            if pub.document_type in KNOWLEDGE_DOCUMENT_TYPES:
                continue

            # Match preferences ?
            if not matches_user_preferences(user, pub):
                continue

            # Construire et envoyer avec boutons interactifs
            body, buttons, header = _build_alert_message(pub)

            try:
                await whatsapp.send_interactive_buttons(
                    user.phone_number, body, buttons, header=header,
                    footer="Tendo - Veille Marchés Publics",
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
                logger.error(f"Erreur envoi user={user.id} pub={pub.id}: {e}")
                if "rate limit" in str(e).lower() or "131056" in str(e):
                    logger.warning("[Notifications] Rate limit, pause 30s")
                    await asyncio.sleep(30)
                    break

        if user_sent > 0:
            await db.commit()

    logger.info(f"[Notifications] Notifications envoyees: {sent_count}")
    return sent_count


def _build_alert_message(publication: Publication) -> tuple:
    """Construit le message d'alerte WhatsApp structuré + boutons.

    Retourne (body_text, buttons, header) pour send_interactive_buttons.
    """
    source_label = publication.source or "TENDO"
    category_label = publication.category or "Marché"

    header = f"{source_label} | {category_label}"

    parts = []

    # Autorité contractante
    if publication.authority_name:
        parts.append(f"Autorité: {publication.authority_name}")

    # Objet du marché (titre)
    parts.append(f"Objet: {publication.title}")

    # Référence
    parts.append(f"Réf: {publication.reference}")

    # Type de marché / document
    if publication.document_type:
        parts.append(f"Type: {publication.document_type}")

    # Montant garantie de soumission
    if hasattr(publication, 'guarantee_amount') and publication.guarantee_amount:
        parts.append(f"Garantie: {publication.guarantee_amount:,.0f} FCFA")
    elif publication.budget:
        parts.append(f"Budget: {publication.budget:,.0f} FCFA")

    # Date de dépôt
    if publication.deadline:
        parts.append(f"Date limite: {publication.deadline.strftime('%d/%m/%Y à %H:%M')}")

    # Source de financement
    if publication.financing_source:
        parts.append(f"Financement: {publication.financing_source}")

    body = "\n".join(parts)

    # Boutons interactifs (max 3, titre max 20 chars)
    buttons = [
        {"id": f"voir_{publication.id}", "title": "Voir le dossier"},
        {"id": f"savoir_{publication.id}", "title": "En savoir plus"},
        {"id": f"demander_{publication.id}", "title": "Demander dossier"},
    ]

    return body, buttons, header

"""Service de notifications – matching et envoi d'alertes WhatsApp."""

from typing import List

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User, SubscriptionStatus
from app.models.publication import Publication
from app.models.notification import Notification
from app.services import whatsapp, claude
from app.utils.logger import logger


def matches_user_preferences(user: User, publication: Publication) -> bool:
    """Vérifie si une publication correspond aux préférences d'un utilisateur."""
    # Si l'utilisateur n'a pas de préférences, il reçoit tout
    if not user.sectors and not user.regions and not user.preferred_sources:
        return True

    # Vérifier les secteurs
    if user.sectors and publication.sectors:
        if any(s in user.sectors for s in publication.sectors):
            return True

    # Vérifier les régions
    if user.regions and publication.regions:
        if any(r in user.regions for r in publication.regions):
            return True

    # Vérifier les sources préférées
    if user.preferred_sources:
        if publication.source in user.preferred_sources:
            return True

    # Si l'utilisateur a des critères mais aucun ne matche
    if user.sectors or user.regions or user.preferred_sources:
        return False

    return True


async def process_new_publications(db: AsyncSession) -> int:
    """Traite les publications non envoyées et envoie les alertes correspondantes.

    Returns:
        Nombre de notifications envoyées.
    """
    # Récupérer les publications non traitées
    result = await db.execute(
        select(Publication).where(Publication.is_processed == False)
    )
    publications = result.scalars().all()

    if not publications:
        logger.info("Aucune nouvelle publication à traiter")
        return 0

    # Récupérer les utilisateurs actifs avec abonnement valide
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

    sent_count = 0

    for publication in publications:
        # Résumer la publication via Claude
        summary = await claude.summarize_publication(
            publication.title,
            publication.summary or publication.html_content or "",
        )

        for user in users:
            if not matches_user_preferences(user, publication):
                continue

            # Construire le message d'alerte
            message = _build_alert_message(publication, summary)

            try:
                await whatsapp.send_message(user.phone_number, message)

                # Enregistrer la notification
                notification = Notification(
                    user_id=user.id,
                    publication_id=publication.id,
                )
                db.add(notification)
                sent_count += 1

            except Exception as e:
                logger.error(
                    f"Erreur envoi alerte user={user.id} pub={publication.id}: {e}"
                )

        # Marquer la publication comme traitée
        publication.is_processed = True

    await db.commit()
    logger.info(f"Notifications envoyées: {sent_count}")
    return sent_count


def _build_alert_message(publication: Publication, summary: str) -> str:
    """Construit le message d'alerte WhatsApp."""
    parts = [f"🔔 *Nouvel Appel d'Offres*\n"]
    parts.append(f"📌 *{publication.title}*\n")
    parts.append(f"📎 Réf: {publication.reference}")
    parts.append(f"🏢 Source: {publication.source}")

    if publication.deadline:
        parts.append(f"⏰ Date limite: {publication.deadline.strftime('%d/%m/%Y')}")
    if publication.budget:
        parts.append(f"💰 Budget: {publication.budget:,.0f} FCFA")

    parts.append(f"\n📝 {summary}")

    if publication.pdf_url:
        parts.append(f"\n📄 Document: {publication.pdf_url}")

    parts.append(f"\n💬 Tapez */demander_dossier {publication.reference}* pour obtenir le dossier")

    return "\n".join(parts)

"""Service de notifications -- matching et envoi d'alertes WhatsApp + email.

Gere le rate limiting pour respecter :
- Groq : 30 requetes/min
- WhatsApp : limite par paire (expediteur/destinataire)
"""

import asyncio
from typing import List

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User, SubscriptionStatus
from app.models.publication import Publication
from app.models.notification import Notification
from app.services import whatsapp, claude
from app.utils.logger import logger

# Delai entre chaque envoi WhatsApp (secondes) pour eviter le rate limit
SEND_DELAY = 3
# Nombre max de publications par cycle de notification
MAX_PUBLICATIONS_PER_CYCLE = 20
# Nombre max de notifications par utilisateur par cycle
MAX_PER_USER_PER_CYCLE = 10


def matches_user_preferences(user: User, publication: Publication) -> bool:
    """Verifie si une publication correspond aux preferences d'un utilisateur."""
    # Si l'utilisateur n'a pas de preferences, il recoit tout
    if not user.sectors and not user.regions and not user.preferred_sources:
        return True

    # Verifier les secteurs
    if user.sectors and publication.sectors:
        if any(s in user.sectors for s in publication.sectors):
            return True

    # Verifier les regions
    if user.regions and publication.regions:
        if any(r in user.regions for r in publication.regions):
            return True

    # Verifier les sources preferees
    if user.preferred_sources:
        if publication.source in user.preferred_sources:
            return True

    # Si l'utilisateur a des criteres mais aucun ne matche
    if user.sectors or user.regions or user.preferred_sources:
        return False

    return True


async def process_new_publications(db: AsyncSession) -> int:
    """Traite les publications non envoyees et envoie les alertes correspondantes.

    Applique un rate limiting strict pour eviter de saturer Groq et WhatsApp.

    Returns:
        Nombre de notifications envoyees.
    """
    # Recuperer les publications non traitees (limitees pour eviter les floods)
    result = await db.execute(
        select(Publication)
        .where(Publication.is_processed == False)
        .order_by(Publication.created_at.desc())
        .limit(MAX_PUBLICATIONS_PER_CYCLE)
    )
    publications = result.scalars().all()

    if not publications:
        logger.info("Aucune nouvelle publication a traiter")
        return 0

    logger.info(f"[Notifications] {len(publications)} publications a traiter")

    # Recuperer les utilisateurs actifs avec abonnement valide
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
        # Marquer quand meme comme traitees
        for pub in publications:
            pub.is_processed = True
        await db.commit()
        return 0

    sent_count = 0
    # Compteur par utilisateur pour limiter le flood
    user_send_counts = {user.id: 0 for user in users}

    for publication in publications:
        # Utiliser le resume existant au lieu d'appeler l'IA
        # On ne fait appel a l'IA que si le resume est vide
        summary = publication.summary or ""
        if not summary.strip() and (publication.html_content or publication.title):
            try:
                summary = await claude.summarize_publication(
                    publication.title,
                    publication.html_content or "",
                )
                # Attendre un peu apres l'appel IA pour respecter les rate limits
                await asyncio.sleep(2)
            except Exception as e:
                logger.warning(f"[Notifications] Resume IA echoue pour pub={publication.id}: {e}")
                summary = publication.title  # Fallback sur le titre

        for user in users:
            # Verifier la limite par utilisateur
            if user_send_counts.get(user.id, 0) >= MAX_PER_USER_PER_CYCLE:
                continue

            if not matches_user_preferences(user, publication):
                continue

            # Construire le message d'alerte
            message = _build_alert_message(publication, summary)

            try:
                await whatsapp.send_message(user.phone_number, message)

                # Envoyer aussi par email si l'utilisateur a une adresse
                if user.email_address:
                    try:
                        await send_email_notification(
                            user_email=user.email_address,
                            user_name=user.name or "",
                            publication=publication,
                            summary=summary,
                        )
                    except Exception as email_err:
                        logger.warning(f"[Notifications] Email echoue user={user.id}: {email_err}")

                # Enregistrer la notification
                notification = Notification(
                    user_id=user.id,
                    publication_id=publication.id,
                )
                db.add(notification)
                sent_count += 1
                user_send_counts[user.id] = user_send_counts.get(user.id, 0) + 1

                # Delai entre chaque envoi pour respecter le rate limit WhatsApp
                await asyncio.sleep(SEND_DELAY)

            except Exception as e:
                logger.error(
                    f"Erreur envoi alerte user={user.id} pub={publication.id}: {e}"
                )
                # Si c'est un rate limit, attendre plus longtemps
                if "rate limit" in str(e).lower() or "131056" in str(e):
                    logger.warning("[Notifications] Rate limit detecte, pause de 30s...")
                    await asyncio.sleep(30)

        # Marquer la publication comme traitee
        publication.is_processed = True
        # Commit intermediaire pour ne pas perdre la progression
        await db.commit()

    logger.info(f"[Notifications] Notifications envoyees: {sent_count}")
    return sent_count


def _build_alert_message(publication: Publication, summary: str) -> str:
    """Construit le message d'alerte WhatsApp (sans emojis, ton professionnel)."""
    parts = ["*NOUVEL APPEL D'OFFRES*\n"]
    parts.append(f"*{publication.title}*\n")
    parts.append(f"Reference: {publication.reference}")
    parts.append(f"Source: {publication.source}")

    if publication.deadline:
        parts.append(f"Date limite: {publication.deadline.strftime('%d/%m/%Y')}")
    if publication.budget:
        parts.append(f"Budget: {publication.budget:,.0f} FCFA")

    if summary:
        parts.append(f"\n{summary}")

    if publication.pdf_url:
        parts.append(f"\nDocument: {publication.pdf_url}")

    parts.append(f"\nTapez */analyser {publication.reference}* pour une analyse detaillee.")
    parts.append(f"Tapez */demander_dossier {publication.reference}* pour obtenir le dossier complet.")

    return "\n".join(parts)


# ================================================
#  NOTIFICATIONS EMAIL (complement WhatsApp)
# ================================================

async def send_email_notification(
    user_email: str,
    user_name: str,
    publication: Publication,
    summary: str,
) -> bool:
    """Envoie une notification par email pour un appel d'offres.

    Returns:
        True si l'email a ete envoye, False sinon.
    """
    from app.config import settings

    if not settings.smtp_user or not settings.smtp_password:
        return False

    subject = f"[Tendo] {publication.title[:80]}"

    # Construire le corps de l'email en HTML
    deadline_str = ""
    if publication.deadline:
        deadline_str = f"<p><strong>Date limite :</strong> {publication.deadline.strftime('%d/%m/%Y')}</p>"

    budget_str = ""
    if publication.budget:
        budget_str = f"<p><strong>Budget :</strong> {publication.budget:,.0f} FCFA</p>"

    pdf_str = ""
    if publication.pdf_url:
        pdf_str = f'<p><a href="{publication.pdf_url}">Telecharger le document (PDF)</a></p>'

    html_body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; color: #333; max-width: 600px;">
        <h2 style="color: #1a5276;">Nouvel Appel d'Offres</h2>
        <h3>{publication.title}</h3>
        <p><strong>Reference :</strong> {publication.reference}</p>
        <p><strong>Source :</strong> {publication.source}</p>
        {deadline_str}
        {budget_str}
        <hr style="border: 1px solid #eee;">
        <p>{summary}</p>
        {pdf_str}
        <hr style="border: 1px solid #eee;">
        <p style="font-size: 12px; color: #888;">
            Cet email a ete envoye par Tendo, votre assistant de veille marches publics.<br>
            Repondez "STOP" par WhatsApp pour arreter ces notifications.
        </p>
    </body>
    </html>
    """

    try:
        import aiosmtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"Tendo <{settings.smtp_user}>"
        msg["To"] = user_email

        # Version texte
        text_body = (
            f"NOUVEL APPEL D'OFFRES\n\n"
            f"{publication.title}\n"
            f"Reference : {publication.reference}\n"
            f"Source : {publication.source}\n"
            f"{'Date limite : ' + publication.deadline.strftime('%d/%m/%Y') if publication.deadline else ''}\n"
            f"{'Budget : ' + str(int(publication.budget)) + ' FCFA' if publication.budget else ''}\n\n"
            f"{summary}\n\n"
            f"{'Document : ' + publication.pdf_url if publication.pdf_url else ''}\n\n"
            f"-- Tendo, votre assistant marches publics"
        )

        msg.attach(MIMEText(text_body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        await aiosmtplib.send(
            msg,
            hostname=settings.smtp_server,
            port=settings.smtp_port,
            username=settings.smtp_user,
            password=settings.smtp_password,
            start_tls=True,
        )
        logger.info(f"[Email] Notification envoyee a {user_email}: {publication.reference}")
        return True

    except Exception as e:
        logger.error(f"[Email] Erreur envoi a {user_email}: {e}")
        return False

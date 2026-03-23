"""Tâches Celery asynchrones – notifications, scraping, emails."""

import asyncio
from datetime import datetime, timezone

from sqlalchemy import select, and_
from sqlalchemy.orm import Session

from app.workers.celery_app import celery_app
from app.utils.db import SyncSessionLocal
from app.utils.logger import logger
from app.models.user import User, SubscriptionStatus
from app.models.publication import Publication
from app.models.email_tracking import EmailTracking
from app.services.scraping import ALL_SCRAPERS
from app.services import email_manager


@celery_app.task(name="app.workers.tasks.send_pending_notifications")
def send_pending_notifications():
    """Envoie les notifications pour les publications non traitées."""
    from app.services.notifications import process_new_publications
    from app.utils.db import AsyncSessionLocal

    async def _run():
        async with AsyncSessionLocal() as db:
            count = await process_new_publications(db)
            logger.info(f"Tâche notifications: {count} envoyées")

    asyncio.run(_run())


@celery_app.task(name="app.workers.tasks.run_all_scrapers")
def run_all_scrapers():
    """Exécute tous les scrapers et insère les résultats en base."""
    db: Session = SyncSessionLocal()
    total_new = 0

    try:
        for source_name, scraper_class in ALL_SCRAPERS.items():
            try:
                scraper = scraper_class()
                publications = scraper.run()

                for pub_data in publications:
                    # Vérifier si la publication existe déjà
                    existing = db.execute(
                        select(Publication).where(
                            Publication.reference == pub_data["reference"]
                        )
                    ).scalar_one_or_none()

                    if existing:
                        continue

                    publication = Publication(
                        source=pub_data["source"],
                        reference=pub_data["reference"],
                        title=pub_data["title"],
                        summary=pub_data.get("summary", ""),
                        budget=pub_data.get("budget"),
                        deadline=pub_data.get("deadline"),
                        pdf_url=pub_data.get("pdf_url"),
                        html_content=pub_data.get("html_content", ""),
                        category=pub_data.get("category", "marché"),
                        sectors=pub_data.get("sectors", []),
                        regions=pub_data.get("regions", []),
                        published_date=pub_data.get("published_date"),
                        authority_email=pub_data.get("authority_email"),
                        authority_name=pub_data.get("authority_name"),
                    )
                    db.add(publication)
                    total_new += 1

                db.commit()
                logger.info(f"[{source_name}] Scraping terminé: {len(publications)} trouvées")

            except Exception as e:
                logger.error(f"[{source_name}] Erreur scraping: {e}")
                db.rollback()

    finally:
        db.close()

    logger.info(f"Scraping total: {total_new} nouvelles publications")
    return total_new


@celery_app.task(name="app.workers.tasks.check_email_responses")
def check_email_responses():
    """Vérifie les réponses aux demandes de dossiers."""
    db: Session = SyncSessionLocal()

    try:
        # Récupérer les demandes en attente
        pending = db.execute(
            select(EmailTracking).where(EmailTracking.response_received == False)
        ).scalars().all()

        if not pending:
            return

        # Collecter les sujets à rechercher
        subjects = [t.subject for t in pending]

        # Vérifier la boîte mail
        responses = email_manager.check_inbox_for_responses(subjects)

        for resp in responses:
            for tracking in pending:
                if tracking.subject.lower() in resp["matched_subject"].lower():
                    tracking.response_received = True
                    tracking.response_content = resp["body"]
                    tracking.response_received_at = datetime.now(timezone.utc)

                    # Notifier l'utilisateur via WhatsApp
                    user = db.execute(
                        select(User).where(User.id == tracking.user_id)
                    ).scalar_one_or_none()

                    if user:
                        _notify_email_response(user.phone_number, tracking, resp["body"])

                    break

        db.commit()
        logger.info(f"Vérification emails: {len(responses)} réponses trouvées")

    except Exception as e:
        logger.error(f"Erreur check emails: {e}")
        db.rollback()
    finally:
        db.close()


def _notify_email_response(phone: str, tracking: EmailTracking, body: str):
    """Notifie l'utilisateur d'une réponse reçue."""
    from app.services.whatsapp import send_message

    message = (
        f"📬 *Réponse reçue !*\n\n"
        f"Votre demande de dossier a reçu une réponse.\n"
        f"De : {tracking.email_sent_to}\n"
        f"Objet : {tracking.subject}\n\n"
        f"📝 Extrait :\n{body[:500]}"
    )

    asyncio.run(send_message(phone, message))


@celery_app.task(name="app.workers.tasks.check_expired_subscriptions")
def check_expired_subscriptions():
    """Vérifie et marque les abonnements expirés."""
    db: Session = SyncSessionLocal()

    try:
        now = datetime.now(timezone.utc)

        # Utilisateurs en période d'essai expirée
        expired_trial = db.execute(
            select(User).where(
                and_(
                    User.subscription_status == SubscriptionStatus.TRIAL,
                    User.trial_end < now,
                )
            )
        ).scalars().all()

        for user in expired_trial:
            user.subscription_status = SubscriptionStatus.EXPIRED
            logger.info(f"Essai expiré: user={user.id}")

        # Utilisateurs avec abonnement actif expiré
        from app.models.subscription import Subscription, PaymentStatus

        active_users = db.execute(
            select(User).where(User.subscription_status == SubscriptionStatus.ACTIVE)
        ).scalars().all()

        for user in active_users:
            latest_sub = db.execute(
                select(Subscription)
                .where(
                    Subscription.user_id == user.id,
                    Subscription.status == PaymentStatus.PAID,
                )
                .order_by(Subscription.end_date.desc())
                .limit(1)
            ).scalar_one_or_none()

            if latest_sub and latest_sub.end_date < now:
                user.subscription_status = SubscriptionStatus.EXPIRED
                logger.info(f"Abonnement expiré: user={user.id}")

        db.commit()
        logger.info(f"Vérification abonnements: {len(expired_trial)} essais expirés")

    except Exception as e:
        logger.error(f"Erreur check abonnements: {e}")
        db.rollback()
    finally:
        db.close()

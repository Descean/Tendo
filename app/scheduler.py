"""Scheduler intégré – remplace Celery+Redis pour le MVP.

Utilise APScheduler pour exécuter les tâches de fond directement dans le process FastAPI.
Pas besoin de Redis, Celery ou de workers séparés.
"""

import asyncio
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import settings
from app.utils.logger import logger
from app.utils.db import AsyncSessionLocal


scheduler = AsyncIOScheduler()


async def job_run_scrapers():
    """Exécute tous les scrapers et insère les résultats en base."""
    from sqlalchemy import select
    from app.models.publication import Publication
    from app.services.scraping import ALL_SCRAPERS

    logger.info("[Scheduler] Lancement du scraping...")
    total_new = 0

    async with AsyncSessionLocal() as db:
        for source_name, scraper_class in ALL_SCRAPERS.items():
            try:
                scraper = scraper_class()
                publications = scraper.run()

                for pub_data in publications:
                    existing = await db.execute(
                        select(Publication).where(
                            Publication.reference == pub_data["reference"]
                        )
                    )
                    if existing.scalar_one_or_none():
                        continue

                    # Nettoyer les champs date : string vide -> None
                    published_date = pub_data.get("published_date") or None
                    deadline = pub_data.get("deadline") or None

                    publication = Publication(
                        source=pub_data["source"],
                        reference=pub_data["reference"],
                        title=pub_data["title"],
                        summary=pub_data.get("summary", ""),
                        budget=pub_data.get("budget") or None,
                        deadline=deadline,
                        pdf_url=pub_data.get("pdf_url") or None,
                        html_content=pub_data.get("html_content", ""),
                        category=pub_data.get("category", "marché"),
                        sectors=pub_data.get("sectors", []),
                        regions=pub_data.get("regions", []),
                        published_date=published_date,
                        authority_email=pub_data.get("authority_email") or None,
                        authority_name=pub_data.get("authority_name") or None,
                    )
                    db.add(publication)
                    total_new += 1

                await db.commit()
                logger.info(f"[Scheduler] [{source_name}] {len(publications)} trouvées")

            except Exception as e:
                logger.error(f"[Scheduler] [{source_name}] Erreur: {e}")
                await db.rollback()

    logger.info(f"[Scheduler] Scraping terminé: {total_new} nouvelles publications")
    return total_new


async def job_send_notifications():
    """Envoie les notifications pour les publications non traitées."""
    from app.services.notifications import process_new_publications

    logger.info("[Scheduler] Envoi des notifications...")
    async with AsyncSessionLocal() as db:
        try:
            count = await process_new_publications(db)
            logger.info(f"[Scheduler] {count} notifications envoyées")
        except Exception as e:
            logger.error(f"[Scheduler] Erreur notifications: {e}")


async def job_check_subscriptions():
    """Vérifie et marque les abonnements expirés."""
    from sqlalchemy import select, and_
    from app.models.user import User, SubscriptionStatus
    from app.models.subscription import Subscription, PaymentStatus

    logger.info("[Scheduler] Vérification des abonnements...")
    now = datetime.now(timezone.utc)

    async with AsyncSessionLocal() as db:
        try:
            # Essais expirés
            result = await db.execute(
                select(User).where(
                    and_(
                        User.subscription_status == SubscriptionStatus.TRIAL.value,
                        User.trial_end < now,
                    )
                )
            )
            expired_trial = result.scalars().all()
            for user in expired_trial:
                user.subscription_status = SubscriptionStatus.EXPIRED.value

            # Abonnements payants expirés
            result = await db.execute(
                select(User).where(
                    User.subscription_status == SubscriptionStatus.ACTIVE.value
                )
            )
            active_users = result.scalars().all()

            for user in active_users:
                sub_result = await db.execute(
                    select(Subscription)
                    .where(
                        Subscription.user_id == user.id,
                        Subscription.status == PaymentStatus.PAID.value,
                    )
                    .order_by(Subscription.end_date.desc())
                    .limit(1)
                )
                latest_sub = sub_result.scalar_one_or_none()
                if latest_sub and latest_sub.end_date < now:
                    user.subscription_status = SubscriptionStatus.EXPIRED.value

            await db.commit()
            logger.info(
                f"[Scheduler] {len(expired_trial)} essais expirés marqués"
            )
        except Exception as e:
            logger.error(f"[Scheduler] Erreur check abonnements: {e}")
            await db.rollback()


def setup_scheduler():
    """Configure et démarre le scheduler."""
    # Scraping – par défaut à 6h du matin (configurable via SCRAPING_SCHEDULE)
    parts = settings.scraping_schedule.split()
    if len(parts) == 5:
        minute, hour, day, month, dow = parts
        scheduler.add_job(
            job_run_scrapers,
            CronTrigger(minute=minute, hour=hour, day=day, month=month, day_of_week=dow),
            id="scraping",
            name="Scraping marchés publics",
            replace_existing=True,
        )
    else:
        # Fallback: toutes les 6 heures
        scheduler.add_job(
            job_run_scrapers,
            CronTrigger(hour="*/6"),
            id="scraping",
            name="Scraping marchés publics",
            replace_existing=True,
        )

    # Notifications – 5 minutes après le scraping, puis toutes les 2 heures
    scheduler.add_job(
        job_send_notifications,
        CronTrigger(minute="5", hour="*/2"),
        id="notifications",
        name="Envoi notifications",
        replace_existing=True,
    )

    # Vérification abonnements – chaque jour à minuit
    scheduler.add_job(
        job_check_subscriptions,
        CronTrigger(hour="0", minute="0"),
        id="check_subscriptions",
        name="Vérification abonnements",
        replace_existing=True,
    )

    scheduler.start()
    logger.info("[Scheduler] Tâches planifiées configurées")


def shutdown_scheduler():
    """Arrête le scheduler proprement."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("[Scheduler] Arrêté")

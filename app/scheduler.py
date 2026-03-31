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

# Import timedelta au top level (manquait pour job_send_expiration_reminders)
from datetime import timedelta


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
                        document_type=pub_data.get("document_type") or None,
                        financing_source=pub_data.get("financing_source") or None,
                        country=pub_data.get("country", "Bénin"),
                    )
                    db.add(publication)
                    total_new += 1

                await db.commit()
                logger.info(f"[Scheduler] [{source_name}] {len(publications)} trouvées")

            except Exception as e:
                logger.error(f"[Scheduler] [{source_name}] Erreur: {e}")
                await db.rollback()
                # Alerter les admins
                try:
                    from app.services.monitoring import alert_scraper_failure
                    await alert_scraper_failure(source_name, str(e))
                except Exception:
                    pass

    logger.info(f"[Scheduler] Scraping termine: {total_new} nouvelles publications")
    return total_new


async def job_send_notifications():
    """Envoie les notifications pour les publications non traitées."""
    from app.services.notifications import process_new_publications

    logger.info("[Scheduler] Envoi des notifications...")
    async with AsyncSessionLocal() as db:
        try:
            count = await process_new_publications(db)
            logger.info(f"[Scheduler] {count} notifications envoyees")
        except Exception as e:
            logger.error(f"[Scheduler] Erreur notifications: {e}")
            try:
                from app.services.monitoring import alert_notification_failure
                await alert_notification_failure(0, str(e))
            except Exception:
                pass


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


async def job_send_expiration_reminders():
    """Envoie des rappels WhatsApp 3 jours avant expiration."""
    from sqlalchemy import select, and_, or_
    from app.models.user import User, SubscriptionStatus
    from app.models.subscription import Subscription, PaymentStatus
    from app.services.whatsapp import send_message
    from app.services.payment import create_payment_link

    logger.info("[Scheduler] Envoi des rappels d'expiration...")
    now = datetime.now(timezone.utc)
    reminder_date = now + timedelta(days=3)
    sent = 0

    async with AsyncSessionLocal() as db:
        try:
            # Essais qui expirent dans 3 jours
            result = await db.execute(
                select(User).where(
                    and_(
                        User.is_active == True,
                        User.subscription_status == SubscriptionStatus.TRIAL.value,
                        User.trial_end != None,
                        User.trial_end > now,
                        User.trial_end <= reminder_date,
                    )
                )
            )
            trial_users = result.scalars().all()

            for user in trial_users:
                days_left = (user.trial_end - now).days
                try:
                    # Generer un lien de paiement pour faciliter la conversion
                    payment = await create_payment_link(
                        user_phone=user.phone_number,
                        plan="essentiel",
                        user_name=user.name or "",
                    )
                    msg = (
                        f"RAPPEL TENDO\n\n"
                        f"Votre essai gratuit expire dans {days_left} jour(s).\n\n"
                        f"Pour continuer a recevoir vos alertes marches publics, "
                        f"souscrivez au Plan Essentiel (5 000 FCFA/mois).\n\n"
                        f"Payer maintenant :\n{payment['payment_link']}\n\n"
                        f"Tapez *Abonnement* pour voir tous les plans."
                    )
                    await send_message(user.phone_number, msg)
                    sent += 1
                    await asyncio.sleep(3)  # Rate limit
                except Exception as e:
                    logger.error(f"[Scheduler] Erreur rappel trial user={user.id}: {e}")

            # Abonnements payants qui expirent dans 3 jours
            result = await db.execute(
                select(User).where(
                    and_(
                        User.is_active == True,
                        User.subscription_status == SubscriptionStatus.ACTIVE.value,
                    )
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
                if latest_sub and latest_sub.end_date > now and latest_sub.end_date <= reminder_date:
                    days_left = (latest_sub.end_date - now).days
                    plan = latest_sub.plan or "essentiel"
                    try:
                        payment = await create_payment_link(
                            user_phone=user.phone_number,
                            plan=plan,
                            user_name=user.name or "",
                        )
                        msg = (
                            f"RAPPEL TENDO\n\n"
                            f"Votre abonnement expire dans {days_left} jour(s).\n\n"
                            f"Renouvelez pour continuer a recevoir vos alertes :\n"
                            f"{payment['payment_link']}\n\n"
                            f"Tapez *Abonnement* pour voir les plans."
                        )
                        await send_message(user.phone_number, msg)
                        sent += 1
                        await asyncio.sleep(3)
                    except Exception as e:
                        logger.error(f"[Scheduler] Erreur rappel sub user={user.id}: {e}")

        except Exception as e:
            logger.error(f"[Scheduler] Erreur rappels expiration: {e}")

    logger.info(f"[Scheduler] {sent} rappels d'expiration envoyes")


async def job_check_email_responses():
    """Verifie les reponses email aux demandes de dossiers et notifie les utilisateurs."""
    from sqlalchemy import select
    from app.models.notification import Notification
    from app.models.publication import Publication
    from app.models.user import User
    from app.services.email_manager import check_inbox_for_responses
    from app.services.whatsapp import send_message

    logger.info("[Scheduler] Verification inbox email...")

    async with AsyncSessionLocal() as db:
        try:
            # Recuperer les demandes de dossier recentes (derniers 7 jours)
            week_ago = datetime.now(timezone.utc) - timedelta(days=7)
            result = await db.execute(
                select(Notification).where(
                    Notification.sent_at >= week_ago
                ).limit(50)
            )
            notifications = result.scalars().all()

            if not notifications:
                logger.info("[Scheduler] Aucune demande recente a verifier")
                return

            # Construire les sujets a chercher
            pub_ids = [n.publication_id for n in notifications if n.publication_id]
            if not pub_ids:
                return

            result = await db.execute(
                select(Publication).where(Publication.id.in_(pub_ids))
            )
            publications = {p.id: p for p in result.scalars().all()}

            subjects = []
            for notif in notifications:
                pub = publications.get(notif.publication_id)
                if pub:
                    subjects.append(pub.reference)

            if not subjects:
                return

            # Verifier les reponses (3 derniers jours)
            since = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%d-%b-%Y")
            responses = await check_inbox_for_responses(subjects, since_date=since)

            for resp in responses:
                # Trouver l'utilisateur concerne
                matched_ref = resp.get("matched_subject", "")
                for notif in notifications:
                    pub = publications.get(notif.publication_id)
                    if pub and pub.reference == matched_ref:
                        # Recuperer l'utilisateur
                        user_result = await db.execute(
                            select(User).where(User.id == notif.user_id)
                        )
                        user = user_result.scalar_one_or_none()
                        if user:
                            try:
                                msg = (
                                    f"*REPONSE DOSSIER RECUE*\n\n"
                                    f"Reference: {matched_ref}\n"
                                    f"De: {resp.get('from', 'Inconnu')}\n\n"
                                    f"{resp.get('body', '')[:500]}\n\n"
                                    f"Consultez votre email pour le dossier complet."
                                )
                                await send_message(user.phone_number, msg)
                                await asyncio.sleep(3)
                            except Exception as e:
                                logger.error(f"[Scheduler] Erreur notif email user={user.id}: {e}")
                        break

            logger.info(f"[Scheduler] {len(responses)} reponses email trouvees")

        except Exception as e:
            logger.error(f"[Scheduler] Erreur check email: {e}")


async def job_process_jnmp_journals():
    """Segmente les journaux JNMP en documents individuels."""
    from app.services.jnmp_analyzer import process_jnmp_journals

    logger.info("[Scheduler] Traitement journaux JNMP...")
    async with AsyncSessionLocal() as db:
        try:
            count = await process_jnmp_journals(db)
            logger.info(f"[Scheduler] JNMP: {count} documents crees")
        except Exception as e:
            logger.error(f"[Scheduler] Erreur JNMP: {e}")


async def job_daily_report():
    """Envoie un rapport quotidien aux administrateurs."""
    from sqlalchemy import select, func
    from app.models.user import User
    from app.models.publication import Publication
    from app.models.notification import Notification
    from app.services.monitoring import send_daily_report

    logger.info("[Scheduler] Generation du rapport quotidien...")

    async with AsyncSessionLocal() as db:
        try:
            now = datetime.now(timezone.utc)
            yesterday = now.replace(hour=0, minute=0, second=0) - __import__("datetime").timedelta(days=1)

            # Compter publications du jour
            pub_count = await db.execute(
                select(func.count(Publication.id)).where(
                    Publication.created_at >= yesterday
                )
            )
            scraped = pub_count.scalar() or 0

            # Compter notifications du jour
            notif_count = await db.execute(
                select(func.count(Notification.id)).where(
                    Notification.sent_at >= yesterday
                )
            )
            notifs = notif_count.scalar() or 0

            # Compter utilisateurs actifs
            user_count = await db.execute(
                select(func.count(User.id)).where(User.is_active == True)
            )
            users = user_count.scalar() or 0

            await send_daily_report(
                scraped_count=scraped,
                notifications_sent=notifs,
                active_users=users,
                errors_count=0,
            )
        except Exception as e:
            logger.error(f"[Scheduler] Erreur rapport quotidien: {e}")


def setup_scheduler():
    """Configure et demarre le scheduler."""
    # Scraping -- par defaut a 6h du matin (configurable via SCRAPING_SCHEDULE)
    parts = settings.scraping_schedule.split()
    if len(parts) == 5:
        minute, hour, day, month, dow = parts
        scheduler.add_job(
            job_run_scrapers,
            CronTrigger(minute=minute, hour=hour, day=day, month=month, day_of_week=dow),
            id="scraping",
            name="Scraping marches publics",
            replace_existing=True,
        )
    else:
        scheduler.add_job(
            job_run_scrapers,
            CronTrigger(hour="*/6"),
            id="scraping",
            name="Scraping marches publics",
            replace_existing=True,
        )

    # Notifications -- 5 minutes apres le scraping, puis toutes les 2 heures
    scheduler.add_job(
        job_send_notifications,
        CronTrigger(minute="5", hour="*/2"),
        id="notifications",
        name="Envoi notifications",
        replace_existing=True,
    )

    # Verification abonnements -- chaque jour a minuit
    scheduler.add_job(
        job_check_subscriptions,
        CronTrigger(hour="0", minute="0"),
        id="check_subscriptions",
        name="Verification abonnements",
        replace_existing=True,
    )

    # Rappels d'expiration -- chaque jour a 9h du matin
    scheduler.add_job(
        job_send_expiration_reminders,
        CronTrigger(hour="9", minute="0"),
        id="expiration_reminders",
        name="Rappels expiration",
        replace_existing=True,
    )

    # Rapport quotidien -- chaque jour a 20h
    scheduler.add_job(
        job_daily_report,
        CronTrigger(hour="20", minute="0"),
        id="daily_report",
        name="Rapport quotidien",
        replace_existing=True,
    )

    # Surveillance inbox email — chaque jour a 10h et 16h
    scheduler.add_job(
        job_check_email_responses,
        CronTrigger(hour="10,16", minute="0"),
        id="email_check",
        name="Verification inbox email",
        replace_existing=True,
    )

    # Segmentation journaux JNMP -- 30 min apres le scraping, puis toutes les 12h
    scheduler.add_job(
        job_process_jnmp_journals,
        CronTrigger(hour="6,18", minute="30"),
        id="jnmp_processing",
        name="Segmentation journaux JNMP",
        replace_existing=True,
    )

    # Peuplement initial de la base de connaissances (une seule fois)
    async def _seed_knowledge():
        from app.services.knowledge_service import seed_initial_knowledge
        async with AsyncSessionLocal() as db:
            try:
                await seed_initial_knowledge(db)
            except Exception as e:
                logger.error(f"[Scheduler] Erreur seed knowledge: {e}")

    scheduler.add_job(
        _seed_knowledge,
        "date",  # Execute une seule fois au demarrage
        id="seed_knowledge",
        name="Seed Knowledge Base",
        replace_existing=True,
    )

    scheduler.start()
    logger.info("[Scheduler] Taches planifiees configurees")


def shutdown_scheduler():
    """Arrête le scheduler proprement."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("[Scheduler] Arrêté")

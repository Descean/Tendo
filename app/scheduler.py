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

    # --- Pipeline chainé : JNMP segmentation + DeepSeek enrichissement ---
    # Toujours lancer l'enrichissement (des pubs récentes peuvent attendre)
    if total_new > 0:
        logger.info("[Scheduler] Pipeline: lancement segmentation JNMP...")
        try:
            await job_process_jnmp_journals()
        except Exception as e:
            logger.error(f"[Scheduler] Pipeline JNMP: {e}")

    logger.info("[Scheduler] Pipeline: lancement enrichissement DeepSeek...")
    try:
        await job_enrich_publications()
    except Exception as e:
        logger.error(f"[Scheduler] Pipeline enrichissement: {e}")

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


async def job_cleanup_expired_publications():
    """Supprime les publications dont la deadline est depassee."""
    from sqlalchemy import select, delete, and_
    from app.models.publication import Publication
    from app.models.notification import Notification
    from app.models.email_tracking import EmailTracking

    logger.info("[Scheduler] Nettoyage des AO expires...")
    now = datetime.now(timezone.utc)

    async with AsyncSessionLocal() as db:
        try:
            # Trouver les publications expirees (deadline passee)
            # Exclure PV/Decisions qui sont des documents de connaissance
            from sqlalchemy import or_
            knowledge_types = ("PV_ATTRIBUTION", "PV_OUVERTURE", "DECISION_ARMP")
            result = await db.execute(
                select(Publication.id).where(
                    and_(
                        Publication.deadline != None,
                        Publication.deadline < now,
                        or_(
                            Publication.document_type == None,
                            ~Publication.document_type.in_(knowledge_types),
                        ),
                    )
                )
            )
            expired_ids = [row[0] for row in result.all()]

            if not expired_ids:
                logger.info("[Scheduler] Aucun AO expire a nettoyer")
                return

            # Supprimer d'abord les notifications et email_trackings liees
            await db.execute(
                delete(Notification).where(
                    Notification.publication_id.in_(expired_ids)
                )
            )
            await db.execute(
                delete(EmailTracking).where(
                    EmailTracking.publication_id.in_(expired_ids)
                )
            )

            # Supprimer les publications expirees
            await db.execute(
                delete(Publication).where(
                    Publication.id.in_(expired_ids)
                )
            )

            await db.commit()
            logger.info(f"[Scheduler] {len(expired_ids)} AO expires supprimes")

        except Exception as e:
            logger.error(f"[Scheduler] Erreur nettoyage expires: {e}")
            await db.rollback()


async def job_enrich_publications():
    """Enrichit les publications via lecture IA des PDF (DeepSeek/Groq/Gemini).

    Pour chaque publication recente qui a un pdf_url mais pas de pdf_content,
    telecharge le PDF, extrait le texte, puis utilise l'IA pour extraire
    les champs structures (autorite, budget, deadline, garantie, etc.).
    """
    from sqlalchemy import select, and_, or_, func
    from app.models.publication import Publication
    from app.services.document_analyzer import extract_pdf_text
    from app.services.deepseek_reader import extract_document_info

    logger.info("[Scheduler] Enrichissement IA des publications...")
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=14)
    enriched = 0

    async with AsyncSessionLocal() as db:
        try:
            # Publications recentes avec PDF mais pas encore analysees
            result = await db.execute(
                select(Publication).where(
                    and_(
                        Publication.created_at >= cutoff,
                        Publication.pdf_url != None,
                        or_(
                            Publication.pdf_content == None,
                            Publication.pdf_content == "",
                        ),
                    )
                )
                .order_by(Publication.created_at.desc())
                .limit(50)
            )
            publications = result.scalars().all()

            if not publications:
                logger.info("[Scheduler] Aucune publication a enrichir")
                return

            for pub in publications:
                try:
                    # Extraire le texte du PDF
                    text, method, pages = await extract_pdf_text(pdf_url=pub.pdf_url)
                    if not text or len(text.strip()) < 50:
                        pub.pdf_content = "[extraction_failed]"
                        continue

                    # Sauvegarder le texte brut (sans limite)
                    pub.pdf_content = text

                    # Extraire les infos structurees via IA
                    # Prefixer le titre pour aider la classification (ex: "ARMP – Avis N2026")
                    text_with_title = f"{pub.title or ''}\n\n{text}"
                    info = await extract_document_info(text_with_title)
                    if info:
                        # Mettre a jour les champs manquants
                        if not pub.authority_name and info.get("authority_name"):
                            pub.authority_name = info["authority_name"]
                        if not pub.authority_email and info.get("authority_email"):
                            pub.authority_email = info["authority_email"]
                        if not pub.budget and info.get("budget"):
                            pub.budget = info["budget"]
                        # Toujours mettre a jour le document_type (le classifieur regex est fiable)
                        if info.get("document_type"):
                            pub.document_type = info["document_type"]
                        if not pub.financing_source and info.get("financing_source"):
                            pub.financing_source = info["financing_source"]
                        if not pub.summary and info.get("summary"):
                            pub.summary = info["summary"]

                        # Deadline depuis l'IA
                        if not pub.deadline and info.get("deadline_date"):
                            from app.services.scraping.gouv_bj import GouvBJScraper
                            parsed = GouvBJScraper._parse_date(None, info["deadline_date"])
                            if parsed:
                                from datetime import datetime as dt
                                try:
                                    pub.deadline = dt.fromisoformat(parsed)
                                except Exception:
                                    pass

                        # Garantie de soumission
                        if info.get("guarantee_amount") and not pub.guarantee_amount:
                            pub.guarantee_amount = info["guarantee_amount"]

                        # Lots et delai de livraison (nouveaux champs)
                        if info.get("lots_detail") and not pub.lots_detail:
                            pub.lots_detail = info["lots_detail"]
                        if info.get("lots_count") and not pub.lots_count:
                            pub.lots_count = info["lots_count"]
                        if info.get("delivery_delay") and not pub.delivery_delay:
                            pub.delivery_delay = info["delivery_delay"]

                        # Documents requis et criteres (colonnes JSONB → stocker en liste)
                        if info.get("required_documents") and not pub.required_documents:
                            docs = info["required_documents"]
                            pub.required_documents = [d.strip() for d in docs.split(",")] if isinstance(docs, str) else docs
                        if info.get("participation_conditions") and not pub.qualification_criteria:
                            conds = info["participation_conditions"]
                            pub.qualification_criteria = [c.strip() for c in conds.split(",")] if isinstance(conds, str) else conds

                        # Stocker les infos supplementaires dans html_content
                        extra_parts = []
                        if info.get("submission_location"):
                            extra_parts.append(f"Lieu depot: {info['submission_location']}")

                        if extra_parts and not pub.html_content:
                            pub.html_content = "\n".join(extra_parts)

                        enriched += 1
                        logger.info(f"[Scheduler] Enrichi: {pub.reference} ({method})")

                    await asyncio.sleep(2)  # Rate limiting

                except Exception as e:
                    logger.error(f"[Scheduler] Erreur enrichissement {pub.reference}: {e}")

            await db.commit()

        except Exception as e:
            logger.error(f"[Scheduler] Erreur enrichissement global: {e}")
            await db.rollback()

    # --- Phase 2: enrichir les publications SANS PDF (texte brut du scraping) ---
    async with AsyncSessionLocal() as db:
        try:
            result = await db.execute(
                select(Publication).where(
                    and_(
                        Publication.created_at >= cutoff,
                        or_(Publication.pdf_url == None, Publication.pdf_url == ""),
                        or_(Publication.technical_summary == None, Publication.technical_summary == ""),
                        Publication.summary != None,
                        func.length(Publication.summary) > 100,
                    )
                )
                .order_by(Publication.created_at.desc())
                .limit(30)
            )
            text_pubs = result.scalars().all()

            for pub in text_pubs:
                try:
                    info = await extract_document_info(pub.summary + "\n" + (pub.html_content or ""))
                    if info:
                        if not pub.authority_name and info.get("authority_name"):
                            pub.authority_name = info["authority_name"]
                        if not pub.budget and info.get("budget"):
                            pub.budget = info["budget"]
                        if info.get("document_type"):
                            pub.document_type = info["document_type"]
                        if not pub.financing_source and info.get("financing_source"):
                            pub.financing_source = info["financing_source"]
                        if info.get("guarantee_amount") and not pub.guarantee_amount:
                            pub.guarantee_amount = info["guarantee_amount"]
                        if info.get("lots_detail") and not pub.lots_detail:
                            pub.lots_detail = info["lots_detail"]
                        if info.get("lots_count") and not pub.lots_count:
                            pub.lots_count = info["lots_count"]
                        if info.get("delivery_delay") and not pub.delivery_delay:
                            pub.delivery_delay = info["delivery_delay"]
                        if info.get("required_documents") and not pub.required_documents:
                            docs = info["required_documents"]
                            pub.required_documents = [d.strip() for d in docs.split(",")] if isinstance(docs, str) else docs

                        pub.technical_summary = info.get("summary", "")
                        enriched += 1

                    await asyncio.sleep(2)
                except Exception as e:
                    logger.error(f"[Scheduler] Erreur enrichissement texte {pub.reference}: {e}")

            await db.commit()
        except Exception as e:
            logger.error(f"[Scheduler] Erreur enrichissement texte global: {e}")
            await db.rollback()

    logger.info(f"[Scheduler] {enriched} publications enrichies par IA")


async def job_reenrich_deadlines():
    """Re-enrichit les publications qui ont du pdf_content mais pas de deadline.

    Utile pour extraire les dates de soumission des documents deja traites
    dont la deadline n'a pas ete parsee lors du premier enrichissement.
    """
    from sqlalchemy import select, and_
    from app.models.publication import Publication
    from app.services.deepseek_reader import extract_document_info

    logger.info("[Scheduler] Re-enrichissement deadlines...")
    enriched = 0

    async with AsyncSessionLocal() as db:
        try:
            # Publications avec contenu PDF mais sans deadline
            result = await db.execute(
                select(Publication).where(
                    and_(
                        Publication.pdf_content != None,
                        Publication.pdf_content != "",
                        Publication.pdf_content != "[extraction_failed]",
                        Publication.deadline == None,
                        Publication.document_type.in_(["AAO", "DAO", "AMI", "RFQ", "RFP", None]),
                    )
                )
                .order_by(Publication.created_at.desc())
                .limit(50)
            )
            publications = result.scalars().all()

            if not publications:
                logger.info("[Scheduler] Aucune publication a re-enrichir pour deadlines")
                return

            logger.info(f"[Scheduler] {len(publications)} publications a re-enrichir")

            for pub in publications:
                try:
                    content = pub.pdf_content
                    if pub.summary:
                        content = pub.summary + "\n" + content

                    info = await extract_document_info(content)
                    if info:
                        # Deadline
                        if info.get("deadline_date"):
                            from app.services.scraping.gouv_bj import GouvBJScraper
                            parsed = GouvBJScraper._parse_date(None, info["deadline_date"])
                            if parsed:
                                from datetime import datetime as dt
                                try:
                                    pub.deadline = dt.fromisoformat(parsed)
                                    enriched += 1
                                    logger.info(f"[Scheduler] Deadline trouvee: {pub.reference} -> {pub.deadline}")
                                except Exception:
                                    pass

                        # Aussi mettre a jour les champs manquants
                        if not pub.authority_name and info.get("authority_name"):
                            pub.authority_name = info["authority_name"]
                        if not pub.budget and info.get("budget"):
                            pub.budget = info["budget"]
                        if not pub.document_type and info.get("document_type"):
                            pub.document_type = info["document_type"]
                        if not pub.financing_source and info.get("financing_source"):
                            pub.financing_source = info["financing_source"]
                        if info.get("guarantee_amount") and not pub.guarantee_amount:
                            pub.guarantee_amount = info["guarantee_amount"]

                    await asyncio.sleep(2)

                except Exception as e:
                    logger.error(f"[Scheduler] Erreur re-enrichissement {pub.reference}: {e}")

            await db.commit()

        except Exception as e:
            logger.error(f"[Scheduler] Erreur re-enrichissement global: {e}")
            await db.rollback()

    logger.info(f"[Scheduler] {enriched} deadlines extraites par re-enrichissement")


async def job_proactive_discussions():
    """Tendo lance des discussions proactives sur les marches publics.

    Envoie des messages de veille aux utilisateurs actifs :
    - Tendances du marche (nouveaux secteurs, volumes)
    - Resumes de PV/Decisions ARMP
    - Conseils pratiques sur les procedures
    - Rappels d'opportunites qui correspondent a leurs secteurs
    """
    from sqlalchemy import select, and_, func
    from app.models.user import User, SubscriptionStatus
    from app.models.publication import Publication
    from app.services import whatsapp
    from app.services.claude import chat

    logger.info("[Scheduler] Discussions proactives...")
    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)

    async with AsyncSessionLocal() as db:
        try:
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
                return

            # Statistiques recentes
            pub_count = await db.execute(
                select(func.count(Publication.id)).where(
                    Publication.created_at >= week_ago
                )
            )
            total_pubs = pub_count.scalar() or 0

            # PV et Decisions recentes (connaissance)
            pv_result = await db.execute(
                select(Publication).where(
                    and_(
                        Publication.created_at >= week_ago,
                        Publication.document_type.in_(["PV_ATTRIBUTION", "PV_OUVERTURE", "DECISION_ARMP"]),
                    )
                ).limit(5)
            )
            pv_docs = pv_result.scalars().all()

            # Publications par secteur
            sector_result = await db.execute(
                select(Publication.category, func.count(Publication.id)).where(
                    Publication.created_at >= week_ago
                ).group_by(Publication.category).order_by(func.count(Publication.id).desc()).limit(5)
            )
            sector_stats = sector_result.all()

            # Construire le contexte pour l'IA
            context_parts = [
                f"Cette semaine: {total_pubs} nouvelles publications",
            ]
            if sector_stats:
                context_parts.append("Repartition: " + ", ".join(
                    f"{cat or 'marché'} ({cnt})" for cat, cnt in sector_stats
                ))
            if pv_docs:
                context_parts.append("PV/Decisions recentes:")
                for pv in pv_docs[:3]:
                    context_parts.append(f"- {pv.title[:100]} ({pv.document_type})")

            context = "\n".join(context_parts)

            # Generer le message proactif via IA
            prompt = f"""A partir de ces donnees sur les marches publics au Benin cette semaine,
redige un court message WhatsApp informatif et engageant pour les abonnes Tendo.

Le message doit :
- Partager une tendance ou un fait interessant
- Etre utile et informatif (pas commercial)
- Encourager l'engagement (poser une question ou inviter a reagir)
- Maximum 5 lignes

Donnees de la semaine :
{context}

Redige le message directement, sans introduction."""

            message = await chat(prompt, is_premium=False)
            if not message or len(message) < 20:
                logger.warning("[Scheduler] Message proactif trop court, abandon")
                return

            # Envoyer a tous les utilisateurs actifs (avec rate limiting)
            # Utilise send_proactive_message car hors fenêtre 24h
            sent = 0
            for user in users:
                try:
                    await whatsapp.send_proactive_message(user.phone_number, message)
                    sent += 1
                    await asyncio.sleep(5)  # Rate limit plus conservateur pour proactif
                except Exception as e:
                    logger.error(f"[Scheduler] Erreur proactive user={user.id}: {e}")
                    if "rate limit" in str(e).lower():
                        await asyncio.sleep(30)
                        break

            logger.info(f"[Scheduler] Discussion proactive envoyee a {sent} utilisateurs")

        except Exception as e:
            logger.error(f"[Scheduler] Erreur discussions proactives: {e}")


async def job_self_learn():
    """Auto-apprentissage : Tendo analyse les PV d'attribution, les patterns de marches,
    et enrichit sa base de connaissances automatiquement.

    Extrait :
    - Fourchettes de prix par secteur/type de marche
    - Entreprises frequemment attributaires
    - Taux de reussite par nombre de soumissionnaires
    - Patterns de deadlines et saisonnalite
    """
    from sqlalchemy import select, func, and_, or_
    from app.models.publication import Publication
    from app.models.knowledge_base import KnowledgeBase

    logger.info("[Self-Learn] Demarrage auto-apprentissage...")

    async with AsyncSessionLocal() as db:
        try:
            learned = 0

            # ── 1. Analyse des PV d'attribution → intelligence de prix ──
            pv_results = await db.execute(
                select(Publication).where(
                    and_(
                        Publication.document_type.in_(["PV_ATTRIBUTION", "PV_OUVERTURE"]),
                        Publication.budget != None,
                        Publication.budget > 0,
                    )
                ).order_by(Publication.created_at.desc()).limit(200)
            )
            pvs = pv_results.scalars().all()

            if pvs:
                # Analyser les fourchettes de prix par secteur
                sector_budgets = {}
                for pv in pvs:
                    for sector in (pv.sectors or []):
                        if sector not in sector_budgets:
                            sector_budgets[sector] = []
                        sector_budgets[sector].append(float(pv.budget))

                for sector, budgets in sector_budgets.items():
                    if len(budgets) < 3:
                        continue
                    budgets.sort()
                    avg = sum(budgets) / len(budgets)
                    median = budgets[len(budgets) // 2]
                    min_b, max_b = budgets[0], budgets[-1]

                    title = f"Intelligence prix - Secteur {sector}"
                    existing = await db.execute(
                        select(KnowledgeBase).where(
                            and_(KnowledgeBase.title == title, KnowledgeBase.category == "intelligence_marche")
                        )
                    )
                    kb = existing.scalar_one_or_none()

                    content = (
                        f"Analyse de {len(budgets)} marches attribues dans le secteur {sector}.\n"
                        f"Budget moyen: {avg:,.0f} FCFA\n"
                        f"Budget median: {median:,.0f} FCFA\n"
                        f"Fourchette: {min_b:,.0f} - {max_b:,.0f} FCFA\n"
                        f"Nombre de marches analyses: {len(budgets)}\n"
                        f"Derniere mise a jour: {datetime.now(timezone.utc).strftime('%d/%m/%Y')}"
                    )

                    if kb:
                        kb.content = content
                        kb.version = (kb.version or 1) + 1
                        kb.updated_at = datetime.now(timezone.utc)
                    else:
                        db.add(KnowledgeBase(
                            category="intelligence_marche",
                            subcategory="prix_secteur",
                            title=title,
                            content=content,
                            keywords=[sector, "prix", "budget", "attribution"],
                            country="Benin",
                            language="fr",
                        ))
                    learned += 1

            # ── 2. Analyse des sources les plus actives ──
            source_stats = await db.execute(
                select(
                    Publication.source,
                    func.count(Publication.id).label("cnt"),
                    func.avg(Publication.budget).label("avg_budget"),
                ).where(
                    Publication.created_at >= datetime.now(timezone.utc) - timedelta(days=30)
                ).group_by(Publication.source).order_by(func.count(Publication.id).desc())
            )
            sources = source_stats.all()

            if sources:
                title = "Activite des sources - 30 derniers jours"
                content_lines = [f"Analyse des {sum(s.cnt for s in sources)} publications sur 30 jours:\n"]
                for src in sources:
                    avg_str = f" (budget moyen: {src.avg_budget:,.0f} FCFA)" if src.avg_budget else ""
                    content_lines.append(f"- {src.source}: {src.cnt} publications{avg_str}")
                content_lines.append(f"\nDerniere mise a jour: {datetime.now(timezone.utc).strftime('%d/%m/%Y')}")

                existing = await db.execute(
                    select(KnowledgeBase).where(
                        and_(KnowledgeBase.title == title, KnowledgeBase.category == "intelligence_marche")
                    )
                )
                kb = existing.scalar_one_or_none()
                content = "\n".join(content_lines)

                if kb:
                    kb.content = content
                    kb.version = (kb.version or 1) + 1
                    kb.updated_at = datetime.now(timezone.utc)
                else:
                    db.add(KnowledgeBase(
                        category="intelligence_marche",
                        subcategory="activite_sources",
                        title=title,
                        content=content,
                        keywords=["source", "activite", "tendance", "statistique"],
                        country="Benin",
                        language="fr",
                    ))
                learned += 1

            # ── 3. Analyse des types de documents les plus frequents ──
            type_stats = await db.execute(
                select(
                    Publication.document_type,
                    func.count(Publication.id).label("cnt"),
                ).where(
                    and_(
                        Publication.document_type != None,
                        Publication.document_type != "",
                    )
                ).group_by(Publication.document_type).order_by(func.count(Publication.id).desc())
            )
            types = type_stats.all()

            if types:
                title = "Repartition des types de documents"
                total = sum(t.cnt for t in types)
                content_lines = [f"Analyse de {total} publications classifiees:\n"]
                for t in types:
                    pct = round(t.cnt / total * 100, 1)
                    content_lines.append(f"- {t.document_type}: {t.cnt} ({pct}%)")
                content_lines.append(f"\nDerniere mise a jour: {datetime.now(timezone.utc).strftime('%d/%m/%Y')}")

                existing = await db.execute(
                    select(KnowledgeBase).where(
                        and_(KnowledgeBase.title == title, KnowledgeBase.category == "intelligence_marche")
                    )
                )
                kb = existing.scalar_one_or_none()
                content = "\n".join(content_lines)

                if kb:
                    kb.content = content
                    kb.version = (kb.version or 1) + 1
                    kb.updated_at = datetime.now(timezone.utc)
                else:
                    db.add(KnowledgeBase(
                        category="intelligence_marche",
                        subcategory="types_documents",
                        title=title,
                        content=content,
                        keywords=["type", "document", "repartition", "statistique"],
                        country="Benin",
                        language="fr",
                    ))
                learned += 1

            # ── 4. Autorites contractantes les plus actives ──
            auth_stats = await db.execute(
                select(
                    Publication.authority_name,
                    func.count(Publication.id).label("cnt"),
                ).where(
                    and_(
                        Publication.authority_name != None,
                        Publication.authority_name != "",
                    )
                ).group_by(Publication.authority_name)
                .order_by(func.count(Publication.id).desc())
                .limit(20)
            )
            authorities = auth_stats.all()

            if authorities:
                title = "Top autorites contractantes"
                content_lines = [f"Les {len(authorities)} autorites contractantes les plus actives:\n"]
                for a in authorities:
                    content_lines.append(f"- {a.authority_name}: {a.cnt} marches publies")
                content_lines.append(f"\nDerniere mise a jour: {datetime.now(timezone.utc).strftime('%d/%m/%Y')}")

                existing = await db.execute(
                    select(KnowledgeBase).where(
                        and_(KnowledgeBase.title == title, KnowledgeBase.category == "intelligence_marche")
                    )
                )
                kb = existing.scalar_one_or_none()
                content = "\n".join(content_lines)

                if kb:
                    kb.content = content
                    kb.version = (kb.version or 1) + 1
                    kb.updated_at = datetime.now(timezone.utc)
                else:
                    db.add(KnowledgeBase(
                        category="intelligence_marche",
                        subcategory="autorites_actives",
                        title=title,
                        content=content,
                        keywords=["autorite", "contractante", "ministere", "actif"],
                        country="Benin",
                        language="fr",
                    ))
                learned += 1

            await db.commit()
            logger.info(f"[Self-Learn] {learned} connaissances creees/mises a jour")

        except Exception as e:
            logger.error(f"[Self-Learn] Erreur: {e}")
            await db.rollback()


async def job_daily_report():
    """Envoie un rapport quotidien complet a l'administrateur via WhatsApp.

    Envoye a 19h GMT au numero +2290140809108.
    """
    from sqlalchemy import select, func, and_
    from app.models.user import User, SubscriptionStatus
    from app.models.publication import Publication
    from app.models.notification import Notification
    from app.services.whatsapp import send_message

    ADMIN_PHONE = "2290140809108"

    logger.info("[Scheduler] Generation du rapport quotidien admin...")

    async with AsyncSessionLocal() as db:
        try:
            now = datetime.now(timezone.utc)
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

            # Publications du jour
            pub_today = await db.execute(
                select(func.count(Publication.id)).where(
                    Publication.created_at >= today_start
                )
            )
            pubs_count = pub_today.scalar() or 0

            # Publications par source aujourd'hui
            source_stats = await db.execute(
                select(Publication.source, func.count(Publication.id)).where(
                    Publication.created_at >= today_start
                ).group_by(Publication.source).order_by(func.count(Publication.id).desc()).limit(8)
            )
            sources = source_stats.all()

            # Notifications envoyees aujourd'hui
            notif_today = await db.execute(
                select(func.count(Notification.id)).where(
                    Notification.sent_at >= today_start
                )
            )
            notifs_count = notif_today.scalar() or 0

            # Utilisateurs actifs (trial + active)
            active_count = await db.execute(
                select(func.count(User.id)).where(
                    and_(
                        User.is_active == True,
                        User.subscription_status.in_([
                            SubscriptionStatus.TRIAL.value,
                            SubscriptionStatus.ACTIVE.value,
                        ]),
                    )
                )
            )
            active_users = active_count.scalar() or 0

            # Utilisateurs total
            total_users = await db.execute(select(func.count(User.id)))
            total = total_users.scalar() or 0

            # Publications enrichies (avec pdf_content)
            enriched_count = await db.execute(
                select(func.count(Publication.id)).where(
                    and_(
                        Publication.created_at >= today_start,
                        Publication.pdf_content != None,
                        Publication.pdf_content != "",
                        Publication.pdf_content != "[extraction_failed]",
                    )
                )
            )
            enriched = enriched_count.scalar() or 0

            # Total publications en base
            total_pubs = await db.execute(select(func.count(Publication.id)))
            total_publications = total_pubs.scalar() or 0

            # Construire le rapport
            date_str = now.strftime("%d/%m/%Y")
            source_lines = "\n".join(
                f"  - {src}: {cnt}" for src, cnt in sources
            ) if sources else "  Aucune"

            report = (
                f"RAPPORT QUOTIDIEN TENDO\n"
                f"{date_str}\n"
                f"{'=' * 30}\n\n"
                f"PUBLICATIONS\n"
                f"  Nouvelles aujourd'hui: {pubs_count}\n"
                f"  Enrichies par IA: {enriched}\n"
                f"  Total en base: {total_publications}\n\n"
                f"SOURCES DU JOUR\n"
                f"{source_lines}\n\n"
                f"NOTIFICATIONS\n"
                f"  Envoyees aujourd'hui: {notifs_count}\n\n"
                f"UTILISATEURS\n"
                f"  Actifs: {active_users}\n"
                f"  Total inscrits: {total}\n\n"
                f"Tendo fonctionne normalement."
            )

            await send_message(ADMIN_PHONE, report)
            logger.info(f"[Scheduler] Rapport quotidien envoye a {ADMIN_PHONE}")

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

    # Notifications continues -- toutes les 30 min de 9h a 18h GMT
    scheduler.add_job(
        job_send_notifications,
        CronTrigger(minute="0,30", hour="9-18"),
        id="notifications",
        name="Envoi notifications (9h-18h GMT, /30min)",
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

    # Enrichissement IA des publications (lecture PDF) -- 1h apres scraping + toutes les 8h
    scheduler.add_job(
        job_enrich_publications,
        CronTrigger(hour="7,15,23", minute="0"),
        id="enrich_publications",
        name="Enrichissement IA des publications",
        replace_existing=True,
    )

    # Nettoyage des AO expires -- chaque jour a 1h du matin
    scheduler.add_job(
        job_cleanup_expired_publications,
        CronTrigger(hour="1", minute="0"),
        id="cleanup_expired",
        name="Nettoyage AO expires",
        replace_existing=True,
    )

    # Discussions proactives -- mardi et vendredi a 10h
    scheduler.add_job(
        job_proactive_discussions,
        CronTrigger(day_of_week="tue,fri", hour="10", minute="0"),
        id="proactive_discussions",
        name="Discussions proactives",
        replace_existing=True,
    )

    # Rapport quotidien -- chaque jour a 19h GMT au +2290140809108
    scheduler.add_job(
        job_daily_report,
        CronTrigger(hour="19", minute="0"),
        id="daily_report",
        name="Rapport quotidien admin (19h GMT)",
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

    # Auto-apprentissage -- chaque dimanche a 3h du matin
    scheduler.add_job(
        job_self_learn,
        CronTrigger(day_of_week="sun", hour="3", minute="0"),
        id="self_learn",
        name="Auto-apprentissage (analyse marches)",
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

"""Configuration Celery pour les tâches asynchrones."""

from celery import Celery
from celery.schedules import crontab

from app.config import settings

celery_app = Celery(
    "tendo",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Africa/Porto-Novo",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)

# Tâches planifiées
celery_app.conf.beat_schedule = {
    # Envoi des notifications toutes les 30 minutes
    "send-notifications": {
        "task": "app.workers.tasks.send_pending_notifications",
        "schedule": crontab(minute="*/30"),
    },
    # Vérification des emails toutes les heures
    "check-email-responses": {
        "task": "app.workers.tasks.check_email_responses",
        "schedule": crontab(minute=0),
    },
    # Vérification des abonnements expirés (quotidien)
    "check-expired-subscriptions": {
        "task": "app.workers.tasks.check_expired_subscriptions",
        "schedule": crontab(hour=0, minute=0),
    },
    # Scraping quotidien (6h du matin)
    "daily-scraping": {
        "task": "app.workers.tasks.run_all_scrapers",
        "schedule": crontab(hour=6, minute=0),
    },
}

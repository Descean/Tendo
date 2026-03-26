"""Service de monitoring et alertes -- Tendo.

Envoie des alertes WhatsApp aux administrateurs quand :
- Un scraper echoue
- Les notifications echouent
- L'application detecte un probleme critique
- Le health check echoue
"""

import asyncio
from datetime import datetime, timezone
from typing import Optional

from app.config import settings
from app.utils.logger import logger


async def send_admin_alert(
    title: str,
    message: str,
    severity: str = "WARNING",
) -> None:
    """Envoie une alerte WhatsApp a tous les numeros admin configures.

    severity: INFO, WARNING, CRITICAL
    """
    from app.services.whatsapp import send_message

    if not settings.admin_phones:
        logger.warning(f"[Monitor] Alerte non envoyee (pas de numeros admin): {title}")
        return

    severity_label = {
        "INFO": "INFORMATION",
        "WARNING": "ATTENTION",
        "CRITICAL": "ALERTE CRITIQUE",
    }.get(severity, "ALERTE")

    timestamp = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")

    alert_text = (
        f"{severity_label} -- TENDO\n\n"
        f"{title}\n\n"
        f"{message}\n\n"
        f"Date : {timestamp}"
    )

    for phone in settings.admin_phones:
        try:
            await send_message(phone, alert_text)
            logger.info(f"[Monitor] Alerte envoyee a {phone}: {title}")
            await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"[Monitor] Erreur envoi alerte a {phone}: {e}")


async def alert_scraper_failure(scraper_name: str, error: str) -> None:
    """Alerte quand un scraper echoue."""
    await send_admin_alert(
        title=f"Scraper en echec : {scraper_name}",
        message=f"Le scraper {scraper_name} a echoue.\n\nErreur : {error[:300]}",
        severity="WARNING",
    )


async def alert_notification_failure(user_count: int, error: str) -> None:
    """Alerte quand les notifications echouent massivement."""
    await send_admin_alert(
        title="Echec des notifications",
        message=(
            f"L'envoi de notifications a echoue pour {user_count} utilisateur(s).\n\n"
            f"Erreur : {error[:300]}"
        ),
        severity="CRITICAL",
    )


async def alert_payment_failure(user_phone: str, error: str) -> None:
    """Alerte quand un paiement echoue."""
    await send_admin_alert(
        title="Echec de paiement",
        message=(
            f"Paiement echoue pour {user_phone}.\n\n"
            f"Erreur : {error[:300]}"
        ),
        severity="WARNING",
    )


async def alert_system_critical(component: str, error: str) -> None:
    """Alerte pour un probleme systeme critique."""
    await send_admin_alert(
        title=f"Probleme systeme : {component}",
        message=f"Le composant {component} rencontre un probleme critique.\n\nDetails : {error[:300]}",
        severity="CRITICAL",
    )


async def send_daily_report(
    scraped_count: int = 0,
    notifications_sent: int = 0,
    active_users: int = 0,
    errors_count: int = 0,
) -> None:
    """Envoie un rapport quotidien aux admins."""
    await send_admin_alert(
        title="Rapport quotidien Tendo",
        message=(
            f"Publications scrapees : {scraped_count}\n"
            f"Notifications envoyees : {notifications_sent}\n"
            f"Utilisateurs actifs : {active_users}\n"
            f"Erreurs : {errors_count}"
        ),
        severity="INFO",
    )

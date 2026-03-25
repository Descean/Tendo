"""Router Admin – dashboard, stats, gestion utilisateurs et déclenchement manuel."""

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_

from app.config import settings
from app.utils.db import get_db
from app.utils.security import get_current_user
from app.models.user import User, SubscriptionStatus
from app.models.publication import Publication
from app.models.subscription import Subscription
from app.models.notification import Notification
from app.utils.logger import logger

router = APIRouter(prefix="/admin", tags=["Admin"])


def _is_admin(user: User):
    """Vérifie que l'utilisateur est admin."""
    admin_emails = settings.admin_emails
    # Admin par email ou par numéro de téléphone (le premier utilisateur inscrit)
    if user.email_address and user.email_address in admin_emails:
        return True
    if user.id == 1:  # Premier utilisateur = admin
        return True
    return False


async def get_admin_user(user: User = Depends(get_current_user)):
    """Dépendance : vérifie les droits admin."""
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Accès administrateur requis")
    return user


# ─── Dashboard Stats ───

@router.get("/stats")
async def dashboard_stats(
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Statistiques globales du dashboard admin."""
    now = datetime.now(timezone.utc)
    last_30d = now - timedelta(days=30)
    last_7d = now - timedelta(days=7)

    # Utilisateurs
    total_users = (await db.execute(select(func.count(User.id)))).scalar()
    active_users = (await db.execute(
        select(func.count(User.id)).where(
            User.subscription_status.in_([
                SubscriptionStatus.ACTIVE.value,
                SubscriptionStatus.TRIAL.value,
            ])
        )
    )).scalar()
    new_users_7d = (await db.execute(
        select(func.count(User.id)).where(User.created_at >= last_7d)
    )).scalar()

    # Publications
    total_publications = (await db.execute(
        select(func.count(Publication.id))
    )).scalar()
    new_publications_30d = (await db.execute(
        select(func.count(Publication.id)).where(
            Publication.created_at >= last_30d
        )
    )).scalar()

    # Abonnements payants
    paid_subs = (await db.execute(
        select(func.count(Subscription.id)).where(
            Subscription.status == "paid"
        )
    )).scalar()
    revenue = (await db.execute(
        select(func.coalesce(func.sum(Subscription.amount), 0)).where(
            Subscription.status == "paid"
        )
    )).scalar()

    # Notifications
    total_notifications = (await db.execute(
        select(func.count(Notification.id))
    )).scalar()

    return {
        "users": {
            "total": total_users,
            "active": active_users,
            "new_last_7_days": new_users_7d,
        },
        "publications": {
            "total": total_publications,
            "new_last_30_days": new_publications_30d,
        },
        "subscriptions": {
            "paid_total": paid_subs,
            "revenue_total_xof": float(revenue),
        },
        "notifications": {
            "total_sent": total_notifications,
        },
    }


# ─── Gestion Utilisateurs ───

@router.get("/users")
async def list_users(
    status: Optional[str] = Query(None, description="Filtrer par statut: trial, active, expired"),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Liste les utilisateurs avec filtres et pagination."""
    stmt = select(User).order_by(User.created_at.desc())

    if status:
        stmt = stmt.where(User.subscription_status == status)

    offset = (page - 1) * per_page
    stmt = stmt.offset(offset).limit(per_page)

    result = await db.execute(stmt)
    users = result.scalars().all()

    total = (await db.execute(
        select(func.count(User.id)).where(
            User.subscription_status == status if status else True
        )
    )).scalar()

    return {
        "users": [
            {
                "id": u.id,
                "phone_number": u.phone_number,
                "name": u.name,
                "company": u.company,
                "subscription_status": u.subscription_status,
                "subscription_plan": u.subscription_plan,
                "sectors": u.sectors,
                "regions": u.regions,
                "created_at": u.created_at.isoformat() if u.created_at else None,
                "is_active": u.is_active,
            }
            for u in users
        ],
        "total": total,
        "page": page,
        "per_page": per_page,
    }


@router.patch("/users/{user_id}/toggle-active")
async def toggle_user_active(
    user_id: int,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Active/désactive un utilisateur."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Utilisateur non trouvé")

    user.is_active = not user.is_active
    await db.flush()
    return {"id": user.id, "is_active": user.is_active}


# --- Declenchement Manuel (avec cle secrete pour simplifier) ---

def _check_admin_key(key: str):
    """Verifie la cle admin pour les endpoints de declenchement."""
    if key != settings.secret_key:
        raise HTTPException(status_code=403, detail="Cle admin invalide")


@router.post("/trigger/scraping")
async def trigger_scraping(key: str = ""):
    """Declenche le scraping manuellement. Passer ?key=SECRET_KEY"""
    _check_admin_key(key)
    from app.scheduler import job_run_scrapers
    import asyncio

    asyncio.create_task(job_run_scrapers())
    logger.info("[Admin] Scraping declenche manuellement")
    return {"status": "started", "message": "Scraping lance en arriere-plan"}


@router.post("/trigger/notifications")
async def trigger_notifications(key: str = ""):
    """Declenche l'envoi des notifications manuellement. Passer ?key=SECRET_KEY"""
    _check_admin_key(key)
    from app.scheduler import job_send_notifications
    import asyncio

    asyncio.create_task(job_send_notifications())
    logger.info("[Admin] Notifications declenchees manuellement")
    return {"status": "started", "message": "Envoi des notifications lance"}


@router.get("/scheduler/status")
async def scheduler_status(admin: User = Depends(get_admin_user)):
    """Retourne l'état du scheduler et des prochaines exécutions."""
    from app.scheduler import scheduler

    jobs = []
    for job in scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "name": job.name,
            "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
        })

    return {
        "running": scheduler.running,
        "jobs": jobs,
    }


# ─── Publications Admin ───

@router.delete("/publications/{pub_id}")
async def delete_publication(
    pub_id: int,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Supprime une publication."""
    result = await db.execute(select(Publication).where(Publication.id == pub_id))
    pub = result.scalar_one_or_none()
    if not pub:
        raise HTTPException(status_code=404, detail="Publication non trouvée")
    await db.delete(pub)
    return {"message": f"Publication {pub_id} supprimée"}

"""Router Users – API REST pour l'application web."""

from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.utils.db import get_db
from app.utils.security import get_current_user, create_access_token
from app.models.user import User
from app.models.notification import Notification
from app.models.publication import Publication
from app.schemas.user import UserResponse, UserUpdate, UserProfile
from app.schemas.notification import NotificationResponse

router = APIRouter(prefix="/users", tags=["Users"])


@router.post("/auth/token")
async def get_token(phone_number: str, db: AsyncSession = Depends(get_db)):
    """Génère un token JWT à partir du numéro WhatsApp (magic link)."""
    result = await db.execute(select(User).where(User.phone_number == phone_number))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="Utilisateur non trouvé")

    token = create_access_token(data={"sub": user.phone_number, "user_id": user.id})
    return {"access_token": token, "token_type": "bearer"}


@router.get("/me", response_model=UserProfile)
async def get_profile(user: User = Depends(get_current_user)):
    """Retourne le profil de l'utilisateur connecté."""
    return user


@router.put("/me", response_model=UserResponse)
async def update_profile(
    update: UserUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Met à jour le profil utilisateur."""
    update_data = update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(user, key, value)
    await db.flush()
    return user


@router.get("/me/notifications", response_model=List[NotificationResponse])
async def get_notifications(
    skip: int = 0,
    limit: int = 20,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Retourne l'historique des notifications."""
    result = await db.execute(
        select(Notification)
        .where(Notification.user_id == user.id)
        .order_by(Notification.sent_at.desc())
        .offset(skip)
        .limit(limit)
    )
    return result.scalars().all()


@router.get("/me/publications", response_model=List)
async def get_user_publications(
    skip: int = 0,
    limit: int = 20,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Retourne les publications filtrées selon les préférences."""
    query = select(Publication).order_by(Publication.created_at.desc())

    # Filtrer par secteurs de l'utilisateur
    if user.sectors:
        # On filtre côté Python car le filtrage JSON varie selon les SGBD
        result = await db.execute(query.limit(100))
        pubs = result.scalars().all()
        filtered = [
            p for p in pubs
            if not p.sectors or any(s in (user.sectors or []) for s in p.sectors)
        ]
        return filtered[skip:skip + limit]

    result = await db.execute(query.offset(skip).limit(limit))
    return result.scalars().all()


@router.post("/me/export")
async def export_data(
    format: str = "json",
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Exporte les données utilisateur (RGPD)."""
    result = await db.execute(
        select(Notification).where(Notification.user_id == user.id)
    )
    notifications = result.scalars().all()

    export = {
        "user": {
            "phone_number": user.phone_number,
            "name": user.name,
            "company": user.company,
            "sectors": user.sectors,
            "regions": user.regions,
            "created_at": user.created_at.isoformat() if user.created_at else None,
        },
        "notifications_count": len(notifications),
    }

    return export


@router.delete("/me")
async def delete_account(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Supprime le compte utilisateur (RGPD)."""
    await db.delete(user)
    return {"message": "Compte supprimé avec succès"}

"""Router Subscriptions – gestion des abonnements."""

from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.utils.db import get_db
from app.utils.security import get_current_user
from app.models.user import User
from app.models.subscription import Subscription
from app.schemas.subscription import SubscriptionResponse
from app.services.payment import PLANS

router = APIRouter(prefix="/subscriptions", tags=["Subscriptions"])


@router.get("/plans")
async def get_plans():
    """Retourne les plans d'abonnement disponibles."""
    return PLANS


@router.get("/me", response_model=List[SubscriptionResponse])
async def get_my_subscriptions(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Retourne les abonnements de l'utilisateur."""
    result = await db.execute(
        select(Subscription)
        .where(Subscription.user_id == user.id)
        .order_by(Subscription.start_date.desc())
    )
    return result.scalars().all()


@router.get("/me/current", response_model=SubscriptionResponse | None)
async def get_current_subscription(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Retourne l'abonnement actif."""
    result = await db.execute(
        select(Subscription)
        .where(Subscription.user_id == user.id, Subscription.status == "paid")
        .order_by(Subscription.end_date.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()

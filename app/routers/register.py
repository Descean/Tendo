"""Router d'inscription web - formulaire landing page."""

import re
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Union

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.utils.db import AsyncSessionLocal
from app.utils.logger import logger
from app.models.user import User, SubscriptionStatus

router = APIRouter(prefix="/api/v1", tags=["Registration"])


class RegisterRequest(BaseModel):
    fullname: str = Field(min_length=3, max_length=100)
    phone: str = Field(min_length=8, max_length=20)
    email: Optional[str] = None
    company: str = Field(min_length=2, max_length=200)
    sector: Optional[str] = None
    sectors: Optional[List[str]] = None
    region: str
    plan: str = "trial"
    device_fp: Optional[str] = None
    form_token: Optional[str] = None


def _sanitize(text: str) -> str:
    """Anti-injection: supprime les caracteres dangereux."""
    if not text:
        return ""
    # Supprimer balises HTML, scripts, SQL injection patterns
    text = re.sub(r'[<>"\'`;(){}]', '', text)
    text = re.sub(r'(--|/\*|\*/|xp_|exec|drop|insert|delete|update|union|select)', '', text, flags=re.IGNORECASE)
    return text.strip()


def _normalize_phone(phone: str) -> str:
    """Normalise le numero de telephone au format +229XXXXXXXX."""
    phone = re.sub(r'[\s\-\(\)]', '', phone)
    if not phone.startswith('+'):
        if phone.startswith('229'):
            phone = '+' + phone
        elif phone.startswith('00229'):
            phone = '+' + phone[2:]
        else:
            phone = '+229' + phone
    return phone


@router.post("/register")
async def register_user(data: RegisterRequest):
    """Inscription depuis le formulaire web de la landing page."""
    # Sanitize all inputs
    fullname = _sanitize(data.fullname)
    company = _sanitize(data.company)
    phone = _normalize_phone(data.phone)
    email = data.email.strip() if data.email else None

    # Validate phone format
    if not re.match(r'^\+[0-9]{10,15}$', phone):
        raise HTTPException(400, "Numero de telephone invalide")

    # Validate email if provided
    if email and not re.match(r'^[^\s@]+@[^\s@]+\.[^\s@]+$', email):
        raise HTTPException(400, "Adresse email invalide")

    async with AsyncSessionLocal() as db:
        # Check if user already exists
        existing = await db.execute(
            select(User).where(User.phone_number == phone)
        )
        user = existing.scalar_one_or_none()

        # Build sectors list from either field
        sectors_list = data.sectors if data.sectors else ([data.sector] if data.sector else [])

        if user:
            # Update profile if incomplete
            if not user.name:
                user.name = fullname
            if not user.company:
                user.company = company
            if email and not user.email_address:
                user.email_address = email
            if sectors_list and (not user.sectors or user.sectors == []):
                user.sectors = sectors_list
            if data.region and (not user.regions or user.regions == []):
                user.regions = [data.region]
            await db.commit()

            logger.info(f"[Register] Profil mis a jour: {phone}")
            response = {
                "success": True,
                "user_id": user.id,
                "message": "Profil mis a jour. Ouvrez WhatsApp pour commencer.",
                "is_existing": True,
            }

            # Generer un lien de paiement si plan payant demande
            plan = data.plan if data.plan in ("essentiel", "premium") else None
            if plan:
                try:
                    from app.services.payment import create_payment_link
                    payment = await create_payment_link(
                        user_phone=phone,
                        plan=plan,
                        user_name=user.name or fullname,
                        user_email=email or user.email_address or f"{phone}@tendo.shiftup.bj",
                    )
                    if payment and payment.get("payment_link"):
                        response["payment_url"] = payment["payment_link"]
                except Exception as e:
                    logger.error(f"[Register] Erreur paiement (existing): {e}")

            return response

        # Create new user
        plan = data.plan if data.plan in ("trial", "essentiel", "premium") else "trial"

        user = User(
            phone_number=phone,
            name=fullname,
            company=company,
            email_address=email,
            sectors=sectors_list,
            regions=[data.region] if data.region else [],
            subscription_status=SubscriptionStatus.TRIAL.value,
            subscription_plan=plan if plan != "trial" else None,
            trial_end=datetime.now(timezone.utc) + timedelta(days=7),
            is_active=True,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)

        logger.info(f"[Register] Nouveau: {fullname} ({phone}) - {plan} - device:{data.device_fp}")

        response = {
            "success": True,
            "user_id": user.id,
            "message": "Inscription reussie ! Votre essai gratuit de 7 jours est active.",
            "plan": plan,
        }

        # Si plan payant, generer un lien de paiement FedaPay
        if plan in ("essentiel", "premium"):
            try:
                from app.services.payment import create_payment_link
                payment = await create_payment_link(
                    user_phone=phone,
                    plan=plan,
                    user_name=fullname,
                    user_email=email or f"{phone}@tendo.shiftup.bj",
                )
                if payment and payment.get("payment_link"):
                    response["payment_url"] = payment["payment_link"]
            except Exception as e:
                logger.error(f"[Register] Erreur paiement: {e}")
                # On continue sans paiement - l'user est en trial

        return response

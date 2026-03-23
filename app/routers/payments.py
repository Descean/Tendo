"""Router Payments – paiements FedaPay (Mobile Money MTN/Moov)."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.utils.db import get_db
from app.utils.security import get_current_user
from app.utils.logger import logger
from app.models.user import User, SubscriptionStatus, SubscriptionPlan
from app.models.subscription import Subscription, PaymentStatus
from app.services.payment import (
    create_payment_link, verify_transaction, verify_webhook_signature, PLANS,
)
from app.services.whatsapp import send_message
from app.schemas.payment import PaymentInitiate, PaymentResponse

router = APIRouter(prefix="/payments", tags=["Payments"])


@router.post("/initiate", response_model=PaymentResponse)
async def initiate_payment(
    payload: PaymentInitiate,
    user: User = Depends(get_current_user),
):
    """Crée un lien de paiement FedaPay pour l'utilisateur."""
    if payload.plan not in PLANS:
        raise HTTPException(
            status_code=400,
            detail=f"Plan invalide. Disponibles: {list(PLANS.keys())}",
        )

    result = await create_payment_link(
        user_phone=user.phone_number,
        plan=payload.plan,
        user_name=user.name,
        user_email=user.email_address,
    )
    return result


@router.post("/webhook/fedapay")
async def fedapay_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """Webhook FedaPay pour confirmation de paiement.

    FedaPay envoie un POST avec l'événement de transaction.
    Événements : transaction.approved, transaction.declined, transaction.canceled
    """
    body = await request.body()

    # Vérifier la signature si configurée
    signature = request.headers.get("X-Fedapay-Signature", "")
    if not verify_webhook_signature(body, signature):
        logger.warning("Signature webhook FedaPay invalide")
        raise HTTPException(status_code=401, detail="Signature invalide")

    data = await request.json()
    event = data.get("name", "")
    entity = data.get("entity", {})

    logger.info(f"Webhook FedaPay: event={event}, id={entity.get('id')}")

    if event == "transaction.approved":
        await _process_successful_payment(entity, db)
    elif event == "transaction.declined":
        logger.warning(f"Paiement refusé: id={entity.get('id')}")
    elif event == "transaction.canceled":
        logger.info(f"Paiement annulé: id={entity.get('id')}")

    return {"status": "ok"}


@router.get("/callback")
async def payment_callback(
    id: str = "",
    status: str = "",
    db: AsyncSession = Depends(get_db),
):
    """Callback après redirection FedaPay.

    FedaPay redirige avec ?id=xxx&status=approved
    """
    if status == "approved" and id:
        try:
            tx_info = await verify_transaction(id)
            if tx_info["status"] == "approved":
                await _process_successful_payment_from_verify(tx_info, db)
                return {"message": "Paiement confirmé ! Retournez sur WhatsApp."}
        except Exception as e:
            logger.error(f"Erreur vérification callback: {e}")

    return {"message": "Paiement non confirmé. Contactez le support si nécessaire."}


async def _process_successful_payment(entity: dict, db: AsyncSession):
    """Traite un paiement réussi depuis le webhook FedaPay."""
    metadata = entity.get("metadata", {})
    tx_ref = metadata.get("tx_ref", "")
    amount = entity.get("amount", 0)
    phone = metadata.get("phone_number", "")
    plan = metadata.get("plan", "")

    if not phone or not plan:
        # Essayer d'extraire du tx_ref (format: tendo-plan-phone-random)
        parts = tx_ref.split("-")
        if len(parts) >= 3:
            plan = plan or parts[1]
            phone = phone or parts[2]
        else:
            logger.error(f"Impossible d'extraire les infos du webhook: tx_ref={tx_ref}, metadata={metadata}")
            return

    await _activate_subscription(phone, plan, amount, str(entity.get("id", tx_ref)), db)


async def _process_successful_payment_from_verify(tx_info: dict, db: AsyncSession):
    """Traite un paiement vérifié via callback."""
    metadata = tx_info.get("metadata", {})
    phone = metadata.get("phone_number", "")
    plan = metadata.get("plan", "")
    amount = tx_info.get("amount", 0)
    tx_ref = metadata.get("tx_ref", "")

    if not phone or not plan:
        parts = tx_ref.split("-")
        if len(parts) >= 3:
            plan = plan or parts[1]
            phone = phone or parts[2]
        else:
            logger.error(f"Impossible d'extraire les infos: tx_ref={tx_ref}")
            return

    await _activate_subscription(phone, plan, amount, tx_info.get("transaction_id", tx_ref), db)


async def _activate_subscription(
    phone: str, plan: str, amount: float, payment_id: str, db: AsyncSession
):
    """Active l'abonnement d'un utilisateur après paiement confirmé."""
    # Trouver l'utilisateur
    result = await db.execute(select(User).where(User.phone_number == phone))
    user = result.scalar_one_or_none()

    if not user:
        # Essayer avec le format +229...
        if not phone.startswith("+"):
            result = await db.execute(select(User).where(User.phone_number == f"+{phone}"))
            user = result.scalar_one_or_none()

    if not user:
        logger.error(f"Utilisateur non trouvé pour paiement: phone={phone}")
        return

    # Durée selon le plan
    duration_days = PLANS.get(plan, {}).get("duration_days", 30)

    # Créer l'abonnement
    subscription = Subscription(
        user_id=user.id,
        plan=plan,
        start_date=datetime.now(timezone.utc),
        end_date=datetime.now(timezone.utc) + timedelta(days=duration_days),
        payment_id=payment_id,
        amount=amount,
        status=PaymentStatus.PAID,
    )
    db.add(subscription)

    # Mettre à jour l'utilisateur
    user.subscription_status = SubscriptionStatus.ACTIVE
    user.subscription_plan = SubscriptionPlan(plan) if plan in ["essentiel", "premium"] else None

    await db.flush()

    # Notifier l'utilisateur sur WhatsApp
    try:
        await send_message(
            user.phone_number,
            f"✅ *Paiement confirmé !*\n\n"
            f"Plan : {PLANS.get(plan, {}).get('name', plan)}\n"
            f"Montant : {amount:,.0f} FCFA\n"
            f"Valable jusqu'au : {subscription.end_date.strftime('%d/%m/%Y')}\n\n"
            f"Merci pour votre confiance ! 🎉",
        )
    except Exception as e:
        logger.error(f"Erreur notification paiement: {e}")

    logger.info(f"Paiement FedaPay traité: user={user.id}, plan={plan}, amount={amount}")

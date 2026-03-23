"""Router webhook WhatsApp – supporte Meta Cloud API ET Twilio."""

import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request, Response, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import settings
from app.utils.db import get_db
from app.utils.logger import logger
from app.models.user import User, SubscriptionStatus
from app.models.publication import Publication
from app.models.notification import Notification
from app.models.email_tracking import EmailTracking
from app.services import whatsapp, claude
from app.services.whatsapp import (
    WELCOME_MESSAGE, MENU_MESSAGE, PLANS_MESSAGE, SUBSCRIPTION_EXPIRED,
    meta_verify_signature, _meta_verify_webhook,
)
from app.services.payment import create_payment_link
from app.services.email_manager import send_dossier_request

router = APIRouter(prefix="/webhook", tags=["Webhook"])

PROVIDER = settings.whatsapp_provider


# ═══════════════════════════════════════════
#  WEBHOOK META WHATSAPP CLOUD API
# ═══════════════════════════════════════════

@router.get("/whatsapp")
async def whatsapp_verify(request: Request):
    """Vérification du webhook (Meta envoie un GET avec challenge)."""
    if PROVIDER == "meta":
        params = dict(request.query_params)
        challenge = _meta_verify_webhook(params)
        if challenge:
            return PlainTextResponse(content=challenge)
        raise HTTPException(status_code=403, detail="Verification failed")
    # Twilio healthcheck
    return {"status": "ok", "service": "Tendo WhatsApp Webhook"}


@router.post("/whatsapp")
async def whatsapp_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """Webhook pour les messages WhatsApp entrants (Meta ou Twilio)."""

    if PROVIDER == "meta":
        return await _handle_meta_webhook(request, db)
    else:
        return await _handle_twilio_webhook(request, db)


async def _handle_meta_webhook(request: Request, db: AsyncSession):
    """Traite un webhook Meta WhatsApp Cloud API."""
    # IMPORTANT: lire le body brut EN PREMIER (avant .json()) pour la vérification de signature
    raw_body = await request.body()

    # Vérifier la signature si app_secret est configuré
    if settings.meta_app_secret:
        signature = request.headers.get("X-Hub-Signature-256", "")
        if not meta_verify_signature(raw_body, signature):
            logger.warning("[Meta] Signature webhook invalide")
            raise HTTPException(status_code=403, detail="Invalid signature")

    body = json.loads(raw_body)

    # Meta envoie plusieurs types d'événements
    if body.get("object") != "whatsapp_business_account":
        return {"status": "ignored"}

    # Parcourir les messages
    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            messages = value.get("messages", [])

            for msg in messages:
                msg_type = msg.get("type")
                from_number = msg.get("from", "")

                if msg_type == "text":
                    text = msg["text"]["body"].strip()
                    await _process_message(f"+{from_number}", text, db)

                # Ignorer silencieusement les statuts et autres types
                # (read receipts, delivered, etc.)

    return {"status": "ok"}


async def _handle_twilio_webhook(request: Request, db: AsyncSession):
    """Traite un webhook Twilio."""
    form_data = await request.form()

    from_number = form_data.get("From", "").replace("whatsapp:", "")
    body = form_data.get("Body", "").strip()

    if not from_number or not body:
        return Response(content="<Response></Response>", media_type="application/xml")

    await _process_message(from_number, body, db)
    return Response(content="<Response></Response>", media_type="application/xml")


# ═══════════════════════════════════════════
#  LOGIQUE MÉTIER (commune aux 2 providers)
# ═══════════════════════════════════════════

async def _process_message(from_number: str, body: str, db: AsyncSession):
    """Traite un message WhatsApp entrant (logique commune)."""
    logger.info(f"Message WhatsApp de {from_number}: {body[:100]}")

    # Récupérer ou créer l'utilisateur
    result = await db.execute(select(User).where(User.phone_number == from_number))
    user = result.scalar_one_or_none()

    if not user:
        user = User(
            phone_number=from_number,
            subscription_status=SubscriptionStatus.TRIAL.value,
            trial_end=datetime.now(timezone.utc) + timedelta(days=7),
        )
        db.add(user)
        await db.flush()
        await whatsapp.send_message(from_number, WELCOME_MESSAGE)
        return

    # Vérifier le trial expiré → passer en EXPIRED
    if (
        user.subscription_status == SubscriptionStatus.TRIAL.value
        and user.trial_end
        and user.trial_end < datetime.now(timezone.utc)
    ):
        user.subscription_status = SubscriptionStatus.EXPIRED.value
        logger.info(f"[Trial] Expiré pour {from_number}")

    # Vérifier l'abonnement
    if user.subscription_status == SubscriptionStatus.EXPIRED.value:
        allowed_cmds = ("abonnement", "paiement", "plans", "support", "premium", "essentiel",
                        "2", "4", "5", "02", "04", "05")
        if body.lower() not in allowed_cmds:
            await whatsapp.send_message(from_number, SUBSCRIPTION_EXPIRED)
            return

    # Gérer le flux d'inscription en cours
    if user.conversation_state and user.conversation_state.startswith("inscription_"):
        reply = await _handle_registration_flow(user, body, db)
        await whatsapp.send_message(from_number, reply)
        return

    # Détecter l'intention
    intent_result = await claude.detect_intent(body)
    intent = intent_result["intent"]

    reply = await _handle_intent(intent, body, user, db)
    await whatsapp.send_message(from_number, reply)


async def _handle_intent(intent: str, body: str, user: User, db: AsyncSession) -> str:
    """Gère l'intention détectée et retourne le message de réponse."""

    if intent == "MENU":
        return MENU_MESSAGE

    elif intent == "INSCRIPTION":
        user.conversation_state = "inscription_nom"
        user.conversation_data = {}
        return "📝 *Inscription Tendo*\n\nCommençons ! Quel est votre *nom complet* ?"

    elif intent == "ABONNEMENT":
        return PLANS_MESSAGE

    elif intent == "HISTORIQUE":
        return await _get_history(user, db)

    elif intent == "PAIEMENT":
        # Détecter si l'utilisateur a demandé un plan spécifique
        body_lower = body.lower()
        if "premium" in body_lower:
            return await _handle_payment(user, plan="premium")
        return await _handle_payment(user, plan="essentiel")

    elif intent == "SUPPORT":
        return (
            "👤 *Support Tendo*\n\n"
            "Un agent va vous contacter prochainement.\n"
            "En attendant, vous pouvez nous écrire à : support@shiftup.bj"
        )

    elif intent == "DEMANDE_DOSSIER":
        return await _handle_dossier_request(body, user, db)

    else:  # QUESTION → conversation IA
        is_premium = user.subscription_plan == "premium"
        reply = await claude.chat(body, is_premium=is_premium)
        return reply


async def _handle_registration_flow(user: User, body: str, db: AsyncSession) -> str:
    """Gère le flux d'inscription pas à pas."""
    state = user.conversation_state
    data = user.conversation_data or {}

    if state == "inscription_nom":
        data["name"] = body
        user.conversation_data = data
        user.conversation_state = "inscription_entreprise"
        return "🏢 Quel est le nom de votre *entreprise* ? (tapez 'Passer' si vous êtes un particulier)"

    elif state == "inscription_entreprise":
        if body.lower() != "passer":
            data["company"] = body
        user.conversation_data = data
        user.conversation_state = "inscription_secteurs"
        return (
            "🏗️ Quels *secteurs* vous intéressent ?\n"
            "Choisissez parmi :\n"
            "1. BTP\n2. Fournitures\n3. Services\n4. TIC\n5. Santé\n"
            "6. Éducation\n7. Agriculture\n8. Environnement\n9. Transport\n10. Énergie\n\n"
            "Envoyez les numéros séparés par des virgules (ex: 1,3,5)"
        )

    elif state == "inscription_secteurs":
        sectors_map = {
            "1": "BTP", "2": "Fournitures", "3": "Services", "4": "TIC",
            "5": "Santé", "6": "Éducation", "7": "Agriculture",
            "8": "Environnement", "9": "Transport", "10": "Énergie",
        }
        selected = [sectors_map.get(s.strip(), s.strip()) for s in body.split(",")]
        data["sectors"] = [s for s in selected if s]
        user.conversation_data = data
        user.conversation_state = "inscription_regions"
        return (
            "📍 Quelles *régions* vous intéressent ?\n"
            "1. Cotonou\n2. Porto-Novo\n3. Parakou\n4. Abomey\n"
            "5. Bohicon\n6. Djougou\n7. Natitingou\n8. Lokossa\n"
            "9. Tout le Bénin\n10. CEDEAO\n\n"
            "Envoyez les numéros séparés par des virgules"
        )

    elif state == "inscription_regions":
        regions_map = {
            "1": "Cotonou", "2": "Porto-Novo", "3": "Parakou", "4": "Abomey",
            "5": "Bohicon", "6": "Djougou", "7": "Natitingou", "8": "Lokossa",
            "9": "Tout le Bénin", "10": "CEDEAO",
        }
        selected = [regions_map.get(r.strip(), r.strip()) for r in body.split(",")]
        data["regions"] = [r for r in selected if r]
        user.conversation_data = data
        user.conversation_state = "inscription_sources"
        return (
            "📰 Quelles *sources* souhaitez-vous surveiller ?\n"
            "1. marches-publics.bj\n2. ARMP\n3. gouv.bj\n"
            "4. ADPME\n5. ABE\n6. BAD\n7. AFD\n8. Toutes\n\n"
            "Envoyez les numéros séparés par des virgules"
        )

    elif state == "inscription_sources":
        sources_map = {
            "1": "marches-publics.bj", "2": "ARMP", "3": "gouv.bj",
            "4": "ADPME", "5": "ABE", "6": "BAD", "7": "AFD", "8": "Toutes",
        }
        selected = [sources_map.get(s.strip(), s.strip()) for s in body.split(",")]
        if "Toutes" in selected:
            selected = list(sources_map.values())[:-1]
        data["sources"] = selected

        # Finaliser l'inscription
        user.name = data.get("name", user.name)
        user.company = data.get("company")
        user.sectors = data.get("sectors", [])
        user.regions = data.get("regions", [])
        user.preferred_sources = data.get("sources", [])
        user.conversation_state = None
        user.conversation_data = None

        return (
            f"✅ *Inscription terminée !*\n\n"
            f"👤 Nom : {user.name}\n"
            f"🏢 Entreprise : {user.company or 'Non renseigné'}\n"
            f"🏗️ Secteurs : {', '.join(user.sectors)}\n"
            f"📍 Régions : {', '.join(user.regions)}\n"
            f"📰 Sources : {', '.join(user.preferred_sources)}\n\n"
            f"Vous recevrez vos premières alertes dès demain !\n"
            f"Tapez *Menu* pour voir les options."
        )

    # État inconnu, réinitialiser
    user.conversation_state = None
    return MENU_MESSAGE


async def _get_history(user: User, db: AsyncSession) -> str:
    """Retourne l'historique des notifications de l'utilisateur."""
    result = await db.execute(
        select(Notification)
        .where(Notification.user_id == user.id)
        .order_by(Notification.sent_at.desc())
        .limit(5)
    )
    notifications = result.scalars().all()

    if not notifications:
        return "📭 Aucune alerte récente. Vos alertes personnalisées arriveront bientôt !"

    lines = ["📋 *Vos 5 dernières alertes :*\n"]
    for i, notif in enumerate(notifications, 1):
        pub_result = await db.execute(
            select(Publication).where(Publication.id == notif.publication_id)
        )
        pub = pub_result.scalar_one_or_none()
        if pub:
            date_str = notif.sent_at.strftime("%d/%m/%Y")
            lines.append(f"{i}. [{date_str}] {pub.title[:60]}")
            lines.append(f"   Réf: {pub.reference}")

    return "\n".join(lines)


async def _handle_payment(user: User, plan: str = "essentiel") -> str:
    """Génère un lien de paiement FedaPay."""
    plan_names = {"essentiel": "Essentiel", "premium": "Premium"}
    plan_label = plan_names.get(plan, "Essentiel")

    try:
        result = await create_payment_link(
            user_phone=user.phone_number,
            plan=plan,
            user_name=user.name,
        )
        other_plan = "Premium (15 000 FCFA)" if plan == "essentiel" else "Essentiel (5 000 FCFA)"
        other_cmd = "Premium" if plan == "essentiel" else "Essentiel"

        return (
            f"💳 *Paiement Tendo*\n\n"
            f"📦 Plan : *{plan_label}*\n"
            f"💰 Montant : *{result['amount']:,.0f} FCFA*\n\n"
            f"👉 Cliquez pour payer (Mobile Money MTN/Moov) :\n"
            f"{result['payment_link']}\n\n"
            f"Pour le plan {other_plan}, tapez *{other_cmd}*."
        )
    except Exception as e:
        logger.error(f"Erreur paiement: {e}")
        return "❌ Erreur lors de la création du paiement. Veuillez réessayer ou contactez le support."


async def _handle_dossier_request(body: str, user: User, db: AsyncSession) -> str:
    """Gère la demande de dossier d'AO."""
    ref = body.replace("/demander_dossier", "").strip()
    if not ref:
        return "📄 Veuillez préciser la référence de l'appel d'offres.\nEx: */demander_dossier AO-MARC-12345678*"

    result = await db.execute(
        select(Publication).where(Publication.reference == ref)
    )
    pub = result.scalar_one_or_none()

    if not pub:
        return f"❌ Publication '{ref}' non trouvée. Vérifiez la référence."

    if not pub.authority_email:
        return (
            f"📄 *{pub.title}*\n\n"
            f"L'adresse email de l'autorité contractante n'est pas disponible.\n"
            f"Veuillez envoyer l'email de l'autorité et nous ferons la demande pour vous."
        )

    if user.subscription_plan != "premium" and user.subscription_status != SubscriptionStatus.TRIAL.value:
        return "🔒 La demande automatique de dossier est réservée au *Plan Premium*.\nTapez *Abonnement* pour upgrader."

    result = await send_dossier_request(
        authority_email=pub.authority_email,
        publication_reference=pub.reference,
        publication_title=pub.title,
        requester_name=user.name or user.phone_number,
        requester_company=user.company,
        cc_email=user.email_address,
    )

    if result["success"]:
        tracking = EmailTracking(
            user_id=user.id,
            publication_id=pub.id,
            email_sent_to=pub.authority_email,
            subject=result["subject"],
        )
        db.add(tracking)

        return (
            f"✅ *Demande envoyée !*\n\n"
            f"📄 {pub.title}\n"
            f"📧 Email envoyé à : {pub.authority_email}\n\n"
            f"Nous vous notifierons dès réception de la réponse."
        )
    else:
        return f"❌ Erreur lors de l'envoi : {result.get('error', 'Inconnue')}"

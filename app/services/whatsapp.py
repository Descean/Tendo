"""Service WhatsApp – support Meta Cloud API (gratuit) ET Twilio (payant).

Par défaut, utilise la Meta WhatsApp Cloud API (1000 conversations/mois gratuites).
Bascule vers Twilio si WHATSAPP_PROVIDER=twilio dans le .env.
"""

from typing import Optional, List

import httpx

from app.config import settings
from app.utils.logger import logger

# ── Choix du provider ──
PROVIDER = settings.whatsapp_provider  # "meta" ou "twilio"


# ═══════════════════════════════════════════
#  META WHATSAPP CLOUD API (GRATUIT)
# ═══════════════════════════════════════════

async def _meta_send_message(to: str, body: str) -> dict:
    """Envoie un message texte via la Meta WhatsApp Cloud API."""
    phone = to.replace("whatsapp:", "").replace("+", "").strip()

    url = f"https://graph.facebook.com/v21.0/{settings.meta_phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {settings.meta_access_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "text",
        "text": {"body": body},
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(url, json=payload, headers=headers, timeout=30)
        data = response.json()

    if response.status_code == 200 and "messages" in data:
        msg_id = data["messages"][0]["id"]
        logger.info(f"[Meta] Message envoyé à {phone}: id={msg_id}")
        return {"id": msg_id, "status": "sent"}
    else:
        logger.error(f"[Meta] Erreur envoi à {phone}: {data}")
        raise Exception(f"Meta API error: {data.get('error', data)}")


async def _meta_send_template(to: str, template_name: str, language: str = "fr", components: Optional[list] = None) -> dict:
    """Envoie un message template pré-approuvé via Meta."""
    phone = to.replace("whatsapp:", "").replace("+", "").strip()

    url = f"https://graph.facebook.com/v21.0/{settings.meta_phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {settings.meta_access_token}",
        "Content-Type": "application/json",
    }
    template_obj = {
        "name": template_name,
        "language": {"code": language},
    }
    if components:
        template_obj["components"] = components

    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "template",
        "template": template_obj,
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(url, json=payload, headers=headers, timeout=30)
        data = response.json()

    if response.status_code == 200 and "messages" in data:
        msg_id = data["messages"][0]["id"]
        logger.info(f"[Meta] Template envoyé à {phone}: id={msg_id}")
        return {"id": msg_id, "status": "sent"}
    else:
        logger.error(f"[Meta] Erreur template à {phone}: {data}")
        raise Exception(f"Meta API error: {data.get('error', data)}")


def _meta_verify_webhook(request_args: dict) -> Optional[str]:
    """Vérifie le webhook Meta (GET challenge).

    Meta envoie hub.mode, hub.verify_token, hub.challenge.
    Retourne hub.challenge si valide, None sinon.
    """
    mode = request_args.get("hub.mode")
    token = request_args.get("hub.verify_token")
    challenge = request_args.get("hub.challenge")

    if mode == "subscribe" and token == settings.meta_verify_token:
        logger.info("[Meta] Webhook vérifié avec succès")
        return challenge
    logger.warning(f"[Meta] Échec vérification webhook: mode={mode}")
    return None


import hmac
import hashlib


def meta_verify_signature(payload: bytes, signature: str) -> bool:
    """Vérifie la signature HMAC-SHA256 du webhook Meta."""
    if not signature.startswith("sha256="):
        return False
    expected = hmac.new(
        settings.meta_app_secret.encode(),
        payload,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature)


# ═══════════════════════════════════════════
#  TWILIO (PAYANT – OPTIONNEL)
# ═══════════════════════════════════════════

async def _twilio_send_message(to: str, body: str) -> dict:
    """Envoie un message WhatsApp via Twilio."""
    from twilio.rest import Client
    client = Client(settings.twilio_account_sid, settings.twilio_auth_token)

    message = client.messages.create(
        from_=settings.twilio_whatsapp_number,
        to=f"whatsapp:{to}" if not to.startswith("whatsapp:") else to,
        body=body,
    )
    logger.info(f"[Twilio] Message envoyé à {to}: SID={message.sid}")
    return {"sid": message.sid, "status": message.status}


def _twilio_validate_request(url: str, params: dict, signature: str) -> bool:
    """Valide la signature d'un webhook Twilio."""
    from twilio.request_validator import RequestValidator
    validator = RequestValidator(settings.twilio_auth_token)
    return validator.validate(url, params, signature)


# ═══════════════════════════════════════════
#  INTERFACE PUBLIQUE (auto-switch)
# ═══════════════════════════════════════════

async def send_message(to: str, body: str) -> dict:
    """Envoie un message WhatsApp via le provider configuré."""
    try:
        if PROVIDER == "twilio":
            return await _twilio_send_message(to, body)
        else:
            return await _meta_send_message(to, body)
    except Exception as e:
        logger.error(f"Erreur envoi WhatsApp ({PROVIDER}) à {to}: {e}")
        raise


async def send_template_message(to: str, template_name: str, **kwargs) -> dict:
    """Envoie un message template via le provider configuré."""
    if PROVIDER == "twilio":
        from twilio.rest import Client
        client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
        message = client.messages.create(
            from_=settings.twilio_whatsapp_number,
            to=f"whatsapp:{to}" if not to.startswith("whatsapp:") else to,
            content_sid=template_name,
            content_variables=kwargs.get("variables", {}),
        )
        return {"sid": message.sid, "status": message.status}
    else:
        return await _meta_send_template(
            to, template_name,
            language=kwargs.get("language", "fr"),
            components=kwargs.get("components"),
        )


# ═══════════════════════════════════════════
#  MESSAGES PRÉDÉFINIS – TENDO
# ═══════════════════════════════════════════

WELCOME_MESSAGE = """🎉 *Bienvenue sur Tendo !*

Votre assistant intelligent de veille sur les marchés publics au Bénin et en Afrique de l'Ouest.

🎁 Vous bénéficiez d'un *essai gratuit de 7 jours*.

Tapez *Menu* pour découvrir ce que je peux faire pour vous ! 👇"""

MENU_MESSAGE = """📋 *Menu Tendo*

1️⃣ *Inscription* – Configurer vos préférences
2️⃣ *Abonnement* – Voir les plans
3️⃣ *Historique* – Vos dernières alertes
4️⃣ *Paiement* – Gérer votre abonnement
5️⃣ *Support* – Contacter un agent

💡 Vous pouvez aussi me poser directement votre question sur les marchés publics !"""

PLANS_MESSAGE = """💎 *Plans d'abonnement Tendo*

📦 *Plan Essentiel* – 5 000 FCFA/mois
• Alertes quotidiennes personnalisées
• Résumés IA des appels d'offres
• Recherche dans la base de données

🏆 *Plan Premium* – 15 000 FCFA/mois
• Tout le Plan Essentiel +
• Assistant IA expert (réponses détaillées)
• Demande automatique de dossiers d'AO
• Surveillance de boîte email
• Support prioritaire

Tapez *Paiement* pour souscrire."""

SUBSCRIPTION_EXPIRED = """⚠️ *Votre abonnement a expiré*

Pour continuer à recevoir vos alertes marchés publics, renouvelez votre abonnement.

Tapez *Abonnement* pour voir les plans disponibles."""

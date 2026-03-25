"""Service WhatsApp -- support Meta Cloud API (gratuit) ET Twilio (payant).

Par defaut, utilise la Meta WhatsApp Cloud API (1000 conversations/mois gratuites).
Bascule vers Twilio si WHATSAPP_PROVIDER=twilio dans le .env.
"""

from typing import Optional, List

import httpx

from app.config import settings
from app.utils.logger import logger

# -- Choix du provider --
PROVIDER = settings.whatsapp_provider  # "meta" ou "twilio"


# ================================================
#  META WHATSAPP CLOUD API (GRATUIT)
# ================================================

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
        logger.info(f"[Meta] Message envoye a {phone}: id={msg_id}")
        return {"id": msg_id, "status": "sent"}
    else:
        logger.error(f"[Meta] Erreur envoi a {phone}: {data}")
        raise Exception(f"Meta API error: {data.get('error', data)}")


async def _meta_send_template(to: str, template_name: str, language: str = "fr", components: Optional[list] = None) -> dict:
    """Envoie un message template pre-approuve via Meta."""
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
        logger.info(f"[Meta] Template envoye a {phone}: id={msg_id}")
        return {"id": msg_id, "status": "sent"}
    else:
        logger.error(f"[Meta] Erreur template a {phone}: {data}")
        raise Exception(f"Meta API error: {data.get('error', data)}")


def _meta_verify_webhook(request_args: dict) -> Optional[str]:
    """Verifie le webhook Meta (GET challenge)."""
    mode = request_args.get("hub.mode")
    token = request_args.get("hub.verify_token")
    challenge = request_args.get("hub.challenge")

    if mode == "subscribe" and token == settings.meta_verify_token:
        logger.info("[Meta] Webhook verifie avec succes")
        return challenge
    logger.warning(f"[Meta] Echec verification webhook: mode={mode}")
    return None


import hmac
import hashlib


def meta_verify_signature(payload: bytes, signature: str) -> bool:
    """Verifie la signature HMAC-SHA256 du webhook Meta."""
    if not signature.startswith("sha256="):
        return False
    expected = hmac.new(
        settings.meta_app_secret.encode(),
        payload,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature)


# ================================================
#  TWILIO (PAYANT -- OPTIONNEL)
# ================================================

async def _twilio_send_message(to: str, body: str) -> dict:
    """Envoie un message WhatsApp via Twilio."""
    from twilio.rest import Client
    client = Client(settings.twilio_account_sid, settings.twilio_auth_token)

    message = client.messages.create(
        from_=settings.twilio_whatsapp_number,
        to=f"whatsapp:{to}" if not to.startswith("whatsapp:") else to,
        body=body,
    )
    logger.info(f"[Twilio] Message envoye a {to}: SID={message.sid}")
    return {"sid": message.sid, "status": message.status}


def _twilio_validate_request(url: str, params: dict, signature: str) -> bool:
    """Valide la signature d'un webhook Twilio."""
    from twilio.request_validator import RequestValidator
    validator = RequestValidator(settings.twilio_auth_token)
    return validator.validate(url, params, signature)


# ================================================
#  INTERFACE PUBLIQUE (auto-switch)
# ================================================

async def send_message(to: str, body: str) -> dict:
    """Envoie un message WhatsApp via le provider configure."""
    try:
        if PROVIDER == "twilio":
            return await _twilio_send_message(to, body)
        else:
            return await _meta_send_message(to, body)
    except Exception as e:
        logger.error(f"Erreur envoi WhatsApp ({PROVIDER}) a {to}: {e}")
        raise


async def send_template_message(to: str, template_name: str, **kwargs) -> dict:
    """Envoie un message template via le provider configure."""
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


# ================================================
#  MESSAGES PREDEFINIS -- TENDO (sans emojis)
# ================================================

WELCOME_MESSAGE = """Bienvenue sur Tendo.

Votre assistant de veille sur les marches publics au Benin et en Afrique de l'Ouest.

Vous beneficiez d'un essai gratuit de 7 jours.

Tapez *Menu* pour decouvrir les fonctionnalites disponibles."""

MENU_MESSAGE = """MENU TENDO

1 - Inscription (configurer vos preferences)
2 - Abonnement (voir les plans)
3 - Historique (vos dernieres alertes)
4 - Paiement (gerer votre abonnement)
5 - Support (contacter un agent)

Vous pouvez aussi me poser directement votre question sur les marches publics."""

PLANS_MESSAGE = """PLANS D'ABONNEMENT TENDO

--- Plan Essentiel -- 5 000 FCFA/mois ---
- Alertes quotidiennes personnalisees
- Resumes IA des appels d'offres
- Recherche dans la base de donnees

--- Plan Premium -- 15 000 FCFA/mois ---
- Tout le Plan Essentiel +
- Assistant IA expert (reponses detaillees)
- Demande automatique de dossiers d'AO
- Surveillance de boite email
- Support prioritaire

Tapez *Essentiel* ou *Premium* pour souscrire."""

SUBSCRIPTION_EXPIRED = """Votre periode d'essai a expire.

Pour continuer a recevoir vos alertes marches publics, souscrivez a un abonnement.

Tapez *Abonnement* pour voir les plans disponibles."""

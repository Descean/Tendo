"""Service de paiement FedaPay – Mobile Money (MTN/Moov) pour le Bénin."""

import uuid
import hashlib
import hmac
from typing import Optional

import httpx

from app.config import settings
from app.utils.logger import logger

# Mode sandbox ou live
FEDAPAY_BASE_URL = (
    "https://sandbox-api.fedapay.com/v1"
    if settings.app_env == "development"
    else "https://api.fedapay.com/v1"
)

# Plans et tarifs (en FCFA)
PLANS = {
    "essentiel": {
        "name": "Plan Essentiel",
        "amount": 5000,
        "currency": "XOF",
        "description": "Alertes quotidiennes + Résumés IA + Recherche",
        "duration_days": 30,
    },
    "premium": {
        "name": "Plan Premium",
        "amount": 15000,
        "currency": "XOF",
        "description": "Tout Essentiel + Demande dossiers + Surveillance email + Support prioritaire",
        "duration_days": 30,
    },
}


def get_headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.fedapay_secret_key}",
        "Content-Type": "application/json",
    }


async def create_payment_link(
    user_phone: str,
    plan: str,
    user_name: str = "",
    user_email: Optional[str] = None,
) -> dict:
    """Crée une transaction FedaPay et retourne le lien de paiement."""
    if plan not in PLANS:
        raise ValueError(f"Plan inconnu: {plan}. Plans disponibles: {list(PLANS.keys())}")

    plan_info = PLANS[plan]
    tx_ref = f"tendo-{plan}-{user_phone}-{uuid.uuid4().hex[:8]}"

    # Étape 1 : Créer la transaction
    payload = {
        "description": f"Tendo - {plan_info['name']}",
        "amount": plan_info["amount"],
        "currency": {"iso": plan_info["currency"]},
        "callback_url": f"{settings.base_url}/payments/callback",
        "customer": {
            "firstname": user_name.split()[0] if user_name else "Client",
            "lastname": " ".join(user_name.split()[1:]) if user_name and len(user_name.split()) > 1 else "Tendo",
            "email": user_email or f"{user_phone.replace('+', '')}@shiftup.bj",
            "phone_number": {"number": user_phone, "country": "bj"},
        },
        "metadata": {
            "phone_number": user_phone,
            "plan": plan,
            "tx_ref": tx_ref,
        },
    }

    async with httpx.AsyncClient(timeout=30) as client:
        # Créer la transaction
        response = await client.post(
            f"{FEDAPAY_BASE_URL}/transactions",
            json=payload,
            headers=get_headers(),
        )
        data = response.json()

    if response.status_code not in (200, 201):
        logger.error(f"Erreur FedaPay création transaction: {data}")
        raise Exception(f"Erreur création paiement: {data.get('message', 'Erreur inconnue')}")

    transaction = data.get("v1/transaction", data.get("data", data))
    transaction_id = transaction.get("id")

    if not transaction_id:
        logger.error(f"Pas d'ID transaction dans la réponse FedaPay: {data}")
        raise Exception("Erreur: pas d'ID transaction retourné")

    # Étape 2 : Générer le token de paiement (lien)
    async with httpx.AsyncClient(timeout=30) as client:
        token_response = await client.post(
            f"{FEDAPAY_BASE_URL}/transactions/{transaction_id}/token",
            json={},
            headers=get_headers(),
        )
        token_data = token_response.json()

    token = token_data.get("token")
    if not token:
        logger.error(f"Erreur FedaPay token: {token_data}")
        raise Exception("Erreur génération du lien de paiement")

    payment_url = f"https://process.fedapay.com/{token}"

    logger.info(f"Lien de paiement FedaPay créé: tx_ref={tx_ref}, id={transaction_id}")
    return {
        "payment_link": payment_url,
        "tx_ref": tx_ref,
        "transaction_id": str(transaction_id),
        "amount": plan_info["amount"],
        "currency": plan_info["currency"],
    }


async def verify_transaction(transaction_id: str) -> dict:
    """Vérifie le statut d'une transaction FedaPay."""
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            f"{FEDAPAY_BASE_URL}/transactions/{transaction_id}",
            headers=get_headers(),
        )
        data = response.json()

    if response.status_code != 200:
        logger.error(f"Erreur vérification transaction FedaPay: {data}")
        raise Exception(f"Transaction non trouvée: {transaction_id}")

    transaction = data.get("v1/transaction", data.get("data", data))
    metadata = transaction.get("metadata", {})

    # FedaPay statuts: pending, approved, declined, canceled, refunded
    status = transaction.get("status", "unknown")

    return {
        "transaction_id": str(transaction.get("id")),
        "tx_ref": metadata.get("tx_ref", ""),
        "status": status,
        "amount": transaction.get("amount", 0),
        "currency": transaction.get("currency", {}).get("iso", "XOF") if isinstance(transaction.get("currency"), dict) else "XOF",
        "customer": transaction.get("customer"),
        "metadata": metadata,
    }


def verify_webhook_signature(payload: bytes, signature: str) -> bool:
    """Verifie la signature du webhook FedaPay (HMAC-SHA256).

    FedaPay peut envoyer la signature sous differents formats :
    - Hex brut : "abcdef1234..."
    - Prefixe : "sha256=abcdef1234..."
    """
    if not settings.fedapay_webhook_secret:
        logger.warning("FEDAPAY_WEBHOOK_SECRET non configure — webhook accepte sans verification")
        return True

    if not signature:
        return False

    # Calculer le HMAC attendu
    expected = hmac.new(
        settings.fedapay_webhook_secret.encode(),
        payload,
        hashlib.sha256,
    ).hexdigest()

    # Essayer le format brut
    if hmac.compare_digest(expected, signature):
        return True

    # Essayer avec prefixe sha256=
    if signature.startswith("sha256="):
        return hmac.compare_digest(expected, signature[7:])

    # Essayer en comparant avec le prefixe
    if hmac.compare_digest(f"sha256={expected}", signature):
        return True

    logger.debug(f"Signature attendue: {expected[:20]}..., recue: {signature[:20]}...")
    return False

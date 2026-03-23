"""Schemas Pydantic pour les paiements FedaPay."""

from typing import Optional

from pydantic import BaseModel


class PaymentInitiate(BaseModel):
    plan: str  # "essentiel" ou "premium"


class PaymentResponse(BaseModel):
    payment_link: str
    tx_ref: str
    transaction_id: str
    amount: float
    currency: str = "XOF"


class FedaPayWebhook(BaseModel):
    name: str  # "transaction.approved", "transaction.declined", etc.
    entity: dict


class PaymentVerification(BaseModel):
    transaction_id: str
    tx_ref: str
    status: str
    amount: float
    currency: str
    customer: Optional[dict] = None
    metadata: Optional[dict] = None

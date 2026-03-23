"""Schemas Pydantic pour Subscription."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class SubscriptionCreate(BaseModel):
    plan: str
    amount: float


class SubscriptionResponse(BaseModel):
    id: int
    user_id: int
    plan: str
    start_date: datetime
    end_date: datetime
    payment_id: Optional[str] = None
    amount: float
    status: str

    model_config = {"from_attributes": True}

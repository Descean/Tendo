"""Schemas Pydantic pour Notification."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class NotificationResponse(BaseModel):
    id: int
    user_id: int
    publication_id: int
    sent_at: datetime
    opened: bool
    interaction_type: Optional[str] = None

    model_config = {"from_attributes": True}

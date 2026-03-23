"""Modèle User – utilisateur WhatsApp."""

import enum
from datetime import datetime
from typing import Optional, List

from sqlalchemy import String, Boolean, DateTime, JSON, Integer, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class SubscriptionStatus(str, enum.Enum):
    TRIAL = "trial"
    ACTIVE = "active"
    EXPIRED = "expired"
    CANCELED = "canceled"


class SubscriptionPlan(str, enum.Enum):
    ESSENTIEL = "essentiel"
    PREMIUM = "premium"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    phone_number: Mapped[str] = mapped_column(String(20), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), default="")
    company: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    sectors: Mapped[Optional[List]] = mapped_column(JSON, default=list)
    regions: Mapped[Optional[List]] = mapped_column(JSON, default=list)
    preferred_sources: Mapped[Optional[List]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    trial_end: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    subscription_status: Mapped[str] = mapped_column(
        String(20), default=SubscriptionStatus.TRIAL.value
    )
    subscription_plan: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=True
    )
    email_monitoring_consent: Mapped[bool] = mapped_column(Boolean, default=False)
    email_address: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Conversation state for registration flow
    conversation_state: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    conversation_data: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Relations
    subscriptions = relationship("Subscription", back_populates="user", lazy="selectin")
    notifications = relationship("Notification", back_populates="user", lazy="selectin")
    email_trackings = relationship("EmailTracking", back_populates="user", lazy="selectin")

    def __repr__(self) -> str:
        return f"<User(id={self.id}, phone={self.phone_number}, name={self.name})>"

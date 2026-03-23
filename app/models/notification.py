"""Modèle Notification – alertes envoyées aux utilisateurs."""

from datetime import datetime
from typing import Optional

from sqlalchemy import String, Boolean, DateTime, Integer, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    publication_id: Mapped[int] = mapped_column(Integer, ForeignKey("publications.id"), nullable=False)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    opened: Mapped[bool] = mapped_column(Boolean, default=False)
    interaction_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # Relations
    user = relationship("User", back_populates="notifications")
    publication = relationship("Publication", back_populates="notifications")

    def __repr__(self) -> str:
        return f"<Notification(id={self.id}, user={self.user_id}, pub={self.publication_id})>"

"""Modèle EmailTracking – suivi des demandes de dossiers par email."""

from datetime import datetime
from typing import Optional

from sqlalchemy import String, Boolean, DateTime, Text, Integer, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class EmailTracking(Base):
    __tablename__ = "email_trackings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    publication_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("publications.id"), nullable=True
    )
    email_sent_to: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    response_received: Mapped[bool] = mapped_column(Boolean, default=False)
    response_content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    response_received_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relations
    user = relationship("User", back_populates="email_trackings")
    publication = relationship("Publication", back_populates="email_trackings")

    def __repr__(self) -> str:
        return f"<EmailTracking(id={self.id}, to={self.email_sent_to})>"

"""Modèle Publication – appels d'offres et opportunités."""

from datetime import datetime
from typing import Optional, List

from sqlalchemy import String, Float, Boolean, DateTime, Text, JSON, Integer, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Publication(Base):
    __tablename__ = "publications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(255), nullable=False)
    reference: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    budget: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    deadline: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    pdf_url: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    html_content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(String(100), default="marché")
    sectors: Mapped[Optional[List]] = mapped_column(JSON, default=list)
    regions: Mapped[Optional[List]] = mapped_column(JSON, default=list)
    published_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    is_processed: Mapped[bool] = mapped_column(Boolean, default=False)

    # Adresse email de l'autorité contractante (pour demande de dossier)
    authority_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    authority_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Relations
    notifications = relationship("Notification", back_populates="publication", lazy="selectin")
    email_trackings = relationship("EmailTracking", back_populates="publication", lazy="selectin")

    def __repr__(self) -> str:
        return f"<Publication(id={self.id}, ref={self.reference}, source={self.source})>"

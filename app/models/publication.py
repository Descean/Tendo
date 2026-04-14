"""Modèle Publication – appels d'offres et opportunités."""

from datetime import datetime
from typing import Optional, List

from sqlalchemy import String, Float, Boolean, DateTime, Text, JSON, Integer, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Publication(Base):
    __tablename__ = "publications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    reference: Mapped[str] = mapped_column(Text, unique=True, nullable=False, index=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    budget: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    deadline: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    pdf_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    html_content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(Text, default="marché")
    sectors: Mapped[Optional[List]] = mapped_column(JSON, default=list)
    regions: Mapped[Optional[List]] = mapped_column(JSON, default=list)
    published_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    is_processed: Mapped[bool] = mapped_column(Boolean, default=False)

    # Classification fine du document
    document_type: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, index=True
    )  # AAO, DAO, PV_ATTRIBUTION, PV_OUVERTURE, DECISION_ARMP, AMI, RFQ, RFP, PPM, ADDITIF

    # Source de financement
    financing_source: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )  # BN, BM, BAD, UE, AFD, GIZ, UNGM, etc.

    # Pays (pour l'expansion regionale)
    country: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, default="Bénin"
    )

    # Adresse email de l'autorité contractante (pour demande de dossier)
    authority_email: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    authority_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Contenu PDF extrait (texte brut) + analyse IA
    pdf_content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    technical_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    required_documents: Mapped[Optional[List]] = mapped_column(JSON, nullable=True)
    qualification_criteria: Mapped[Optional[List]] = mapped_column(JSON, nullable=True)
    guarantee_amount: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Lots et delais (extraits par deepseek_reader)
    lots_detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    lots_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    delivery_delay: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relations
    notifications = relationship("Notification", back_populates="publication", lazy="selectin")
    email_trackings = relationship("EmailTracking", back_populates="publication", lazy="selectin")

    def __repr__(self) -> str:
        return f"<Publication(id={self.id}, ref={self.reference}, source={self.source})>"

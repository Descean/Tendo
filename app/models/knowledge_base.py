"""Modèle KnowledgeBase – base de connaissances métier de Tendo.

Stocke les connaissances sur les marchés publics, les procédures,
les réglementations, le lexique, etc. Utilisé par l'IA pour
enrichir ses réponses avec des données structurées.
"""

from datetime import datetime
from typing import Optional, List

from sqlalchemy import String, Boolean, DateTime, Text, JSON, Integer, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class KnowledgeBase(Base):
    __tablename__ = "knowledge_base"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Classification
    category: Mapped[str] = mapped_column(
        String(100), nullable=False, index=True
    )  # "procedure", "reglementation", "lexique", "document_type", "source_info", "armp_decision"

    subcategory: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True
    )  # ex: "DAO", "AMI", "recours", "sanction"

    # Contenu
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Mots-clés pour la recherche
    keywords: Mapped[Optional[List]] = mapped_column(
        JSON, default=list
    )  # ["avis", "appel d'offres", "tender notice", ...]

    # Métadonnées
    country: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True
    )  # "Bénin", "Sénégal", "UEMOA", "international"

    source_url: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    language: Mapped[str] = mapped_column(String(10), default="fr")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), nullable=True
    )

    def __repr__(self) -> str:
        return f"<KnowledgeBase(id={self.id}, cat={self.category}, title={self.title[:50]})>"

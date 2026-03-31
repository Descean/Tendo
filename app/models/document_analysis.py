"""Modèle DocumentAnalysis – analyses IA de documents PDF.

Stocke les résultats d'analyse des documents (scrapés ou envoyés par WhatsApp),
incluant la classification, le texte extrait, et le matching avec les utilisateurs.
"""

from datetime import datetime
from typing import Optional, List

from sqlalchemy import (
    String, Boolean, DateTime, Text, JSON, Integer, Float, ForeignKey, func
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class DocumentAnalysis(Base):
    __tablename__ = "document_analyses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Lien optionnel avec une publication scrapée
    publication_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("publications.id"), nullable=True, index=True
    )

    # Source du document
    source_type: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # "scraper", "whatsapp", "url", "upload"

    source_url: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    original_filename: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # Texte extrait
    extracted_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    extraction_method: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True
    )  # "pypdf", "ocr_tesseract", "gemini_vision", "mixed"
    page_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    char_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Classification IA
    document_type: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True, index=True
    )  # "AAO", "DAO", "PV_ATTRIBUTION", "PV_OUVERTURE", "DECISION_ARMP", "AMI", "RFQ", "PPM", "ADDITIF", etc.

    document_subtype: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True
    )  # "recours", "sanction", "exclusion", etc.

    classification_confidence: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True
    )  # 0.0 à 1.0

    # Données extraites par l'IA
    ai_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ai_analysis: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Métadonnées structurées extraites
    extracted_data: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True
    )
    # Contient selon le type :
    # Pour AAO/DAO : {
    #   "objet": "...", "reference": "...", "deadline": "...",
    #   "budget": 0, "autorite_contractante": "...",
    #   "pieces_administratives": [...], "pieces_techniques": [...],
    #   "pieces_financieres": [...], "criteres_evaluation": [...],
    #   "mode_passation": "AOO/AOR/DRP/AMI",
    #   "financement": "BN/BM/BAD/UE/...",
    # }
    # Pour PV_ATTRIBUTION : {
    #   "titulaire": "...", "montant": 0, "reference_ao": "...",
    #   "nombre_offres": 0, "date_attribution": "...",
    # }
    # Pour DECISION_ARMP : {
    #   "numero_decision": "...", "type": "recours/sanction/autosaisine",
    #   "entreprises_concernees": [...], "duree_exclusion": "...",
    #   "motifs": [...],
    # }

    # Secteurs et régions détectés
    detected_sectors: Mapped[Optional[List]] = mapped_column(JSON, default=list)
    detected_regions: Mapped[Optional[List]] = mapped_column(JSON, default=list)
    detected_country: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Statut
    is_analyzed: Mapped[bool] = mapped_column(Boolean, default=False)
    analysis_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Relations
    publication = relationship("Publication", foreign_keys=[publication_id], lazy="selectin")

    def __repr__(self) -> str:
        return (
            f"<DocumentAnalysis(id={self.id}, type={self.document_type}, "
            f"source={self.source_type})>"
        )

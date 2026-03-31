"""Add document intelligence: knowledge_base, document_analyses tables + publication fields.

Revision ID: a1b2c3d4e5f6
Revises: d4f3d172c286
Create Date: 2026-03-26
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "a1b2c3d4e5f6"
down_revision = "d4f3d172c286"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Nouveaux champs sur publications ──
    op.add_column("publications", sa.Column("document_type", sa.String(100), nullable=True))
    op.add_column("publications", sa.Column("financing_source", sa.String(100), nullable=True))
    op.add_column("publications", sa.Column("country", sa.String(100), nullable=True, server_default="Bénin"))
    op.create_index("ix_publications_document_type", "publications", ["document_type"])

    # ── Table knowledge_base ──
    op.create_table(
        "knowledge_base",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("category", sa.String(100), nullable=False, index=True),
        sa.Column("subcategory", sa.String(100), nullable=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("summary", sa.Text, nullable=True),
        sa.Column("keywords", sa.JSON, default=[]),
        sa.Column("country", sa.String(100), nullable=True),
        sa.Column("source_url", sa.String(1000), nullable=True),
        sa.Column("language", sa.String(10), default="fr"),
        sa.Column("is_active", sa.Boolean, default=True),
        sa.Column("version", sa.Integer, default=1),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ── Table document_analyses ──
    op.create_table(
        "document_analyses",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("publication_id", sa.Integer, sa.ForeignKey("publications.id"), nullable=True, index=True),
        sa.Column("source_type", sa.String(50), nullable=False),
        sa.Column("source_url", sa.String(1000), nullable=True),
        sa.Column("original_filename", sa.String(500), nullable=True),
        sa.Column("extracted_text", sa.Text, nullable=True),
        sa.Column("extraction_method", sa.String(50), nullable=True),
        sa.Column("page_count", sa.Integer, nullable=True),
        sa.Column("char_count", sa.Integer, nullable=True),
        sa.Column("document_type", sa.String(100), nullable=True, index=True),
        sa.Column("document_subtype", sa.String(100), nullable=True),
        sa.Column("classification_confidence", sa.Float, nullable=True),
        sa.Column("ai_summary", sa.Text, nullable=True),
        sa.Column("ai_analysis", sa.Text, nullable=True),
        sa.Column("extracted_data", sa.JSON, nullable=True),
        sa.Column("detected_sectors", sa.JSON, default=[]),
        sa.Column("detected_regions", sa.JSON, default=[]),
        sa.Column("detected_country", sa.String(100), nullable=True),
        sa.Column("is_analyzed", sa.Boolean, default=False),
        sa.Column("analysis_error", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("document_analyses")
    op.drop_table("knowledge_base")
    op.drop_index("ix_publications_document_type", "publications")
    op.drop_column("publications", "country")
    op.drop_column("publications", "financing_source")
    op.drop_column("publications", "document_type")

"""Add PDF analysis fields to publications.

Revision ID: b2c3d4e5f6g7
Revises: a1b2c3d4e5f6
Create Date: 2026-04-01
"""

from alembic import op
import sqlalchemy as sa

revision = "b2c3d4e5f6g7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("publications", sa.Column("pdf_content", sa.Text(), nullable=True))
    op.add_column("publications", sa.Column("technical_summary", sa.Text(), nullable=True))
    op.add_column("publications", sa.Column("required_documents", sa.Text(), nullable=True))
    op.add_column("publications", sa.Column("qualification_criteria", sa.Text(), nullable=True))
    op.add_column("publications", sa.Column("guarantee_amount", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("publications", "guarantee_amount")
    op.drop_column("publications", "qualification_criteria")
    op.drop_column("publications", "required_documents")
    op.drop_column("publications", "technical_summary")
    op.drop_column("publications", "pdf_content")

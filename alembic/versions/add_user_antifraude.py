"""Add anti-fraude fields to users: registration_ip, device_fingerprint, has_used_trial.

Revision ID: b2c3d4e5f6g7
Revises: add_pdf_fields
Create Date: 2026-04-06
"""

from alembic import op
import sqlalchemy as sa

revision = "c3d4e5f6g7h8"
down_revision = "b2c3d4e5f6g7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("registration_ip", sa.String(45), nullable=True))
    op.add_column("users", sa.Column("device_fingerprint", sa.String(255), nullable=True))
    op.add_column("users", sa.Column("has_used_trial", sa.Boolean(), server_default="true", nullable=False))


def downgrade() -> None:
    op.drop_column("users", "has_used_trial")
    op.drop_column("users", "device_fingerprint")
    op.drop_column("users", "registration_ip")

"""Add channel column and unique constraint to notifications.

Revision ID: c3d4e5f6g7h8
Revises: b2c3d4e5f6g7
Create Date: 2026-04-14
"""

from alembic import op
import sqlalchemy as sa

revision = "c3d4e5f6g7h8"
down_revision = "b2c3d4e5f6g7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Ajouter la colonne channel avec valeur par defaut 'whatsapp'
    op.add_column(
        "notifications",
        sa.Column("channel", sa.String(20), nullable=False, server_default="whatsapp"),
    )
    # Creer la contrainte UNIQUE (user_id, publication_id, channel)
    op.create_unique_constraint(
        "uq_user_pub_channel", "notifications", ["user_id", "publication_id", "channel"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_user_pub_channel", "notifications", type_="unique")
    op.drop_column("notifications", "channel")

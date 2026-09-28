"""agent secret owner

Revision ID: 4259933782af
Revises: 2b8629fd9a5f
Create Date: 2026-09-28

Ein Secret gehoert dem, der es anlegt. Bisher konnte ein Mitglied ein Secret
anlegen, es danach aber weder sehen noch zuweisen — es fiel in den allgemeinen
Topf, den Nicht-Admins nur ueber die Rollen-Freigabe erreichen. Bestehende
Secrets (von Admins angelegt) behalten owner_id NULL: Firmen-Secrets wie bisher.
"""
from alembic import op
import sqlalchemy as sa

revision = "4259933782af"
down_revision = "2b8629fd9a5f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_secrets", sa.Column("owner_id", sa.String(), nullable=True))
    op.create_index("ix_agent_secrets_owner_id", "agent_secrets", ["owner_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_secrets_owner_id", table_name="agent_secrets")
    op.drop_column("agent_secrets", "owner_id")

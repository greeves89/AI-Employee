"""Keys mit erlaubten Zielen fuer den Egress-Proxy

Ein Key mit ``erlaubte_hosts`` geht bei eingeschaltetem Egress-Proxy nur noch als
Platzhalter in den Agenten; der Proxy setzt den Wert erst auf dem Weg zu diesen
Zielen ein (core/egress.py). Leer = wie bisher.

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-10-31
"""
import sqlalchemy as sa
from alembic import op

revision = "f3a4b5c6d7e8"
down_revision = "e2f3a4b5c6d7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_secrets", sa.Column("erlaubte_hosts", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_secrets", "erlaubte_hosts")

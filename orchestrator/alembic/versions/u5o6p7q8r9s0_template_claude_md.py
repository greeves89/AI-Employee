"""Add claude_md column to agent_templates

Revision ID: u5o6p7q8r9s0b
Revises: ub5oc6pd7qe8
Create Date: 2026-04-18

"""
from alembic import op

revision = "u5o6p7q8r9s0b"
down_revision = "ub5oc6pd7qe8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Spalte anlegen, falls sie fehlt — ohne vorher zu LESEN (#689).

    Siehe die Schwesterrevision ``ub5oc6pd7qe8``: ein SELECT mit ``fetchone()``
    scheitert im Offline-Modus (``--sql``) und blockiert damit die Vorschau fuer
    die gesamte nachfolgende Kette.
    """
    op.execute(
        "ALTER TABLE agent_templates ADD COLUMN IF NOT EXISTS claude_md "
        "text NOT NULL DEFAULT ''"
    )


def downgrade() -> None:
    pass

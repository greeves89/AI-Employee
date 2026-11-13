"""Freigabe eines Agenten nur zum Nutzen (``agent_access.rolle``).

Neue Freigaben ueber „Freigeben fuer“ tragen ``nutzen``: chatten, sprechen,
Dateien schicken und abholen — nicht einrichten. Aeltere Eintraege bleiben NULL
und behalten ihre Wirkung.

Revision ID: n2o3p4q5r6s7
Revises: m1n2o3p4q5r6
"""

from alembic import op

revision = "n2o3p4q5r6s7"
down_revision = "m1n2o3p4q5r6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "DO $$ BEGIN "
        "IF to_regclass('agent_access') IS NOT NULL THEN "
        "ALTER TABLE agent_access ADD COLUMN IF NOT EXISTS rolle varchar(16); "
        "END IF; END $$"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE IF EXISTS agent_access DROP COLUMN IF EXISTS rolle")

"""bridge_lernen: Schalter, Nutzungsprotokoll und Regelvorschläge (#1023)

Revision ID: d7e3b91a4c52
Revises: i7j8k9l0m1n2
Create Date: 2026-11-04
"""
from alembic import op

revision = "d7e3b91a4c52"
down_revision = "i7j8k9l0m1n2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS bridge_lernen (
            user_id VARCHAR(64) PRIMARY KEY,
            aktiv BOOLEAN NOT NULL DEFAULT FALSE,
            agent_id VARCHAR(64),
            ausgewertet_at TIMESTAMP WITH TIME ZONE,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS bridge_nutzung (
            id SERIAL PRIMARY KEY,
            user_id VARCHAR(64) NOT NULL,
            app VARCHAR(120) NOT NULL DEFAULT '',
            titel VARCHAR(300) NOT NULL DEFAULT '',
            dauer_sekunden INTEGER NOT NULL DEFAULT 0,
            ende_at TIMESTAMP WITH TIME ZONE NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_bridge_nutzung_user_id ON bridge_nutzung (user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_bridge_nutzung_ende_at ON bridge_nutzung (ende_at)")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS bridge_vorschlaege (
            id SERIAL PRIMARY KEY,
            user_id VARCHAR(64) NOT NULL,
            agent_id VARCHAR(64),
            name VARCHAR(120) NOT NULL,
            wenn_app VARCHAR(120) NOT NULL DEFAULT '',
            wenn_titel VARCHAR(200) NOT NULL DEFAULT '',
            angebot VARCHAR(300) NOT NULL,
            auftrag TEXT NOT NULL,
            begruendung VARCHAR(300) NOT NULL DEFAULT '',
            status VARCHAR(20) NOT NULL DEFAULT 'offen',
            regel_id INTEGER,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_bridge_vorschlaege_user_id ON bridge_vorschlaege (user_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS bridge_vorschlaege")
    op.execute("DROP TABLE IF EXISTS bridge_nutzung")
    op.execute("DROP TABLE IF EXISTS bridge_lernen")

"""bridge_regeln: Regeln des Bridge-Beobachters (#1019)

Revision ID: a9b8c7d6e5f4
Revises: f4a6c8e0b2d3
Create Date: 2026-10-26
"""
from alembic import op

revision = "a9b8c7d6e5f4"
down_revision = "f4a6c8e0b2d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS bridge_regeln (
            id SERIAL PRIMARY KEY,
            user_id VARCHAR(64) NOT NULL,
            agent_id VARCHAR(64),
            name VARCHAR(120) NOT NULL,
            wenn_app VARCHAR(120) NOT NULL DEFAULT '',
            wenn_titel VARCHAR(200) NOT NULL DEFAULT '',
            angebot VARCHAR(300) NOT NULL,
            auftrag TEXT NOT NULL,
            aktiv BOOLEAN NOT NULL DEFAULT TRUE,
            von VARCHAR(80) NOT NULL DEFAULT 'nutzer',
            treffer INTEGER NOT NULL DEFAULT 0,
            zuletzt_at TIMESTAMP WITH TIME ZONE,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_bridge_regeln_user_id ON bridge_regeln (user_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS bridge_regeln")

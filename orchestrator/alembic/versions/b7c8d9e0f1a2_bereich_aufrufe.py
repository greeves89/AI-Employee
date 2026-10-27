"""bereich_aufrufe: Nutzungsmessung je Bereich und Tag (#1047)

Revision ID: b7c8d9e0f1a2
Revises: a9b8c7d6e5f4
Create Date: 2026-10-27
"""
from alembic import op

revision = "b7c8d9e0f1a2"
down_revision = "a9b8c7d6e5f4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS bereich_aufrufe (
            id SERIAL PRIMARY KEY,
            user_id VARCHAR(64) NOT NULL,
            bereich VARCHAR(120) NOT NULL,
            tag DATE NOT NULL,
            anzahl INTEGER NOT NULL DEFAULT 0,
            CONSTRAINT uq_bereich_aufrufe_tag UNIQUE (user_id, bereich, tag)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_bereich_aufrufe_user_id ON bereich_aufrufe (user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_bereich_aufrufe_tag ON bereich_aufrufe (tag)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS bereich_aufrufe")

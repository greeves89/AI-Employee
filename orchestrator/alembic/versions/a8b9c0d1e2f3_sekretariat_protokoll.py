"""sekretariat: Protokoll („Was das Sekretariat heute getan hat“) und Bezug von Erinnerungen

Revision ID: a8b9c0d1e2f3
Revises: f2a3b4c5d6e7
Create Date: 2026-10-29
"""
from alembic import op

revision = "a8b9c0d1e2f3"
down_revision = "f2a3b4c5d6e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS sekretariat_protokoll (
            id SERIAL PRIMARY KEY,
            user_id VARCHAR(64) NOT NULL,
            agent_id VARCHAR(64),
            art VARCHAR(30) NOT NULL,
            text TEXT NOT NULL,
            bezug JSON,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_sekretariat_protokoll_user_id ON sekretariat_protokoll (user_id)")
    op.execute("ALTER TABLE IF EXISTS erinnerungen ADD COLUMN IF NOT EXISTS bezug JSON")


def downgrade() -> None:
    op.execute("ALTER TABLE IF EXISTS erinnerungen DROP COLUMN IF EXISTS bezug")
    op.execute("DROP TABLE IF EXISTS sekretariat_protokoll")

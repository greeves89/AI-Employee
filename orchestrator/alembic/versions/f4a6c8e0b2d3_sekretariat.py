"""sekretariat: Team-Art und Erinnerungen

Revision ID: f4a6c8e0b2d3
Revises: e2c4f6a8b0d1
Create Date: 2026-10-16
"""
from alembic import op

revision = "f4a6c8e0b2d3"
down_revision = "e2c4f6a8b0d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE teams ADD COLUMN IF NOT EXISTS art VARCHAR(20)")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS erinnerungen (
            id SERIAL PRIMARY KEY,
            user_id VARCHAR(64) NOT NULL,
            text TEXT NOT NULL,
            faellig_at TIMESTAMP WITH TIME ZONE NOT NULL,
            von VARCHAR(80) NOT NULL DEFAULT 'nutzer',
            gemeldet_at TIMESTAMP WITH TIME ZONE,
            erledigt_at TIMESTAMP WITH TIME ZONE,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_erinnerungen_user_id ON erinnerungen (user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_erinnerungen_faellig_at ON erinnerungen (faellig_at)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS erinnerungen")
    op.execute("ALTER TABLE teams DROP COLUMN IF EXISTS art")

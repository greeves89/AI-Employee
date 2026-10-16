"""projekt_arbeitsraum: Projektgespräch, Termine und Besprechungen am Projekt

Revision ID: b7e4c2a96d18
Revises: a3d5e8f17b20
Create Date: 2026-10-16
"""
from alembic import op

revision = "b7e4c2a96d18"
down_revision = "a3d5e8f17b20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS projekt_nachrichten (
            id SERIAL PRIMARY KEY,
            team_id VARCHAR(32) NOT NULL,
            autor_typ VARCHAR(10) NOT NULL,
            autor_id VARCHAR(100),
            autor_name VARCHAR(200) NOT NULL,
            text TEXT NOT NULL,
            art VARCHAR(20) NOT NULL DEFAULT 'nachricht',
            an JSON NOT NULL DEFAULT '[]',
            karte_id VARCHAR(32),
            task_id VARCHAR,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_projekt_nachrichten_team_id ON projekt_nachrichten (team_id)")
    op.execute("ALTER TABLE meeting_rooms ADD COLUMN IF NOT EXISTS team_id VARCHAR(32)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_meeting_rooms_team_id ON meeting_rooms (team_id)")
    op.execute("ALTER TABLE schedules ADD COLUMN IF NOT EXISTS team_id VARCHAR(32)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_schedules_team_id ON schedules (team_id)")


def downgrade() -> None:
    op.execute("ALTER TABLE schedules DROP COLUMN IF EXISTS team_id")
    op.execute("ALTER TABLE meeting_rooms DROP COLUMN IF EXISTS team_id")
    op.execute("DROP TABLE IF EXISTS projekt_nachrichten")

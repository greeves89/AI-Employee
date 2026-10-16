"""projekt_karten: Board-Karten, Arbeitstakt und Projektwissen

Revision ID: e6c1a7b93f42
Revises: d4a8f2c61e90
Create Date: 2026-10-16

Das Board eines Projekts besteht aus Karten (eigen, Issue, PR) statt aus
Auftraegen: Eingang -> Bereit -> In Arbeit -> Pruefung -> Erledigt. Ein Auftrag
(Task) ist der Lauf zu einer Karte. Dazu am Team der Arbeitstakt und der Slug
des Second Brain mit dem Projektwissen.
"""
from alembic import op

revision = "e6c1a7b93f42"
down_revision = "d4a8f2c61e90"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS projekt_karten (
            id VARCHAR(32) PRIMARY KEY,
            team_id VARCHAR(32) NOT NULL,
            quelle VARCHAR(10) NOT NULL DEFAULT 'eigen',
            extern_nr INTEGER,
            url VARCHAR(500),
            titel VARCHAR(300) NOT NULL,
            text TEXT NOT NULL DEFAULT '',
            labels JSON NOT NULL DEFAULT '[]',
            spalte VARCHAR(20) NOT NULL DEFAULT 'eingang',
            prioritaet INTEGER NOT NULL DEFAULT 2,
            reihenfolge DOUBLE PRECISION NOT NULL DEFAULT 0,
            task_id VARCHAR,
            agent_id VARCHAR(36),
            stufe VARCHAR(20),
            notiz VARCHAR(500),
            pr_nr INTEGER,
            pr_url VARCHAR(500),
            pr_zustand VARCHAR(20),
            erstellt_von VARCHAR(100),
            erledigt_at TIMESTAMP WITH TIME ZONE,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            CONSTRAINT uq_projekt_karte_extern UNIQUE (team_id, quelle, extern_nr)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_projekt_karten_team_id ON projekt_karten (team_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_projekt_karten_spalte ON projekt_karten (spalte)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_projekt_karten_task_id ON projekt_karten (task_id)")
    op.execute("ALTER TABLE teams ADD COLUMN IF NOT EXISTS takt_minuten INTEGER")
    op.execute("ALTER TABLE teams ADD COLUMN IF NOT EXISTS takt_schedule_id VARCHAR")
    op.execute("ALTER TABLE teams ADD COLUMN IF NOT EXISTS wissen_slug VARCHAR(64)")


def downgrade() -> None:
    op.execute("ALTER TABLE teams DROP COLUMN IF EXISTS wissen_slug")
    op.execute("ALTER TABLE teams DROP COLUMN IF EXISTS takt_schedule_id")
    op.execute("ALTER TABLE teams DROP COLUMN IF EXISTS takt_minuten")
    op.execute("DROP TABLE IF EXISTS projekt_karten")

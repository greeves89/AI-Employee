"""projekte: Auftraege gehoeren zu einem Projekt (Team), Abnahme, Projektfarbe

Revision ID: d4a8f2c61e90
Revises: c7e2a9d4f1b3
Create Date: 2026-10-16

``tasks.team_id`` ordnet einen Auftrag einem Projekt zu (Board, spaeter Kosten).
Bisher stand das nur in ``metadata.team_id`` des Auftrags, den der Lead bekam —
seine Unterauftraege trugen es nicht. Die Rueckfuellung uebernimmt den Wert aus
den Metadaten und reicht ihn rekursiv an alle Unterauftraege weiter (UNION statt
UNION ALL: ein Kreis in parent_task_id kann die Rekursion nicht endlos machen).
Danach bleibt die Zuordnung nur, wo der ausfuehrende Agent zum Projekt gehoert.
``tasks.abgenommen_at``: wann der Mensch das Ergebnis abgenommen hat.
``teams.farbe``: Kennfarbe des Projekts.
"""
from alembic import op

revision = "d4a8f2c61e90"
down_revision = "c7e2a9d4f1b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS team_id VARCHAR(32)")
    op.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS abgenommen_at TIMESTAMP WITH TIME ZONE")
    op.execute("CREATE INDEX IF NOT EXISTS ix_tasks_team_id ON tasks (team_id)")
    op.execute("ALTER TABLE teams ADD COLUMN IF NOT EXISTS farbe VARCHAR(20)")
    op.execute(
        "UPDATE tasks SET team_id = LEFT(metadata->>'team_id', 32) "
        "WHERE team_id IS NULL AND metadata->>'team_id' IS NOT NULL"
    )
    op.execute(
        """
        WITH RECURSIVE baum AS (
            SELECT id, team_id FROM tasks WHERE team_id IS NOT NULL
            UNION
            SELECT t.id, b.team_id FROM tasks t JOIN baum b ON t.parent_task_id = b.id
            WHERE t.team_id IS NULL
        )
        UPDATE tasks SET team_id = baum.team_id
        FROM baum WHERE tasks.id = baum.id AND tasks.team_id IS NULL
        """
    )
    # Wie zur Laufzeit (models/task._projekt_erben): zum Projekt gehoert nur, was
    # ein Agent des Projekts ausfuehrt.
    op.execute(
        """
        UPDATE tasks SET team_id = NULL
        WHERE team_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM teams
            WHERE teams.id = tasks.team_id
              AND (teams.lead_agent_id = tasks.agent_id
                   OR teams.member_agent_ids::jsonb @> jsonb_build_array(tasks.agent_id))
        )
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE teams DROP COLUMN IF EXISTS farbe")
    op.execute("DROP INDEX IF EXISTS ix_tasks_team_id")
    op.execute("ALTER TABLE tasks DROP COLUMN IF EXISTS abgenommen_at")
    op.execute("ALTER TABLE tasks DROP COLUMN IF EXISTS team_id")

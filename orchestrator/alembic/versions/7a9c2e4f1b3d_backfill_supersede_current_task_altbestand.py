"""Backfill: current_task-Altbestand je (agent_id, room) abloesen (issue #716)

Revision ID: 7a9c2e4f1b3d
Revises: 3c58f5d6c519
Create Date: 2026-09-17

`current_task` wurde erst mit dem vorigen Release als Einfach-Schluessel im
KEY_SCHEMA verzeichnet, sodass ``save_memory_core`` ihn ab jetzt beim
Schreiben abloest. Das wirkt aber nur VORWAERTS und nur eine Zeile je Schreib-
vorgang (``.limit(1)``, die juengste aktive). Der Altbestand bleibt davon
unberuehrt: bei einem Agenten im Betrieb standen am 07.09.2026 1.327 Zeilen,
519 davon aktiv verteilt ueber 205 Raeume, statt einer je Raum.

Dieses Backfill behebt das einmalig fuer den gesamten bestehenden Datenbestand:
je (agent_id, room) bleibt die juengste aktive ``current_task``-Zeile aktiv,
alle anderen werden auf sie abgeloest (``superseded_by``/``superseded_at``
gesetzt). Nicht destruktiv — keine Zeile wird geloescht, jede bleibt ueber
``memory_search`` weiterhin vollstaendig lesbar.
"""
from alembic import op
import sqlalchemy as sa

revision = "7a9c2e4f1b3d"
down_revision = "3c58f5d6c519"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT
                    id,
                    ROW_NUMBER() OVER (
                        PARTITION BY agent_id, room
                        ORDER BY created_at DESC, id DESC
                    ) AS rn,
                    FIRST_VALUE(id) OVER (
                        PARTITION BY agent_id, room
                        ORDER BY created_at DESC, id DESC
                    ) AS newest_id
                FROM agent_memories
                WHERE key = 'current_task' AND superseded_by IS NULL
            )
            UPDATE agent_memories
            SET superseded_by = ranked.newest_id,
                superseded_at = now(),
                updated_at = now()
            FROM ranked
            WHERE agent_memories.id = ranked.id
              AND ranked.rn > 1
            """
        )
    )


def downgrade() -> None:
    # Absichtlich irreversibel: welche Zeilen dieses Backfill abgeloest hat,
    # laesst sich von echten, spaeter regulaer abgeloesten Zeilen nicht mehr
    # unterscheiden — ein Rueckbau wuerde beides durcheinanderwerfen.
    pass

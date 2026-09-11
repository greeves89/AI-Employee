"""task_ratings: fulfilled/gap verdict from the self-reflection judge

Revision ID: h6i7j8k9l0m1
Revises: a1g2e3n4t5f6
Create Date: 2026-09-11

Der Reflection-Richter (`_llm_reflect_on_task`) bewertete bisher nur die
Metriken eines Laufs (Dauer/Turns/Kosten), nie ob das Ergebnis den Auftrag
tatsaechlich erfuellt. Er bekommt jetzt zusaetzlich Auftrag + Ergebnistext und
liefert ein separates `fulfilled`/`gap`-Urteil, das VOR den Benachrichtigungen
an Menschen und delegierende Agenten verfuegbar ist (siehe task_router.py
handle_task_completion). `fulfilled` ist nullable: `NULL` heisst "kein Urteil
moeglich" (z. B. CLI-Fallback) und muss nie wie "nicht erfuellt" behandelt
werden.

Idempotent: ADD COLUMN IF NOT EXISTS, sicher bei bereits vorhandenen Spalten.
"""
from alembic import op

revision = "h6i7j8k9l0m1"
down_revision = "a1g2e3n4t5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE task_ratings ADD COLUMN IF NOT EXISTS fulfilled boolean"
    )
    op.execute(
        "ALTER TABLE task_ratings ADD COLUMN IF NOT EXISTS gap text"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE task_ratings DROP COLUMN IF EXISTS gap")
    op.execute("ALTER TABLE task_ratings DROP COLUMN IF EXISTS fulfilled")

"""Titel eines Wissenseintrags ist je Mandant eindeutig, nicht global

Revision ID: c31f7a9d40be
Revises: 5c72fd28db6a
Create Date: 2026-08-24

Zu Issue #655. `knowledge_entries.title` trug eine GLOBALE Unique-Bedingung aus
der Zeit vor der Mandantentrennung; die Spalte `user_id` kam erst spaeter dazu.
Der Schreibpfad sucht seitdem nach `(title, user_id)` — findet also nichts, wenn
der Titel einem anderen Besitzer gehoert — und laeuft dann in ein INSERT, das an
der globalen Bedingung scheitert. Der naechtliche Reflexionslauf brach daran
komplett ab, weil die `UniqueViolation` die Session mitnahm.

Zwei TEILWEISE Indizes statt eines auf `(title, user_id)`: NULLs gelten in einem
zusammengesetzten Unique-Index als verschieden, ein einfacher Index wuerde also
beliebig viele globale Eintraege gleichen Titels zulassen. So bleibt die bisherige
Semantik fuer globale Eintraege exakt erhalten.

Der alte Index wird durch einen gleichnamigen NICHT-eindeutigen ersetzt, damit die
Titelsuche ihren Index behaelt. Da die strengere Bedingung bisher galt, kann es
keine Bestandsdaten geben, an denen der Wechsel scheitert.
"""
from alembic import op

revision = "c31f7a9d40be"
down_revision = "5c72fd28db6a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_knowledge_entries_title")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_knowledge_entries_title "
        "ON knowledge_entries (title)"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_knowledge_entries_title_global "
        "ON knowledge_entries (title) WHERE user_id IS NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_knowledge_entries_title_per_user "
        "ON knowledge_entries (title, user_id) WHERE user_id IS NOT NULL"
    )


def downgrade() -> None:
    # Zurueck auf die globale Bedingung. Das kann scheitern, wenn inzwischen zwei
    # Mandanten denselben Titel fuehren — genau der Zustand, den diese Migration
    # erlaubt. Dann muessen die Duplikate vorher von Hand aufgeloest werden.
    op.execute("DROP INDEX IF EXISTS uq_knowledge_entries_title_per_user")
    op.execute("DROP INDEX IF EXISTS uq_knowledge_entries_title_global")
    op.execute("DROP INDEX IF EXISTS ix_knowledge_entries_title")
    op.execute(
        "CREATE UNIQUE INDEX ix_knowledge_entries_title "
        "ON knowledge_entries (title)"
    )

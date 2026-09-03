"""Titel im Wissensspeicher je Besitzer eindeutig statt global

Revision ID: b7c1e93a5f20
Revises: 5c72fd28db6a
Create Date: 2026-08-24

`knowledge_entries.title` trug seit Anlage der Tabelle eine GLOBALE
Eindeutigkeitsbedingung. Die Spalte `user_id` kam erst spaeter dazu; der
Schreibpfad sucht seitdem immer nur im Vault des Besitzers. Dadurch entstand ein
Zustand, den der Code fuer unmoeglich haelt: die Suche findet nichts (der Titel
gehoert einem anderen Mandanten), der folgende INSERT scheitert an der globalen
Bedingung — und die UniqueViolation reisst den ganzen Reflection-Lauf mit.

`user_id` ist nullable (globale Eintraege). Ein einfacher Index auf
(title, user_id) wuerde mehrfache globale Eintraege gleichen Titels erlauben,
weil NULLs in Postgres als verschieden gelten. Zwei partielle Indizes erhalten
die bisherige Semantik daher exakt.

Der bisherige Index wird nicht ersatzlos entfernt, sondern als nicht-eindeutiger
Index gleichen Namens neu angelegt — Suchen nach `title` bleiben indiziert und
das Modell (`index=True`) passt weiterhin zum Schema.

Da heute die strengere globale Bedingung gilt, kann kein Bestandsdatensatz die
neue, lockerere Bedingung verletzen.
"""
from alembic import op

revision = "b7c1e93a5f20"
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
    # Schlaegt fehl, sobald zwei Mandanten denselben Titel benutzen — genau das,
    # was die neue Bedingung erlaubt. Dann muessen die Duplikate erst weg.
    op.execute("DROP INDEX IF EXISTS uq_knowledge_entries_title_per_user")
    op.execute("DROP INDEX IF EXISTS uq_knowledge_entries_title_global")
    op.execute("DROP INDEX IF EXISTS ix_knowledge_entries_title")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_knowledge_entries_title "
        "ON knowledge_entries (title)"
    )

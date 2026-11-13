"""Besitzer je Gespraech (``chat_sessions.user_id``) und Empfaenger einer Meldung
(``notifications.empfaenger_id``) — Chats je Nutzer an freigegebenen Agenten
(app/core/chat_besitz).

Beide Tabellen legt nicht eine Migration an (``chat_sessions``: der Start in
main.py, ``notifications``: create_all). Deshalb hier idempotent: ohne Tabelle
nichts tun, Spalte nur anlegen, wenn sie fehlt.

Revision ID: m1n2o3p4q5r6
Revises: l0m1n2o3p4q5
"""

from alembic import op

revision = "m1n2o3p4q5r6"
down_revision = "l0m1n2o3p4q5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "DO $$ BEGIN "
        "IF to_regclass('chat_sessions') IS NOT NULL THEN "
        "ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS user_id varchar; "
        "CREATE INDEX IF NOT EXISTS ix_chat_sessions_user_id ON chat_sessions (user_id); "
        "END IF; "
        "IF to_regclass('notifications') IS NOT NULL THEN "
        "ALTER TABLE notifications ADD COLUMN IF NOT EXISTS empfaenger_id varchar; "
        "CREATE INDEX IF NOT EXISTS ix_notifications_empfaenger_id ON notifications (empfaenger_id); "
        "END IF; END $$"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_notifications_empfaenger_id")
    op.execute("ALTER TABLE IF EXISTS notifications DROP COLUMN IF EXISTS empfaenger_id")
    op.execute("DROP INDEX IF EXISTS ix_chat_sessions_user_id")
    op.execute("ALTER TABLE IF EXISTS chat_sessions DROP COLUMN IF EXISTS user_id")

"""user tutorial seen

Revision ID: b66153cb8a45
Revises: 9e5f3a2c6b74
Create Date: 2026-10-02

Willkommensfenster mit den Klick-Tutorials: wann der Nutzer es geschlossen hat.
Leer = beim naechsten Start zeigen — auch fuer Nutzer, die beim Einspielen schon
ein Konto haben: Sie sehen die Tutorials einmal nach dem Update (so gewollt).
``IF NOT EXISTS``, damit die Migration auf einer Datenbank, die die Spalte schon
hat, nicht scheitert.
"""
from alembic import op

revision = "b66153cb8a45"
down_revision = "9e5f3a2c6b74"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS tutorial_seen_at TIMESTAMP WITH TIME ZONE")


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS tutorial_seen_at")

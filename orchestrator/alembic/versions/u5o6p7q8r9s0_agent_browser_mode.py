"""Add browser_mode column to agents table for Playwright browser control

Revision ID: ub5oc6pd7qe8
Revises: a2b3c4d5e6f7
Create Date: 2026-04-19

"""
from alembic import op

revision = "ub5oc6pd7qe8"
down_revision = "a2b3c4d5e6f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Spalte anlegen, falls sie fehlt — ohne vorher zu LESEN.

    Frueher stand hier „erst per SELECT nachsehen, dann anlegen". Im
    Offline-Modus (``alembic upgrade head --sql``) gibt es aber keine
    Verbindung, an der ein Ergebnis abzuholen waere: ``fetchone()`` scheiterte,
    und weil Alembic die Kette der Reihe nach abarbeitet, blockierte diese eine
    Revision die Vorschau fuer ALLE nachfolgenden (#689). Praktische Folge: jede
    neue Migration wurde ungesehen aufgespielt, weil „geht in diesem Baum nicht"
    zur Gewohnheit wurde.

    ``ADD COLUMN IF NOT EXISTS`` erledigt dieselbe Pruefung in der Datenbank.
    Das ist PostgreSQL-Syntax — und PostgreSQL ist hier gesetzt.
    """
    op.execute(
        "ALTER TABLE agents ADD COLUMN IF NOT EXISTS browser_mode "
        "boolean NOT NULL DEFAULT false"
    )


def downgrade() -> None:
    # Symmetrisch idempotent: ein Rueckbau, der an einer fehlenden Spalte
    # scheitert, blockiert die Kette genauso.
    op.execute("ALTER TABLE agents DROP COLUMN IF EXISTS browser_mode")

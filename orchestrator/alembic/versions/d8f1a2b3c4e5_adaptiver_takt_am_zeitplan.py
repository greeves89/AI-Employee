"""adaptiver Takt am Zeitplan: adaptive, Grundtakt, Stufen, Leerlauf-Zaehler

Revision ID: d8f1a2b3c4e5
Revises: b7e4c2a96d18
Create Date: 2026-10-15

Issue #941: Ein wiederkehrender Beobachtungs-Job soll bei Leerlauf von selbst
langsamer werden (30 Min -> 2 Std -> 4 Std -> 8 Std) und bei neuer Arbeit sofort
wieder im Grundtakt laufen. Bisher ging das nur, indem der Agent den Zeitplan
LOESCHTE und neu anlegte — dabei gingen Kennung, Laufhistorie und Erfolgszaehler
verloren.

``adaptive`` steht bewusst auf FALSE: Jeder bestehende Zeitplan behaelt seinen
festen Takt, bis ihn jemand ausdruecklich umstellt. ``basis_intervall_s`` und
``stufen`` bleiben leer — die Rechnung faellt dann auf das aktuelle Intervall
bzw. die Standardleiter zurueck (siehe ``app.core.adaptiver_takt``).

``IF NOT EXISTS``, damit die Migration auf einer Datenbank, die die Spalten schon
aus den Modellen hat (create_all-Pfad), nicht scheitert.
"""
from alembic import op

revision = "d8f1a2b3c4e5"
down_revision = "b7e4c2a96d18"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE schedules ADD COLUMN IF NOT EXISTS adaptive BOOLEAN NOT NULL DEFAULT FALSE")
    op.execute("ALTER TABLE schedules ADD COLUMN IF NOT EXISTS basis_intervall_s INTEGER")
    op.execute("ALTER TABLE schedules ADD COLUMN IF NOT EXISTS stufen JSON")
    op.execute("ALTER TABLE schedules ADD COLUMN IF NOT EXISTS leer_schwelle INTEGER NOT NULL DEFAULT 3")
    op.execute("ALTER TABLE schedules ADD COLUMN IF NOT EXISTS leerlaeufe INTEGER NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE schedules ADD COLUMN IF NOT EXISTS stufe INTEGER NOT NULL DEFAULT 0")


def downgrade() -> None:
    op.execute("ALTER TABLE schedules DROP COLUMN IF EXISTS stufe")
    op.execute("ALTER TABLE schedules DROP COLUMN IF EXISTS leerlaeufe")
    op.execute("ALTER TABLE schedules DROP COLUMN IF EXISTS leer_schwelle")
    op.execute("ALTER TABLE schedules DROP COLUMN IF EXISTS stufen")
    op.execute("ALTER TABLE schedules DROP COLUMN IF EXISTS basis_intervall_s")
    op.execute("ALTER TABLE schedules DROP COLUMN IF EXISTS adaptive")

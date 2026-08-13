"""Feedback-Widget: Element-Pin + MD-Store-Referenzen am Feedback-Eintrag

Revision ID: w8x9y0z1a2b3
Revises: b532p2h1o2s3
Create Date: 2026-08-13

Das In-App-Feedback-Widget pinnt Feedback an ein konkretes UI-Element und legt
pro Feedback eine Markdown-Datei (+ optional PNG-Screenshot) in FEEDBACK_DIR ab.
Der DB-Eintrag bekommt die Metadaten dazu, damit die bestehende Admin-Liste
weiterfunktioniert. Rein additiv, alle Spalten nullable — Alt-Einträge aus dem
Modal bleiben unverändert lesbar. IF NOT EXISTS, weil frisch provisionierte
Datenbanken die Spalten schon über create_all bekommen.
"""
from alembic import op

revision = "w8x9y0z1a2b3"
down_revision = "b532p2h1o2s3"
branch_labels = None
depends_on = None

_COLUMNS = ("page", "element_label", "selector", "sentiment", "md_file", "screenshot_file")


def upgrade() -> None:
    for col in _COLUMNS:
        op.execute(f"ALTER TABLE feedback ADD COLUMN IF NOT EXISTS {col} VARCHAR")


def downgrade() -> None:
    for col in _COLUMNS:
        op.execute(f"ALTER TABLE feedback DROP COLUMN IF EXISTS {col}")

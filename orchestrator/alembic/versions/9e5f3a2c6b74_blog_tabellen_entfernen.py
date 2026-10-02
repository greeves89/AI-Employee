"""blog tabellen entfernen

Revision ID: 9e5f3a2c6b74
Revises: 8d4e2f1b5a63
Create Date: 2026-10-02

Der Blog der Landingpage ist kein Teil des Produkts mehr (v1.354.0); seine
Tabellen gehen mit. ``IF EXISTS``: auf einer frischen Datenbank gibt es sie
nicht, und die Migration bleibt offline-faehig.
"""
from alembic import op

revision = "9e5f3a2c6b74"
down_revision = "8d4e2f1b5a63"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS blog_images")
    op.execute("DROP TABLE IF EXISTS blog_posts")


def downgrade() -> None:
    # Die Tabellen gehoerten zu einer entfernten Funktion; ein Zurueck legt sie
    # nicht wieder an. Wer auf v1.353.0 zurueckgeht, bekommt sie von den beiden
    # Revisionen davor (CREATE TABLE IF NOT EXISTS).
    pass

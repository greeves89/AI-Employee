"""app favorites

Revision ID: a1f2c3d4e5f6
Revises: 4e7b040149c2
Create Date: 2026-09-20

Anpinnen von Apps in der Uebersicht. Eigene Tabelle statt Liste am Nutzer:
Setzen und Entfernen sind dann ein Einfuegen bzw. ein Loeschen statt eines
Lesen-Aendern-Schreibens mit Wettlauf.
"""
from alembic import op
import sqlalchemy as sa

revision = "a1f2c3d4e5f6"
down_revision = "4e7b040149c2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_favorites",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("project", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "project", name="uq_app_favorite_user_project"),
    )
    op.create_index("ix_app_favorites_user_id", "app_favorites", ["user_id"])
    op.create_index("ix_app_favorites_project", "app_favorites", ["project"])


def downgrade() -> None:
    op.drop_index("ix_app_favorites_project", table_name="app_favorites")
    op.drop_index("ix_app_favorites_user_id", table_name="app_favorites")
    op.drop_table("app_favorites")

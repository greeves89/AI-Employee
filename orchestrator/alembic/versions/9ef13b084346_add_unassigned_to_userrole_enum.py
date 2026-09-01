"""add unassigned to userrole enum

Revision ID: 9ef13b084346
Revises: 5c72fd28db6a
Create Date: 2026-08-28

Nachzieh-Migration: ``UserRole.UNASSIGNED`` kam als Python-Enum-Member dazu
(Rolle fuer frisch per SSO angemeldete Nutzer ohne Zuteilung), aber der
Postgres-Enum-Typ ``userrole`` wurde nie um den Wert erweitert — live auf
zwei Deployments bestaetigt: ``ALTER TYPE ... ADD VALUE`` fehlte, jeder SSO-
Login eines neuen Nutzers endete mit
``InvalidTextRepresentationError: invalid input value for enum userrole:
"UNASSIGNED"`` (500 auf dem Microsoft-Callback). Gleiches Muster wie
484693cdc27d (oauth_provider/GITHUB): SQLAlchemy Enum() nutzt ohne
``values_callable`` die Python-NAMEN (Grossschreibung) als DB-Werte.
"""
from alembic import op

revision = "9ef13b084346"
down_revision = "5c72fd28db6a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'UNASSIGNED'")


def downgrade() -> None:
    # PostgreSQL does not support removing values from enums easily.
    # This would require recreating the type + column. Skipping for safety.
    pass

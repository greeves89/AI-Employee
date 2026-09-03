"""Drop the unused task_logs table (dead code — never written or read).

Revision ID: d1r2o3p4l5o6
Revises: c1f2c3o4s5t6
Create Date: 2026-05-17
"""

from alembic import op
from sqlalchemy import inspect

revision = "d1r2o3p4l5o6"
down_revision = "c1f2c3o4s5t6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Offline-tauglich seit #689: die frueher hier stehende Pruefung per
    # `inspect(bind)` braucht eine echte Verbindung und scheitert bei
    # `alembic upgrade --sql` — was die Vorschau fuer die GANZE
    # nachfolgende Kette blockierte. Die Datenbank kann dieselbe Pruefung
    # selbst (PostgreSQL ist hier gesetzt).
    op.execute("DROP TABLE IF EXISTS task_logs")


def downgrade() -> None:
    # No-op: the table was dead code; recreating it serves no purpose.
    pass

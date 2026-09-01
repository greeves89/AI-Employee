"""Session-Invalidierung fuer Admin-Passwort-Reset (users.token_version)

Revision ID: f4a1c9d2e6b7
Revises: 9ef13b084346
Create Date: 2026-08-28

JWTs (Access 30min, Refresh 7 Tage) tragen keinerlei Widerruf-Mechanismus. Ein
Admin-Passwort-Reset aenderte bisher nur den Hash, liess eine bereits laufende
(z.B. kompromittierte) Sitzung aber bis zu 7 Tage lang unberuehrt weiterlaufen.
``token_version`` wird bei jedem Reset erhoeht und in jedem frisch ausgestellten
Token als ``tv``-Claim eingebettet; ``get_current_user``/``get_current_user_ws``/
``/auth/refresh`` lehnen Tokens mit veralteter ``tv`` ab. Additiv, IF NOT EXISTS
analog zu 5c72fd28db6a.
"""
from alembic import op

revision = "f4a1c9d2e6b7"
down_revision = "9ef13b084346"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
        "token_version INTEGER NOT NULL DEFAULT 0"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS token_version")

"""Add per-MCP OAuth callback base URL

Revision ID: g5h6i7j8k9l0
Revises: f4a1c9d2e6b7
Create Date: 2026-08-31

Die Spalte stammt aus dem Fix zu #665 und wird seitdem vom Startpfad
(``orchestrator/app/main.py``, ``ADD COLUMN IF NOT EXISTS``) auf jeder
Anlage angelegt. Auf jeder Installation, die vor dieser Revision einmal
gestartet ist, existiert sie also schon -- ``op.add_column`` brach dort mit
``DuplicateColumnError`` ab und blockierte die GESAMTE nachfolgende Kette
(Issue #825: sieben Migrationen haengend, Orchestrator kam nicht hoch).

Deshalb ``ADD COLUMN IF NOT EXISTS`` per ``op.execute`` -- wie seit #689 in
diesem Baum Konvention, offline-faehig (``alembic upgrade head --sql``).
"""
from typing import Sequence, Union

from alembic import op


revision: str = "g5h6i7j8k9l0"
down_revision: Union[str, None] = "f4a1c9d2e6b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE mcp_servers ADD COLUMN IF NOT EXISTS oauth_callback_base_url TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE mcp_servers DROP COLUMN IF EXISTS oauth_callback_base_url")

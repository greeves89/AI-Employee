"""Eigenes Microsoft-Konto je Agent: Spalte oauth_integrations.agent_id.

Plan docs/plaene/agenten-identitaet-m365.md, Phase 1. Die Teil-Eindeutigkeiten für Konten von
Menschen und globale Tokens bekommen den Zusatz ``agent_id IS NULL`` — aber nur, wo es sie
gibt: Auf manchen Anlagen fehlen sie (gesehen auf der Testanlage). Dort jetzt neu anzulegen,
könnte an alten Doppelzeilen scheitern und das Update anhalten.

Revision ID: j8k9l0m1n2o3
Revises: i7j8k9l0m1n2
"""

import sqlalchemy as sa
from alembic import op

revision = "j8k9l0m1n2o3"
down_revision = "i7j8k9l0m1n2"
branch_labels = None
depends_on = None


def _neu_wenn_da(name: str, definition: str) -> None:
    """Index nur ersetzen, wenn es ihn gibt — geprüft IN der Datenbank (DO-Block), damit die
    Revision auch offline (``alembic upgrade --sql``) läuft und nichts liest."""
    op.execute(
        f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = '{name}') THEN "
        f"DROP INDEX {name}; CREATE UNIQUE INDEX {name} ON oauth_integrations {definition}; "
        f"END IF; END $$;")


def upgrade() -> None:
    op.add_column("oauth_integrations", sa.Column(
        "agent_id", sa.String(), sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=True))
    op.create_index("ix_oauth_integrations_agent_id", "oauth_integrations", ["agent_id"])
    _neu_wenn_da("uq_oauth_global", "(provider) WHERE user_id IS NULL AND agent_id IS NULL")
    _neu_wenn_da("uq_oauth_user", "(provider, user_id) WHERE user_id IS NOT NULL AND agent_id IS NULL")
    op.execute("CREATE UNIQUE INDEX uq_oauth_agent ON oauth_integrations (provider, agent_id) "
               "WHERE agent_id IS NOT NULL")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_oauth_agent")
    op.execute("DELETE FROM oauth_integrations WHERE agent_id IS NOT NULL")
    _neu_wenn_da("uq_oauth_global", "(provider) WHERE user_id IS NULL")
    _neu_wenn_da("uq_oauth_user", "(provider, user_id) WHERE user_id IS NOT NULL")
    op.drop_index("ix_oauth_integrations_agent_id", table_name="oauth_integrations")
    op.drop_column("oauth_integrations", "agent_id")

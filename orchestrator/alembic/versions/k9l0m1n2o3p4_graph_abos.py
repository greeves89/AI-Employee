"""Graph-Abonnements der Agenten (Wecken bei neuer Mail).

Plan docs/plaene/agenten-identitaet-m365.md, Phase 2.

Revision ID: k9l0m1n2o3p4
Revises: j8k9l0m1n2o3
"""

import sqlalchemy as sa
from alembic import op

revision = "k9l0m1n2o3p4"
down_revision = "j8k9l0m1n2o3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "graph_abos",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("agent_id", sa.String(), sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("art", sa.String(16), nullable=False),
        sa.Column("ressource", sa.String(200), nullable=False),
        sa.Column("subscription_id", sa.String(64), nullable=True, unique=True),
        sa.Column("ablauf", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delta_link", sa.Text(), nullable=True),
        sa.Column("letzte_meldung", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fehler", sa.String(300), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("agent_id", "art", name="uq_graph_abo_agent_art"),
    )
    op.create_index("ix_graph_abos_agent_id", "graph_abos", ["agent_id"])


def downgrade() -> None:
    op.drop_index("ix_graph_abos_agent_id", table_name="graph_abos")
    op.drop_table("graph_abos")

"""Backfill agents.model 'gpt-5-codex' -> 'gpt-5.5' (issue #293)

Revision ID: 515d03f814a0
Revises: c3d4e5f6a7b8
Create Date: 2026-07-06

PR #292 removed gpt-5-codex from the codex_cli catalog and made the runtime
coerce guard route it to gpt-5.5 (the ChatGPT-account codex harness rejects
gpt-5-codex at runtime: "not supported when using Codex with a ChatGPT
account"). The dispatch-time guard already handles existing agents, but the
stored agents.model values stay stale. This one-off data migration rewrites
them so the DB matches the catalog. It is a no-op on deployments that never
stored gpt-5-codex.
"""
from alembic import op
import sqlalchemy as sa

revision = "515d03f814a0"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text("UPDATE agents SET model = 'gpt-5.5' WHERE model = 'gpt-5-codex'")
    )


def downgrade() -> None:
    # Intentionally irreversible: gpt-5-codex is unsupported on the codex
    # harness, so restoring it would only re-introduce the broken state.
    pass

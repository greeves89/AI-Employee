"""add knowledge_feeds table for MCP registry + AI news + competitor tracker + CLAUDE.md updater

Revision ID: 51b28b9b96b3
Revises: b6f263e51643
Create Date: 2026-04-15 18:50:24.356280

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '51b28b9b96b3'
down_revision: Union[str, None] = 'b6f263e51643'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Two tables:
    #  (1) knowledge_feeds: registered feed definitions (a feed is "a thing
    #      we pull periodically" — MCP registry, AI news, competitor posts,
    #      latest best practices for CLAUDE.md).
    #  (2) knowledge_feed_items: individual items harvested from each feed
    #      (e.g. a single new MCP server, a single news article, a single
    #       competitor release note).
    op.create_table(
        "knowledge_feeds",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("feed_type", sa.String(length=50), nullable=False),  # mcp_registry|ai_news|competitor|best_practices
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("config", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("interval_minutes", sa.Integer(), nullable=False, server_default="1440"),  # daily
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status", sa.String(length=50), nullable=True),  # ok|error|running
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("items_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_knowledge_feeds_feed_type", "knowledge_feeds", ["feed_type"])
    op.create_index("ix_knowledge_feeds_enabled", "knowledge_feeds", ["enabled"])

    op.create_table(
        "knowledge_feed_items",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("feed_id", sa.Integer(), sa.ForeignKey("knowledge_feeds.id", ondelete="CASCADE"), nullable=False),
        sa.Column("external_id", sa.String(length=500), nullable=False),  # url or upstream id — for dedup
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("harvested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("seen", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.UniqueConstraint("feed_id", "external_id", name="uq_feed_item_external_id"),
    )
    op.create_index("ix_knowledge_feed_items_feed_id", "knowledge_feed_items", ["feed_id"])
    op.create_index("ix_knowledge_feed_items_seen", "knowledge_feed_items", ["seen"])
    op.create_index(
        "ix_knowledge_feed_items_published_at",
        "knowledge_feed_items",
        ["published_at"],
        postgresql_where=sa.text("published_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_knowledge_feed_items_published_at", table_name="knowledge_feed_items")
    op.drop_index("ix_knowledge_feed_items_seen", table_name="knowledge_feed_items")
    op.drop_index("ix_knowledge_feed_items_feed_id", table_name="knowledge_feed_items")
    op.drop_table("knowledge_feed_items")
    op.drop_index("ix_knowledge_feeds_enabled", table_name="knowledge_feeds")
    op.drop_index("ix_knowledge_feeds_feed_type", table_name="knowledge_feeds")
    op.drop_table("knowledge_feeds")

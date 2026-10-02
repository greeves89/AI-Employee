"""blog images

Revision ID: 8d4e2f1b5a63
Revises: 7c3d1e9a4f52
Create Date: 2026-10-02

Bilder fuer den Blog der Landingpage (Titelbilder, Grafiken, Bildschirmfotos)
und das Titelbild je Beitrag. ``IF NOT EXISTS`` wie in diesem Baum ueblich:
offline-faehig und auf einer frischen Datenbank ohne Wirkung.
"""
from alembic import op

revision = "8d4e2f1b5a63"
down_revision = "7c3d1e9a4f52"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS blog_images (
            id SERIAL PRIMARY KEY,
            name VARCHAR(120) NOT NULL,
            content_type VARCHAR(32) NOT NULL,
            alt VARCHAR(300) NOT NULL DEFAULT '',
            width INTEGER NOT NULL DEFAULT 0,
            height INTEGER NOT NULL DEFAULT 0,
            size INTEGER NOT NULL DEFAULT 0,
            etag VARCHAR(40) NOT NULL DEFAULT '',
            data BYTEA NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_blog_images_name ON blog_images (name)")
    op.execute("ALTER TABLE blog_posts ADD COLUMN IF NOT EXISTS cover VARCHAR(120) NOT NULL DEFAULT ''")


def downgrade() -> None:
    op.execute("ALTER TABLE blog_posts DROP COLUMN IF EXISTS cover")
    op.execute("DROP INDEX IF EXISTS ix_blog_images_name")
    op.execute("DROP TABLE IF EXISTS blog_images")

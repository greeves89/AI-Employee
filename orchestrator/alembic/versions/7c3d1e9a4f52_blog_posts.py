"""blog posts

Revision ID: 7c3d1e9a4f52
Revises: 6b2c0bb45b3a
Create Date: 2026-10-02

Blogbeitraege der Landingpage: Markdown plus Titel, Beschreibung, Hauptbegriff
und Fragen mit Antworten. Die Tabelle bleibt leer, solange der Betreiber den
Blog nicht einschaltet.

``CREATE TABLE IF NOT EXISTS`` per ``op.execute`` — wie seit #689 in diesem Baum
Konvention: Eine frische Datenbank legt die Tabelle beim Start schon aus den
Modellen an, und die Migration bleibt offline-faehig
(``alembic upgrade heads --sql``), weil sie nichts aus der Datenbank liest.
"""
from alembic import op

revision = "7c3d1e9a4f52"
down_revision = "6b2c0bb45b3a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS blog_posts (
            id SERIAL PRIMARY KEY,
            slug VARCHAR(80) NOT NULL,
            title VARCHAR(200) NOT NULL,
            description VARCHAR(320) NOT NULL DEFAULT '',
            keyword VARCHAR(120) NOT NULL DEFAULT '',
            body_md TEXT NOT NULL DEFAULT '',
            tags JSON NOT NULL DEFAULT '[]',
            faq JSON NOT NULL DEFAULT '[]',
            author VARCHAR(120) NOT NULL DEFAULT '',
            words INTEGER NOT NULL DEFAULT 0,
            preview_key VARCHAR(64) NOT NULL DEFAULT '',
            status VARCHAR(16) NOT NULL DEFAULT 'draft',
            published_at TIMESTAMP WITH TIME ZONE,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_blog_posts_slug ON blog_posts (slug)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_blog_posts_status ON blog_posts (status)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_blog_posts_status")
    op.execute("DROP INDEX IF EXISTS ix_blog_posts_slug")
    op.execute("DROP TABLE IF EXISTS blog_posts")

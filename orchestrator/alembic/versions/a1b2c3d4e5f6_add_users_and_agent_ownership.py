"""add users table and agent ownership

Revision ID: a1b2c3d4e5f6
Revises: 363944841959
Create Date: 2026-02-16 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '363944841959'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create enum type if it doesn't exist (may already be created by create_all)
    op.execute("DO $$ BEGIN CREATE TYPE userrole AS ENUM ('admin', 'member'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;")

    # Create users table if not exists
    op.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id VARCHAR NOT NULL PRIMARY KEY,
        email VARCHAR NOT NULL,
        name VARCHAR NOT NULL,
        password_hash VARCHAR NOT NULL,
        role userrole NOT NULL,
        is_active BOOLEAN NOT NULL DEFAULT true,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now()
    );
    """)
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_email ON users (email);")

    # Add user_id FK to agents table (idempotent)
    op.execute("""
    DO $$ BEGIN
        ALTER TABLE agents ADD COLUMN user_id VARCHAR;
    EXCEPTION WHEN duplicate_column THEN NULL;
    END $$;
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_agents_user_id ON agents (user_id);")
    op.execute("""
    DO $$ BEGIN
        ALTER TABLE agents ADD CONSTRAINT fk_agents_user_id
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL;
    EXCEPTION WHEN duplicate_object THEN NULL;
    END $$;
    """)


def downgrade() -> None:
    op.drop_constraint('fk_agents_user_id', 'agents', type_='foreignkey')
    op.drop_index('ix_agents_user_id', table_name='agents')
    op.drop_column('agents', 'user_id')
    op.drop_index('ix_users_email', table_name='users')
    op.drop_table('users')
    # Drop the enum type
    sa.Enum(name='userrole').drop(op.get_bind(), checkfirst=True)

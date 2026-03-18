#!/usr/bin/env python3
"""Initialize database from scratch using current SQLAlchemy models.

Use this instead of Alembic migrations when setting up a fresh instance.
Creates all tables directly from the ORM models and stamps Alembic to HEAD
so future incremental migrations work correctly.

Usage:
    # Inside orchestrator container:
    python scripts/init_db.py

    # From host:
    docker compose exec orchestrator python scripts/init_db.py

    # Or via docker compose run:
    docker compose run --rm orchestrator python scripts/init_db.py
"""

import asyncio
import sys
import os

# Add parent dir to path so we can import app modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def init_db() -> None:
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy import text

    from app.config import settings
    from app.models import Base  # noqa: F401 — imports all models so metadata is complete

    engine = create_async_engine(settings.database_url)

    print(f"[init_db] Connecting to {settings.database_url.split('@')[-1]} ...")

    async with engine.begin() as conn:
        # Check if tables already exist
        result = await conn.execute(
            text(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
            )
        )
        table_count = result.scalar()

        if table_count and table_count > 0:
            print(f"[init_db] Database already has {table_count} tables.")
            answer = input("[init_db] Drop all and recreate? (yes/no): ").strip().lower()
            if answer != "yes":
                print("[init_db] Aborted.")
                await engine.dispose()
                return
            print("[init_db] Dropping all tables ...")
            await conn.run_sync(Base.metadata.drop_all)

        # Create all tables from current models
        print("[init_db] Creating tables from SQLAlchemy models ...")
        await conn.run_sync(Base.metadata.create_all)

        # Count created tables
        result = await conn.execute(
            text(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
            )
        )
        new_count = result.scalar()
        print(f"[init_db] Created {new_count} tables.")

    await engine.dispose()

    # Stamp Alembic to HEAD so future migrations work
    import subprocess

    print("[init_db] Stamping Alembic revision to HEAD ...")
    result = subprocess.run(
        ["alembic", "stamp", "head"],
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"[init_db] WARNING: Alembic stamp failed: {result.stderr}")
    else:
        print("[init_db] Alembic stamped to HEAD — future migrations will work incrementally.")

    print("[init_db] Done! Database is ready.")


if __name__ == "__main__":
    asyncio.run(init_db())

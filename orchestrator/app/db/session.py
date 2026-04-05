from collections.abc import AsyncGenerator

from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_size=10,
    max_overflow=20,
    pool_recycle=300,
    pool_pre_ping=True,
)
async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield an AsyncSession for FastAPI endpoints.

    Row-Level Security: the session variable `app.current_user_id` defaults
    to the bypass state. `require_auth` calls `set_rls_user()` after it
    resolves the user, so authenticated endpoints run isolated per user.
    Background services (scheduler, backfill, etc.) use the session without
    calling set_rls_user → RLS bypass via app.bypass_rls = 'yes'.
    """
    async with async_session_factory() as session:
        # Default stance: background/system work bypasses RLS.
        # Authenticated requests override this with set_rls_user().
        try:
            await session.execute(sa_text("SET LOCAL app.bypass_rls = 'yes'"))
            await session.execute(sa_text("SET LOCAL app.current_user_id = ''"))
        except Exception:
            # If pgvector migration hasn't run yet or RLS is not enabled, skip silently.
            pass
        yield session


async def set_rls_user(session: AsyncSession, user_id: str | None) -> None:
    """Restrict the session to a specific user — enforced by Postgres RLS.

    Call this inside `require_auth` after the user is identified. Once set,
    queries on user-scoped tables will only return rows belonging to this
    user (or rows with user_id IS NULL, e.g. legacy/global entries).

    Passing user_id=None or an empty string falls back to BYPASS mode
    (used by admin/system operations that legitimately need cross-user access).
    """
    try:
        if not user_id:
            await session.execute(sa_text("SET LOCAL app.bypass_rls = 'yes'"))
            await session.execute(sa_text("SET LOCAL app.current_user_id = ''"))
        else:
            await session.execute(sa_text("SET LOCAL app.bypass_rls = 'no'"))
            # Safe string interpolation: user_id comes from a verified JWT,
            # but we still quote it properly via parameterized SET.
            # PostgreSQL's SET LOCAL does not accept bind params → we hand-escape.
            safe_uid = str(user_id).replace("'", "''")
            await session.execute(sa_text(f"SET LOCAL app.current_user_id = '{safe_uid}'"))
    except Exception:
        pass

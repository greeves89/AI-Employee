import asyncio
import logging
import random
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager

from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

logger = logging.getLogger(__name__)

# Explicit connect-level timeout (seconds) for asyncpg's establishment of a NEW
# TCP/auth connection. This is deliberately tunable instead of relying on the
# asyncpg default, and is distinct from pool_timeout (checkout wait). See #356.
_CONNECT_TIMEOUT = 10

engine = create_async_engine(
    settings.database_url,
    echo=False,
    # Auto-scaling pool: small warm pool + unlimited overflow.
    # Connections are created on demand and returned when done.
    # PostgreSQL's max_connections (set in docker-compose) is the real cap.
    pool_size=5,        # Keep 5 warm connections (background tasks)
    max_overflow=-1,    # Unlimited: scale to whatever is needed, PG is the limit
    pool_recycle=300,   # Recycle connections every 5 min (prevents stale)
    pool_pre_ping=True, # Verify connection is alive before using
    pool_timeout=10,    # Fail fast if PG itself is overloaded (seconds)
    connect_args={"timeout": _CONNECT_TIMEOUT},  # bound the NEW-connection handshake (#356)
)
async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@asynccontextmanager
async def resilient_session(
    retries: int = 3,
    base_delay: float = 0.5,
    session_factory: Callable[[], AsyncSession] | None = None,
) -> AsyncGenerator[AsyncSession, None]:
    """Yield an AsyncSession whose *connection establishment* is retried with
    exponential backoff + jitter, so a brief DB blip (connect timeout / refused)
    doesn't kill an entire background sweep tick (see #356).

    Only the connect/checkout is retried: the session is forced live up-front via
    a ``SELECT 1`` pre-ping, so a connect-level ``TimeoutError`` surfaces here —
    where it can be retried — instead of deep inside the sweep body. Once the
    connection is live it is handed to the caller unchanged; exceptions raised
    *inside* the ``async with resilient_session()`` body are NOT retried and
    propagate normally.
    """
    factory = session_factory or async_session_factory
    attempt = 0
    while True:
        session = factory()
        try:
            await session.execute(sa_text("SELECT 1"))
        except Exception as e:
            await session.close()
            attempt += 1
            if attempt > retries:
                raise
            delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, base_delay)
            logger.warning(
                "[resilient_session] connect attempt %s/%s failed (%s: %s); retrying in %.2fs",
                attempt, retries, type(e).__name__, e, delay,
            )
            await asyncio.sleep(delay)
            continue
        try:
            yield session
        finally:
            await session.close()
        return


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

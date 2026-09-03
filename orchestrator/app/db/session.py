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
    # BEGRENZTER Pool — bewusst NICHT mehr unbegrenzt.
    #
    # Vorher stand hier max_overflow=-1 ("unbegrenzt, PG ist die Grenze"). Auf
    # dem Raspberry Pi war genau das die Ursache naechtlicher DB-Timeouts
    # (2026-08-19, rein 01-05 Uhr, ~360/h): der naechtliche Reflection/Dreaming-
    # Job faechert DB-Arbeit parallel auf, und ein unbegrenzter Pool oeffnet
    # dann schlagartig sehr viele NEUE Verbindungen gleichzeitig. Postgres auf
    # dem Pi kann so viele Neu-Verbindungen (TCP-Accept + Backend-Fork + SSL)
    # nicht schnell genug annehmen -> der Verbindungsaufbau laeuft in einen
    # TimeoutError, und alles im Zeitfenster (auch /kiosk/overview) gibt 500.
    #
    # Mit begrenztem Overflow WARTET ein Burst stattdessen kurz in der
    # Pool-Queue (billig) statt Postgres mit hunderten gleichzeitigen
    # Verbindungsaufbauten zu ueberrennen. Die Tages-Spitze lag bei ~11
    # Verbindungen — 30 sind reichlich Kopffreiheit; PG erlaubt 250.
    pool_size=10,       # warme Grundmenge
    max_overflow=20,    # + bis zu 20 unter Last -> hoechstens 30 gleichzeitig
    pool_recycle=900,   # 15 min: seltener neu aufbauen = weniger Handshake-Last
    pool_pre_ping=True, # tote Verbindung vor Nutzung erkennen
    pool_timeout=20,    # so lange auf einen freien Platz warten, bevor es fehlschlaegt
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

    A true drop-in for ``async with factory() as db``: it uses the session's
    async-context protocol, so ``session_factory`` is the usual
    ``async_session_factory`` (or any callable returning an async-context session).

    Only the connect/checkout is retried: the session is forced live up-front via
    a ``SELECT 1`` pre-ping, so a connect-level ``TimeoutError`` surfaces before
    the ``yield`` — where it can be retried — instead of deep inside the sweep
    body. Once the connection is live it is handed to the caller unchanged;
    exceptions raised *inside* the ``async with resilient_session()`` body are
    NOT retried and propagate normally.
    """
    factory = session_factory or async_session_factory
    attempt = 0
    while True:
        cm = factory()
        session = await cm.__aenter__()
        try:
            await session.execute(sa_text("SELECT 1"))
        except Exception as e:
            await cm.__aexit__(type(e), e, e.__traceback__)
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
        except BaseException as e:
            await cm.__aexit__(type(e), e, e.__traceback__)
            raise
        else:
            await cm.__aexit__(None, None, None)
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

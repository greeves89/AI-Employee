"""Issue #787 Punkt 1: der neue gebuendelte Endpunkt GET/PUT
/agents/{id}/access-policy.

Buendelt Autonomie-Matrix + abgeleitete Sudo-Pakete + Computer-Use-Default +
anwendbare Command Policies in einem Aufruf, gegen ``agent.access_policy``
statt der alten ``agent.config``-Schluessel. ``_check_owner`` (Ownership-
Pruefung) wird gepatcht -- diese Logik ist bereits an anderer Stelle getestet
und fuer diese Tests nicht das Interessante; hier geht es um die Buendelung.
"""
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import StaticPool

from app.models.base import Base
from app.models.agent import Agent
from app.models.command_policy import CommandPolicy
from app.api.agents import get_access_policy, update_access_policy


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(
                c,
                tables=[
                    Base.metadata.tables["agents"],
                    Base.metadata.tables["command_policies"],
                ],
            )
        )
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as session:
        yield session
    await engine.dispose()


_NO_OWNER_CHECK = patch("app.api.agents._check_owner", new=AsyncMock(return_value=None))
_FAKE_USER = type("U", (), {"id": "u1"})()


@pytest.mark.asyncio
async def test_get_bundles_matrix_permissions_and_policies(db):
    matrix = {"file_read": "allow", "shell_exec": "allow", "system_config": "allow"}
    db.add(Agent(
        id="a1", name="A", autonomy_level="custom",
        access_policy={"autonomy_matrix": matrix},
    ))
    db.add(CommandPolicy(name="no-rm", pattern=r"rm -rf", effect="blocked", scope="global"))
    await db.commit()

    with _NO_OWNER_CHECK:
        result = await get_access_policy("a1", user=_FAKE_USER, db=db)

    assert result["autonomy_level"] == "custom"
    assert result["matrix"]["shell_exec"] == "allow"
    # system_config:allow derives sudo — same rule as before this migration.
    assert sorted(result["permissions"]) == ["package-install", "system-config"]
    assert result["permissions_mode"] == "auto"
    assert len(result["command_policies"]) == 1
    assert result["command_policies"][0]["name"] == "no-rm"
    # No override set → platform default, not an empty list.
    assert result["computer_use_default_capabilities"]


@pytest.mark.asyncio
async def test_get_reflects_a_computer_use_override(db):
    db.add(Agent(
        id="a2", name="B", autonomy_level="l1",
        access_policy={"computer_use_default_capabilities": ["screenshots"]},
    ))
    await db.commit()

    with _NO_OWNER_CHECK:
        result = await get_access_policy("a2", user=_FAKE_USER, db=db)

    assert result["computer_use_default_capabilities"] == ["screenshots"]


@pytest.mark.asyncio
async def test_get_unknown_agent_404s(db):
    with _NO_OWNER_CHECK, pytest.raises(Exception) as exc_info:
        await get_access_policy("missing", user=_FAKE_USER, db=db)
    assert getattr(exc_info.value, "status_code", None) == 404


@pytest.mark.asyncio
async def test_put_matrix_only_leaves_permissions_mode_untouched(db):
    db.add(Agent(
        id="a3", name="C", autonomy_level="l3",
        access_policy={"permissions_mode": "manual", "permissions": ["package-install"]},
    ))
    await db.commit()

    from app.api.agents import AccessPolicyUpdate
    manager = AsyncMock()
    manager._apply_permissions = AsyncMock()
    # "purchases: deny" matches no L1-L4 preset (none of them use deny at all)
    # -> forces the level label to "custom", proving the matrix write is what
    # decides it, independently of permissions_mode.
    with _NO_OWNER_CHECK, patch("app.api.agents.asyncio.to_thread", new=AsyncMock(return_value=None)):
        result = await update_access_policy(
            "a3", AccessPolicyUpdate(matrix={"purchases": "deny"}),
            user=_FAKE_USER, db=db, manager=manager,
        )

    assert result["permissions_mode"] == "manual"
    assert result["autonomy_level"] == "custom"  # matrix no longer matches any preset


@pytest.mark.asyncio
async def test_put_permissions_manual_mode_roundtrips(db):
    db.add(Agent(id="a4", name="D", autonomy_level="l1"))
    await db.commit()

    from app.api.agents import AccessPolicyUpdate
    manager = AsyncMock()
    with _NO_OWNER_CHECK, patch("app.api.agents.asyncio.to_thread", new=AsyncMock(return_value=None)):
        result = await update_access_policy(
            "a4",
            AccessPolicyUpdate(permissions_mode="manual", permissions=["package-install"]),
            user=_FAKE_USER, db=db, manager=manager,
        )

    assert result["permissions_mode"] == "manual"
    assert result["permissions"] == ["package-install"]


@pytest.mark.asyncio
async def test_put_rejects_unknown_permission_package(db):
    db.add(Agent(id="a5", name="E", autonomy_level="l1"))
    await db.commit()

    from app.api.agents import AccessPolicyUpdate
    manager = AsyncMock()
    with _NO_OWNER_CHECK, pytest.raises(Exception) as exc_info:
        await update_access_policy(
            "a5",
            AccessPolicyUpdate(permissions_mode="manual", permissions=["not-a-real-package"]),
            user=_FAKE_USER, db=db, manager=manager,
        )
    assert getattr(exc_info.value, "status_code", None) == 400


@pytest.mark.asyncio
async def test_put_rejects_unknown_capability_group(db):
    db.add(Agent(id="a6", name="F", autonomy_level="l1"))
    await db.commit()

    from app.api.agents import AccessPolicyUpdate
    manager = AsyncMock()
    with _NO_OWNER_CHECK, pytest.raises(Exception) as exc_info:
        await update_access_policy(
            "a6",
            AccessPolicyUpdate(computer_use_default_capabilities=["not-a-real-capability"]),
            user=_FAKE_USER, db=db, manager=manager,
        )
    assert getattr(exc_info.value, "status_code", None) == 400


@pytest.mark.asyncio
async def test_put_computer_use_default_roundtrips(db):
    db.add(Agent(id="a7", name="G", autonomy_level="l1"))
    await db.commit()

    from app.api.agents import AccessPolicyUpdate
    manager = AsyncMock()
    with _NO_OWNER_CHECK, patch("app.api.agents.asyncio.to_thread", new=AsyncMock(return_value=None)):
        result = await update_access_policy(
            "a7", AccessPolicyUpdate(computer_use_default_capabilities=["screenshots"]),
            user=_FAKE_USER, db=db, manager=manager,
        )

    assert result["computer_use_default_capabilities"] == ["screenshots"]


@pytest.mark.asyncio
async def test_full_access_without_an_explicit_mode_still_pins_to_manual(db):
    """Security-Fund im Review: effective_permissions() hat einen Grossvater-
    Zweig fuer ALTE full-access-Grants (mode is None + gespeichertes
    full-access -> gilt trotzdem) -- der war fuer Alt-Agenten gedacht, nicht
    als heimlicher zweiter Schreibweg fuer NEUE full-access-Vergaben ueber
    diesen Endpunkt ohne je "manual" zu setzen. Ohne den Fix wuerde diese
    Anfrage full-access gewaehren, OHNE dass die Antwort das je als "manual"
    auswiese -- ein GET danach haette "auto" gelogen.
    """
    db.add(Agent(id="a8", name="H", autonomy_level="l1"))
    await db.commit()

    from app.api.agents import AccessPolicyUpdate
    manager = AsyncMock()
    with _NO_OWNER_CHECK, patch("app.api.agents.asyncio.to_thread", new=AsyncMock(return_value=None)):
        result = await update_access_policy(
            "a8", AccessPolicyUpdate(permissions=["full-access"]),
            user=_FAKE_USER, db=db, manager=manager,
        )

    assert result["permissions_mode"] == "manual"
    assert result["permissions"] == ["full-access"]

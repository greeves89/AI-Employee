"""Nur eine Token-Erneuerung je Integration gleichzeitig (#1013).

Microsoft tauscht den Refresh-Token bei jeder Erneuerung aus. Erneuerten zwei
Aufrufe gleichzeitig (Seite „Heute“, Agent über MCP, Sprachfront, Hintergrund-Job),
scheiterte der zweite mit dem schon verbrauchten Token — die Verbindung galt als
getrennt. Jetzt wartet der zweite auf den ersten und übernimmt dessen Ergebnis.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services import oauth_service as os_mod
from app.services.oauth_service import OAuthService


def _db(aktuell, dialekt="postgresql"):
    db = AsyncMock()
    db.bind = SimpleNamespace(dialect=SimpleNamespace(name=dialekt))
    db.scalar = AsyncMock(return_value=aktuell)
    db.execute = AsyncMock()
    return db


def _integration(**kw):
    werte = {"id": 42, "provider": SimpleNamespace(value="microsoft"), "user_id": "u1",
             "refresh_token_encrypted": "alt", "access_token_encrypted": "zugang-alt", "expires_at": None}
    werte.update(kw)
    return SimpleNamespace(**werte)


@pytest.mark.asyncio
async def test_nimmt_die_sperre_je_integration():
    gelesen = _integration()
    db = _db(_integration())
    with patch.object(OAuthService, "_erneuern", AsyncMock(return_value="neu")) as erneuern:
        assert await OAuthService(db, None)._refresh_token(gelesen) == "neu"
    sql, parameter = db.execute.await_args.args
    assert "pg_advisory_xact_lock" in str(sql)
    assert parameter == {"ns": os_mod._OAUTH_SPERRE, "oid": 42}
    erneuern.assert_awaited_once()


@pytest.mark.asyncio
async def test_hat_ein_anderer_schon_erneuert_wird_nicht_noch_einmal_erneuert():
    """Der Wartende sieht nach der Sperre einen NEUEN Refresh-Token — und nimmt ihn."""
    gelesen = _integration(refresh_token_encrypted="alt")
    inzwischen = _integration(refresh_token_encrypted="neu-vom-anderen", access_token_encrypted="zugang-neu")
    db = _db(inzwischen)
    with patch.object(OAuthService, "_erneuern", AsyncMock()) as erneuern:
        ergebnis = await OAuthService(db, None)._refresh_token(gelesen)
    erneuern.assert_not_awaited()
    assert ergebnis is inzwischen
    db.commit.assert_awaited_once()  # Sperre gelöst


@pytest.mark.asyncio
async def test_fehlschlag_loest_die_sperre():
    db = _db(_integration())
    with patch.object(OAuthService, "_erneuern", AsyncMock(side_effect=ValueError("Token refresh failed: 400"))), \
            pytest.raises(ValueError):
        await OAuthService(db, None)._refresh_token(_integration())
    db.commit.assert_awaited_once()   # Sperre gelöst — ohne rollback (Objekte verfallen sonst)
    db.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_getrennte_integration_wird_gemeldet():
    db = _db(None)
    with patch.object(OAuthService, "_erneuern", AsyncMock()) as erneuern, pytest.raises(ValueError):
        await OAuthService(db, None)._refresh_token(_integration())
    erneuern.assert_not_awaited()


@pytest.mark.asyncio
async def test_ohne_postgres_keine_sperre_aber_gleiche_logik():
    db = _db(_integration(), dialekt="sqlite")
    with patch.object(OAuthService, "_erneuern", AsyncMock(return_value="neu")):
        assert await OAuthService(db, None)._refresh_token(_integration()) == "neu"
    db.execute.assert_not_awaited()


def test_mcp_und_integrationen_nutzen_dieselbe_sperre():
    """Keine zweite Umsetzung daneben: beide gehen über core/db_sperre."""
    from pathlib import Path

    app = Path(__file__).resolve().parents[1] / "app"
    treffer = [p.name for p in app.rglob("*.py") if "SELECT pg_advisory_xact_lock" in p.read_text(encoding="utf-8")]
    assert treffer == ["db_sperre.py"]


@pytest.mark.asyncio
async def test_inzwischen_frischer_token_wird_nicht_noch_einmal_erneuert():
    """Anbieter ohne rotierenden Refresh-Token: Der Wartende sieht ein frisches Ablaufdatum."""
    from datetime import datetime, timedelta, timezone

    frisch = _integration(expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
    db = _db(frisch)
    with patch.object(OAuthService, "_erneuern", AsyncMock()) as erneuern:
        assert await OAuthService(db, None)._refresh_token(_integration()) is frisch
    erneuern.assert_not_awaited()


@pytest.mark.asyncio
async def test_erzwungen_wird_trotz_frischem_token_erneuert():
    from datetime import datetime, timedelta, timezone

    db = _db(_integration(expires_at=datetime.now(timezone.utc) + timedelta(hours=1)))
    with patch.object(OAuthService, "_erneuern", AsyncMock(return_value="neu")) as erneuern:
        assert await OAuthService(db, None)._refresh_token(_integration(), erzwingen=True) == "neu"
    erneuern.assert_awaited_once()


@pytest.mark.asyncio
async def test_wartezeit_ist_begrenzt():
    db = _db(_integration())
    with patch.object(OAuthService, "_erneuern", AsyncMock(return_value="neu")):
        await OAuthService(db, None)._refresh_token(_integration())
    befehle = [str(c.args[0]) for c in db.execute.await_args_list]
    assert any("lock_timeout = '30s'" in b for b in befehle)


@pytest.mark.asyncio
async def test_hintergrund_schleife_laeuft_nach_einem_fehler_weiter():
    eins, zwei = _integration(id=1), _integration(id=2)
    ergebnis = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [eins, zwei]))
    db = AsyncMock()
    db.execute = AsyncMock(return_value=ergebnis)
    aufrufe = []

    async def refresh(self, integration, erzwingen=False):
        aufrufe.append(integration.id)
        if integration.id == 1:
            raise ValueError("Token refresh failed: 400")

    with patch.object(OAuthService, "_refresh_token", refresh):
        await OAuthService(db, None).refresh_expiring_tokens()
    assert aufrufe == [1, 2]

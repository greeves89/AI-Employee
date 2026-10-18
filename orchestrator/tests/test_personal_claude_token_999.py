"""Das eigene Claude-Abo läuft nicht mehr nach Stunden ab (#999).

Beim Kunden antwortete ein Agent, der mit dem eigenen Claude-Abo seines Besitzers
lief, nach einigen Stunden nur noch mit „401 OAuth access token has expired".
Neu anmelden half jeweils nur bis zum nächsten Ablauf. Drei Ursachen, alle hier
festgenagelt:

1. Der eigene Zugang speicherte nur eine Abschrift des Access-Tokens, ohne
   Refresh-Token und ohne Ablaufzeit, und niemand erneuerte ihn.
2. Die Browser-Anmeldung lief über ``exchange_code`` und überschrieb damit die
   EINE globale Anthropic-Integration, also die Teamlizenz der ganzen Anlage.
3. Der laufende Agent bekam keine erneuerten Token, nur die Abschrift aus seiner
   Erstellung.
"""

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from app.config import settings
from app.core import agent_credentials as creds
from app.core import personal_claude_token as pct
from app.core.encryption import decrypt_token, encrypt_token
from app.models.agent import Agent, AgentState
from app.models.oauth_integration import OAuthIntegration
from app.models.user_ai_credential import UserAiCredential


# Ohne festen Schlüssel würde die Verschlüsselung versuchen, ihn unter /app
# anzulegen — das gibt es ausserhalb des Containers nicht.
if not settings.encryption_key:
    from cryptography.fernet import Fernet
    settings.encryption_key = Fernet.generate_key().decode()


@compiles(JSONB, "sqlite")
def _jsonb_as_json(type_, compiler, **kw):  # noqa: ANN001
    return "JSON"


NOW = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)


class _Calls:
    """Ein Erneuerer, der mitzählt und eine vorgegebene Antwort liefert."""

    def __init__(self, status=200, body=None, exc=None):
        self.status, self.body, self.exc = status, body, exc
        self.seen: list[str] = []

    async def __call__(self, refresh_token: str):
        self.seen.append(refresh_token)
        if self.exc:
            raise self.exc
        return self.status, self.body if self.body is not None else {}


class _Base(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            for model in (UserAiCredential, OAuthIntegration, Agent):
                await conn.run_sync(model.metadata.create_all, tables=[model.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _row(self, *, secret="tok-alt", refresh="ref-alt", expires_at=NOW + timedelta(minutes=5),
                   user="u1", status="ok"):
        async with self.Session() as db:
            db.add(UserAiCredential(
                user_id=user, harness="claude_code", secret_encrypted=encrypt_token(secret),
                refresh_token_encrypted=encrypt_token(refresh) if refresh else None,
                expires_at=expires_at, last_status=status,
            ))
            await db.commit()

    async def _load_into(self, db, user="u1"):
        return (await db.execute(select(UserAiCredential).where(
            UserAiCredential.user_id == user))).scalar_one()

    async def _load(self, user="u1"):
        async with self.Session() as db:
            return (await db.execute(select(UserAiCredential).where(
                UserAiCredential.user_id == user))).scalar_one()


class NeedsRefresh(unittest.TestCase):
    def test_grenzen(self):
        self.assertFalse(pct.needs_refresh(None, NOW))
        self.assertTrue(pct.needs_refresh(NOW + timedelta(minutes=15), NOW))
        self.assertFalse(pct.needs_refresh(NOW + timedelta(minutes=16), NOW))
        self.assertTrue(pct.needs_refresh(NOW - timedelta(hours=1), NOW))
        # SQLite liefert Zeitstempel ohne Zone zurück.
        self.assertTrue(pct.needs_refresh((NOW + timedelta(minutes=1)).replace(tzinfo=None), NOW))


class CurrentToken(_Base):
    async def test_ohne_zugang_none(self):
        async with self.Session() as db:
            self.assertIsNone(await pct.current_claude_token(db, "u1", now=NOW, refresh_call=_Calls()))
            self.assertIsNone(await pct.current_claude_token(db, None, now=NOW, refresh_call=_Calls()))

    async def test_bald_ablaufend_wird_erneuert_und_zurueckgeschrieben(self):
        await self._row()
        call = _Calls(body={"access_token": "tok-neu", "refresh_token": "ref-neu", "expires_in": 28800})
        async with self.Session() as db:
            tok = await pct.current_claude_token(db, "u1", now=NOW, refresh_call=call)
        self.assertEqual(tok, "tok-neu")
        self.assertEqual(call.seen, ["ref-alt"])
        row = await self._load()
        self.assertEqual(decrypt_token(row.secret_encrypted), "tok-neu")
        self.assertEqual(decrypt_token(row.refresh_token_encrypted), "ref-neu")
        self.assertEqual(row.expires_at.replace(tzinfo=timezone.utc), NOW + timedelta(hours=8))
        self.assertEqual(row.last_status, "ok")

    async def test_zweiter_abruf_erneuert_nicht_erneut(self):
        await self._row()
        call = _Calls(body={"access_token": "tok-neu", "refresh_token": "ref-neu", "expires_in": 28800})
        async with self.Session() as db:
            await pct.current_claude_token(db, "u1", now=NOW, refresh_call=call)
        async with self.Session() as db:
            tok = await pct.current_claude_token(db, "u1", now=NOW, refresh_call=call)
        self.assertEqual(tok, "tok-neu")
        self.assertEqual(len(call.seen), 1, "verbrauchter Refresh-Token darf nicht erneut gehen")

    async def test_gleichzeitiger_abruf_mit_veraltetem_stand_erneuert_nicht_doppelt(self):
        """Zwei Abrufe zugleich: der zweite hält noch den alten Stand im Speicher.

        Ohne erneutes Lesen unter der Zeilensperre schickte er den schon
        verbrauchten Refresh-Token los — Anthropic lehnt ab, und der eben frisch
        erneuerte Zugang stünde als ``auth_failed`` da.
        """
        await self._row()
        call = _Calls(body={"access_token": "tok-neu", "refresh_token": "ref-neu", "expires_in": 28800})
        async with self.Session() as langsam:
            alt = await self._load_into(langsam)    # veralteter Stand im Speicher (Referenz halten: die Identity-Map ist schwach)
            async with self.Session() as schnell:
                await pct.current_claude_token(schnell, "u1", now=NOW, refresh_call=call)
            self.assertEqual(alt.last_status, "ok")  # Vorbedingung: Stand ist geladen
            tok = await pct.current_claude_token(langsam, "u1", now=NOW, refresh_call=call)
        self.assertEqual(tok, "tok-neu")
        self.assertEqual(call.seen, ["ref-alt"])

    async def test_ohne_neuen_refresh_token_bleibt_der_alte(self):
        await self._row()
        call = _Calls(body={"access_token": "tok-neu", "expires_in": 3600})
        async with self.Session() as db:
            await pct.current_claude_token(db, "u1", now=NOW, refresh_call=call)
        row = await self._load()
        self.assertEqual(decrypt_token(row.refresh_token_encrypted), "ref-alt")
        self.assertEqual(decrypt_token(row.secret_encrypted), "tok-neu")

    async def test_noch_lange_gueltig_kein_aufruf(self):
        await self._row(expires_at=NOW + timedelta(hours=3))
        call = _Calls()
        async with self.Session() as db:
            self.assertEqual(await pct.current_claude_token(db, "u1", now=NOW, refresh_call=call), "tok-alt")
        self.assertEqual(call.seen, [])

    async def test_eingefuegtes_token_ohne_refresh_wird_nie_erneuert(self):
        await self._row(refresh=None, expires_at=None)
        call = _Calls()
        async with self.Session() as db:
            self.assertEqual(await pct.current_claude_token(db, "u1", now=NOW, refresh_call=call), "tok-alt")
        self.assertEqual(call.seen, [])

    async def test_toter_refresh_token_markiert_auth_failed(self):
        await self._row()
        async with self.Session() as db:
            tok = await pct.current_claude_token(db, "u1", now=NOW, refresh_call=_Calls(status=400))
        self.assertEqual(tok, "tok-alt")
        self.assertEqual((await self._load()).last_status, "auth_failed")

    async def test_voruebergehender_fehler_aendert_nichts(self):
        await self._row()
        for call in (_Calls(status=503), _Calls(exc=OSError("netz"))):
            async with self.Session() as db:
                self.assertEqual(await pct.current_claude_token(db, "u1", now=NOW, refresh_call=call),
                                 "tok-alt")
            row = await self._load()
            self.assertEqual(row.last_status, "ok")
            self.assertEqual(decrypt_token(row.refresh_token_encrypted), "ref-alt")

    async def test_kaputte_antwort_ueberschreibt_nichts(self):
        await self._row()
        for body in ({"refresh_token": "ref-neu"}, {"access_token": "x", "refresh_token": "ref-neu",
                                                     "expires_in": "kaputt"}):
            async with self.Session() as db:
                tok = await pct.current_claude_token(db, "u1", now=NOW, refresh_call=_Calls(body=body))
            self.assertEqual(tok, "tok-alt")
            row = await self._load()
            self.assertEqual(decrypt_token(row.secret_encrypted), "tok-alt")
            self.assertEqual(decrypt_token(row.refresh_token_encrypted), "ref-alt")


class ApplyTokenData(unittest.TestCase):
    def test_ohne_expires_in_eine_stunde_statt_nie(self):
        row = UserAiCredential(user_id="u1", harness="claude_code", secret_encrypted="x")
        pct.apply_token_data(row, {"access_token": "tok"}, NOW)
        self.assertEqual(row.expires_at, NOW + timedelta(hours=1))


class ResolveUsesRefresh(_Base):
    async def test_datenbankfehler_rollback_vor_dem_rueckfall(self):
        """Nach einem Fehler in der Erneuerung muss die Sitzung zurückgesetzt
        werden, bevor gelesen wird — sonst wirft resolve auf Postgres, und der
        Container liefe still auf dem globalen Token."""
        await self._row(expires_at=NOW + timedelta(hours=3))

        async def kaputt(*a, **kw):
            raise RuntimeError("db weg")

        async with self.Session() as db:
            rollbacks = []
            echt = db.rollback

            async def zaehlen():
                rollbacks.append(1)
                await echt()

            with patch.object(pct, "current_claude_token", kaputt), \
                    patch.object(db, "rollback", zaehlen):
                self.assertEqual(await creds._own_secret(db, "u1", "claude_code"), "tok-alt")
        self.assertEqual(rollbacks, [1])


    async def test_resolve_liefert_erneuertes_token_und_markierung(self):
        await self._row(expires_at=datetime.now(timezone.utc) + timedelta(minutes=1))
        call = _Calls(body={"access_token": "tok-neu", "refresh_token": "ref-neu", "expires_in": 28800})
        with patch.object(pct, "anthropic_refresh_call", call), \
                patch.object(creds, "personal_credentials_allowed", return_value=True):
            async with self.Session() as db:
                source, harness, secret = await creds.resolve(
                    db, owner_id="u1", mode="claude_code", model_provider="anthropic")
        self.assertEqual((source, harness, secret), (creds.SOURCE_PERSONAL, "claude_code", "tok-neu"))
        self.assertEqual(creds.personal_oauth_env(harness, source, secret),
                         {"CLAUDE_CREDENTIAL_SOURCE": "personal"})

    def test_markierung_nur_fuer_eigenes_oauth_token(self):
        self.assertEqual(creds.personal_oauth_env("claude_code", creds.SOURCE_TEAM, "tok"), {})
        self.assertEqual(creds.personal_oauth_env("claude_code", creds.SOURCE_PERSONAL, "sk-ant-api03-x"), {})
        self.assertEqual(creds.personal_oauth_env("codex", creds.SOURCE_PERSONAL, "{}"), {})
        self.assertEqual(creds.personal_oauth_env("claude_code", creds.SOURCE_PERSONAL, None), {})


class _FakeService:
    def __init__(self, token_data):
        self.token_data = token_data

    async def exchange_code_tokens(self, provider, code, state):
        return "u1", self.token_data

    async def exchange_code(self, *a, **kw):  # pragma: no cover — darf nie laufen
        raise AssertionError("exchange_code ueberschreibt die Teamlizenz (#999)")


class ExchangeLeavesTeamLicenseAlone(_Base):
    async def _exchange(self, token_data):
        from app.api import my_ai_credentials as api
        user = SimpleNamespace(id="u1", role="member")
        body = api.OAuthExchange(code="abc#xyz", state="s1")
        with patch.object(api, "_eigene_zugaenge_erlaubt", lambda u: None):
            async with self.Session() as db:
                return await api.exchange_anthropic_login(body=body, user=user, db=db,
                                                          service=_FakeService(token_data))

    async def test_speichert_refresh_und_ablauf_und_keine_integration(self):
        res = await self._exchange({"access_token": "tok", "refresh_token": "ref", "expires_in": 28800})
        self.assertEqual(res["status"], "connected")
        row = await self._load()
        self.assertEqual(decrypt_token(row.secret_encrypted), "tok")
        self.assertEqual(decrypt_token(row.refresh_token_encrypted), "ref")
        self.assertIsNotNone(row.expires_at)
        async with self.Session() as db:
            self.assertEqual((await db.execute(select(OAuthIntegration))).scalars().all(), [],
                             "die globale Anthropic-Integration (Teamlizenz) bleibt unberuehrt")

    async def test_fremder_state_400(self):
        from app.api import my_ai_credentials as api

        class _Fremd(_FakeService):
            async def exchange_code_tokens(self, provider, code, state):
                return "u9", self.token_data

        user = SimpleNamespace(id="u1", role="member")
        with patch.object(api, "_eigene_zugaenge_erlaubt", lambda u: None):
            async with self.Session() as db:
                with self.assertRaises(HTTPException) as cm:
                    await api.exchange_anthropic_login(
                        body=api.OAuthExchange(code="abc", state="s"), user=user, db=db,
                        service=_Fremd({"access_token": "tok", "refresh_token": "ref"}))
        self.assertEqual(cm.exception.status_code, 400)

    async def test_ohne_access_token_400(self):
        with self.assertRaises(HTTPException) as cm:
            await self._exchange({"refresh_token": "ref"})
        self.assertEqual(cm.exception.status_code, 400)

    async def test_eingefuegtes_token_loescht_alten_refresh(self):
        from app.api import my_ai_credentials as api
        await self._row()
        user = SimpleNamespace(id="u1", role="member")
        with patch.object(api, "_eigene_zugaenge_erlaubt", lambda u: None):
            async with self.Session() as db:
                await api.upsert_my_credential(
                    body=api.CredentialUpsert(harness="claude_code", secret="sk-ant-oat01-neu"),
                    user=user, db=db)
        row = await self._load()
        self.assertIsNone(row.refresh_token_encrypted)
        self.assertIsNone(row.expires_at)


class AgentEndpoint(_Base):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        async with self.Session() as db:
            db.add(Agent(id="a1", name="Eins", state=AgentState.RUNNING, user_id="u1", config={},
                         mode="claude_code"))
            db.add(Agent(id="a2", name="Zwei", state=AgentState.RUNNING, user_id="u2", config={},
                         mode="claude_code"))
            await db.commit()

    async def _call(self, agent_id, auth_id):
        from app.api import agents as api
        async with self.Session() as db:
            return await api.get_agent_claude_token(agent_id=agent_id, db=db,
                                                    agent_auth={"agent_id": auth_id})

    async def test_fremder_agent_403(self):
        with self.assertRaises(HTTPException) as cm:
            await self._call("a1", "a2")
        self.assertEqual(cm.exception.status_code, 403)

    async def test_eigenes_abo_liefert_token(self):
        await self._row(expires_at=datetime.now(timezone.utc) + timedelta(hours=3))
        with patch.object(creds, "personal_credentials_allowed", return_value=True):
            res = await self._call("a1", "a1")
        self.assertEqual(res, {"source": creds.SOURCE_PERSONAL, "access_token": "tok-alt"})

    async def test_ohne_eigenes_abo_kein_token(self):
        with patch.object(creds, "personal_credentials_allowed", return_value=True), \
                patch.object(creds, "team_license_allowed", return_value=True), \
                patch.object(creds, "team_secret", return_value="team-tok"):
            res = await self._call("a2", "a2")
        self.assertIsNone(res["access_token"])
        self.assertEqual(res["source"], creds.SOURCE_TEAM)


if __name__ == "__main__":
    unittest.main()

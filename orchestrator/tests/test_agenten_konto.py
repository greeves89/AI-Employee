"""Eigenes Microsoft-365-Konto eines Agenten (Plan docs/plaene/agenten-identitaet-m365.md, Phase 1).

Was hier festgehalten wird — jeweils als Verhalten gegen eine echte Datenbank (SQLite):

1. Trennung: Das Konto eines Agenten und das seines Besitzers liegen beide unter
   ``user_id = Besitzer``. Kein Weg für Menschen (Integrationsseite, SSO, Sekretariat)
   darf je das Konto des Agenten treffen, und umgekehrt.
2. Kein stiller Rückfall: Hat der Agent ein eigenes Konto und dessen Erneuerung scheitert,
   arbeitet er NICHT mit dem Konto des Besitzers weiter (er handelte sonst als dieser).
3. Verbinden nur durch Besitzer oder Admin, und nie mit dem eigenen Konto — geprüft VOR
   dem Speichern, damit eine abgelehnte Anmeldung kein bestehendes Konto überschreibt.
4. Alle Microsoft-Wege eines Agenten (Werkzeuge, Teams, Mail-Freigabe) holen ihr Token an
   EINER Stelle (``agenten_konto.token_fuer``).
"""

import base64
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("ENCRYPTION_KEY", "c2VjcmV0LWtleS1mb3ItdGVzdHMtb25seS0zMmJ5dGU=")

from sqlalchemy import select  # noqa: E402
from sqlalchemy.dialects.postgresql import JSONB  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402

from app.core import agenten_konto  # noqa: E402
from app.core.encryption import encrypt_token  # noqa: E402
from app.models.agent import Agent, AgentState  # noqa: E402
from app.models.audit_log import AuditLog  # noqa: E402
from app.models.oauth_integration import OAuthIntegration, OAuthProvider  # noqa: E402
from app.models.platform_settings import PlatformSettings  # noqa: E402
from app.models.agent_access import AgentAccess  # noqa: E402
from app.models.user import User, UserRole  # noqa: E402
from app.services.oauth_service import OAuthService  # noqa: E402

ORCH = Path(__file__).resolve().parents[1]


@compiles(JSONB, "sqlite")
def _jsonb_as_json(type_, compiler, **kw):
    return "JSON"


def _id_token(sub: str) -> str:
    teil = base64.urlsafe_b64encode(json.dumps({"sub": sub}).encode()).decode().rstrip("=")
    return f"kopf.{teil}.signatur"


class _Basis(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            for model in (User, Agent, OAuthIntegration, AuditLog, PlatformSettings, AgentAccess):
                await conn.run_sync(model.metadata.create_all, tables=[model.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.Session() as db:
            db.add_all([
                User(id="u1", email="besitzer@example.com", name="Besitzer", role=UserRole.MEMBER,
                     sso_provider="microsoft", sso_subject="sub-besitzer"),
                User(id="u2", email="fremd@example.com", name="Fremd", role=UserRole.MEMBER),
                User(id="adm", email="admin@example.com", name="Admin", role=UserRole.ADMIN),
                Agent(id="a1", name="Assistenz", state=AgentState.RUNNING, user_id="u1", config={}),
                # Menschliches Konto des Besitzers
                OAuthIntegration(provider=OAuthProvider.MICROSOFT, user_id="u1", agent_id=None,
                                 access_token_encrypted=encrypt_token("tok-mensch"),
                                 account_label="besitzer@example.com", scopes=""),
            ])
            await db.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _agentenkonto(self, db, token="tok-agent", refresh=None, ablauf=None):
        db.add(OAuthIntegration(provider=OAuthProvider.MICROSOFT, user_id="u1", agent_id="a1",
                                access_token_encrypted=encrypt_token(token),
                                refresh_token_encrypted=encrypt_token(refresh) if refresh else None,
                                expires_at=ablauf, account_label="assistenz@example.com", scopes=""))
        await db.commit()


class TrennungTests(_Basis):
    """Beide Zeilen tragen user_id=u1 — trotzdem trifft jeder Weg nur seine eigene."""

    async def test_mensch_bekommt_nie_das_token_des_agenten(self):
        async with self.Session() as db:
            await self._agentenkonto(db)
            svc = OAuthService(db, None)
            self.assertEqual(await svc.get_valid_token("microsoft", "u1"), "tok-mensch")
            self.assertEqual(await svc.get_valid_token("microsoft", "u1", agent_id="a1"), "tok-agent")

    async def test_ohne_menschliches_konto_kein_treffer_auf_das_des_agenten(self):
        async with self.Session() as db:
            await db.delete((await db.execute(select(OAuthIntegration))).scalars().first())
            await db.commit()
            await self._agentenkonto(db)
            with self.assertRaises(ValueError):
                await OAuthService(db, None).get_valid_token("microsoft", "u1")

    async def test_integrationsseite_zeigt_nur_das_menschliche_konto(self):
        async with self.Session() as db:
            await self._agentenkonto(db)
            liste = await OAuthService(db, None).list_integrations("u1")
        ms = next(i for i in liste if i["provider"] == "microsoft")
        self.assertEqual(ms["account_label"], "besitzer@example.com")

    async def test_trennen_des_menschen_laesst_das_agentenkonto_stehen(self):
        async with self.Session() as db:
            await self._agentenkonto(db)
            await OAuthService(db, None).disconnect("microsoft", "u1")
            reste = (await db.execute(select(OAuthIntegration.agent_id))).scalars().all()
        self.assertEqual(reste, ["a1"])

    async def test_trennen_des_agentenkontos_behaelt_die_mcp_freigaben_des_besitzers(self):
        async with self.Session() as db:
            await self._agentenkonto(db)
            with patch("app.core.mcp_oauth.forget_grants", AsyncMock()) as vergessen:
                await OAuthService(db, SimpleNamespace(client=AsyncMock())).disconnect(
                    "microsoft", "u1", agent_id="a1")
            reste = (await db.execute(select(OAuthIntegration.agent_id))).scalars().all()
        self.assertEqual(reste, [None])
        vergessen.assert_not_awaited()


class TokenFuerTests(_Basis):
    async def test_eigenes_konto_wird_genommen(self):
        async with self.Session() as db:
            await self._agentenkonto(db)
            agent = await db.get(Agent, "a1")
            self.assertEqual(await agenten_konto.token_fuer(db, agent), "tok-agent")

    async def test_ohne_eigenes_konto_das_des_besitzers(self):
        async with self.Session() as db:
            agent = await db.get(Agent, "a1")
            self.assertEqual(await agenten_konto.token_fuer(db, agent), "tok-mensch")

    async def test_scheitert_das_eigene_konto_kein_rueckfall_auf_den_besitzer(self):
        async with self.Session() as db:
            await self._agentenkonto(db)
            agent = await db.get(Agent, "a1")
            aufrufe = []

            async def gvt(self_, provider, user_id=None, agent_id=None):
                aufrufe.append(agent_id)
                if agent_id:
                    raise ValueError("Token refresh failed: 400")
                return "tok-mensch"

            with patch.object(OAuthService, "get_valid_token", gvt):
                self.assertIsNone(await agenten_konto.token_fuer(db, agent))
        self.assertEqual(aufrufe, ["a1"], "Nach dem Scheitern darf das Konto des Besitzers nicht gefragt werden.")

    async def test_ohne_agent_kein_token(self):
        async with self.Session() as db:
            self.assertIsNone(await agenten_konto.token_fuer(db, None))


class SpeichernTests(_Basis):
    def _svc(self, db, konto):
        svc = OAuthService(db, None)
        svc.kontoname = AsyncMock(return_value=konto)
        return svc

    async def _zeilen(self, db):
        return (await db.execute(select(OAuthIntegration.agent_id, OAuthIntegration.account_label)
                                 .where(OAuthIntegration.agent_id.is_not(None)))).all()

    async def test_konto_des_agenten_wird_gespeichert_und_protokolliert(self):
        async with self.Session() as db:
            zeile = await agenten_konto.speichern(self._svc(db, "assistenz@example.com"), "microsoft", "u1", "a1",
                                                  {"access_token": "neu", "refresh_token": "r"})
            self.assertEqual((zeile.agent_id, zeile.user_id), ("a1", "u1"))
            self.assertEqual(await self._zeilen(db), [("a1", "assistenz@example.com")])
            prot = (await db.execute(select(AuditLog))).scalars().all()
        self.assertTrue(any("verbunden" in (p.command or "") for p in prot))

    async def test_eigenes_konto_des_besitzers_wird_abgelehnt(self):
        async with self.Session() as db:
            for konto in ("besitzer@example.com", "BESITZER@example.com "):
                with self.assertRaises(ValueError) as e:
                    await agenten_konto.speichern(self._svc(db, konto), "microsoft", "u1", "a1", {"access_token": "x"})
                self.assertIn("eigenes Microsoft-Konto", str(e.exception))
            self.assertEqual(await self._zeilen(db), [])

    async def test_gleiche_anmeldung_ueber_sub_wird_abgelehnt(self):
        """Andere Adresse (Alias), aber dieselbe Person laut id_token."""
        async with self.Session() as db:
            with self.assertRaises(ValueError):
                await agenten_konto.speichern(self._svc(db, "alias@example.com"), "microsoft", "u1", "a1",
                                              {"access_token": "x", "id_token": _id_token("sub-besitzer")})

    async def test_nicht_feststellbares_konto_wird_abgelehnt(self):
        async with self.Session() as db:
            with self.assertRaises(ValueError) as e:
                await agenten_konto.speichern(self._svc(db, None), "microsoft", "u1", "a1", {"access_token": "x"})
            self.assertIn("nicht feststellen", str(e.exception))
            self.assertEqual(await self._zeilen(db), [])

    async def test_abgelehnte_anmeldung_ueberschreibt_kein_bestehendes_konto(self):
        async with self.Session() as db:
            await self._agentenkonto(db)
            with self.assertRaises(ValueError):
                await agenten_konto.speichern(self._svc(db, "besitzer@example.com"), "microsoft", "u1", "a1",
                                              {"access_token": "falsch"})
            agent = await db.get(Agent, "a1")
            self.assertEqual(await agenten_konto.token_fuer(db, agent), "tok-agent")

    async def test_fremder_nutzer_darf_nicht_verbinden(self):
        async with self.Session() as db:
            with self.assertRaises(ValueError):
                await agenten_konto.speichern(self._svc(db, "assistenz@example.com"), "microsoft", "u2", "a1",
                                              {"access_token": "x"})
            self.assertEqual(await self._zeilen(db), [])

    async def test_admin_darf_verbinden_konto_liegt_beim_besitzer(self):
        async with self.Session() as db:
            zeile = await agenten_konto.speichern(self._svc(db, "assistenz@example.com"), "microsoft", "adm", "a1",
                                                  {"access_token": "x"})
        self.assertEqual(zeile.user_id, "u1")

    async def test_nur_microsoft(self):
        async with self.Session() as db:
            with self.assertRaises(ValueError):
                await agenten_konto.speichern(self._svc(db, "x@example.com"), "google", "u1", "a1", {})


class RueckrufTests(_Basis):
    """exchange_code leitet ein Konto mit agent_id im Zustand an agenten_konto.speichern."""

    async def test_anmelde_adresse_fragt_nach_dem_konto_und_traegt_den_agenten(self):
        gespeichert = {}

        class _R:
            async def setex(self, key, ttl, wert):
                gespeichert[key] = wert

        async with self.Session() as db:
            svc = OAuthService(db, SimpleNamespace(client=_R()))
            with patch("app.services.oauth_service.get_provider_client_id", return_value="client"):
                url = await svc.generate_auth_url("microsoft", "u1", "/agents/a1?konnektoren=1", agent_id="a1")
        self.assertIn("prompt=select_account", url)
        zustand = json.loads(next(iter(gespeichert.values())))
        self.assertEqual((zustand["agent_id"], zustand["user_id"]), ("a1", "u1"))

    async def test_ohne_agent_kein_kontowahl_zwang(self):
        class _R:
            async def setex(self, *a):
                pass

        async with self.Session() as db:
            svc = OAuthService(db, SimpleNamespace(client=_R()))
            with patch("app.services.oauth_service.get_provider_client_id", return_value="client"):
                url = await svc.generate_auth_url("microsoft", "u1")
        self.assertNotIn("select_account", url)

    async def test_abgelehnter_rueckruf_fuehrt_zum_agenten_zurueck(self):
        from app.api.integrations import oauth_callback

        svc = SimpleNamespace(ruecksprung_fuer=AsyncMock(return_value="/agents/a1?konnektoren=1"),
                              exchange_code=AsyncMock(side_effect=ValueError("Das ist dein eigenes Microsoft-Konto.")))
        antwort = await oauth_callback("microsoft", code="c", state="s", error=None, service=svc)
        ziel = antwort.headers["location"]
        self.assertTrue(ziel.startswith("/agents/a1?konnektoren=1&"), ziel)
        self.assertIn("error=", ziel)
        self.assertIn("provider=microsoft", ziel)


class ZustandEinmalTests(_Basis):
    """Review M1: Der Zustand wird EINMAL gelesen; ein Agenten-Zustand landet nie beim Menschen."""

    class _Redis:
        def __init__(self, wert):
            self.wert, self.gelesen = wert, 0

        async def get(self, key):
            self.gelesen += 1
            return self.wert if key.startswith("oauth:state:") else None

        async def delete(self, key):
            if key.startswith("oauth:state:"):
                self.wert = None

    def _http(self):
        antwort = SimpleNamespace(status_code=200, json=lambda: {"access_token": "neu"}, text="")
        client = SimpleNamespace(post=AsyncMock(return_value=antwort))

        class _Ctx:
            async def __aenter__(self_):
                return client

            async def __aexit__(self_, *a):
                return False

        return patch("app.services.oauth_service.httpx.AsyncClient", lambda *a, **k: _Ctx())

    async def test_agentenkonto_geht_an_speichern_und_zustand_wird_einmal_gelesen(self):
        r = self._Redis(json.dumps({"provider": "microsoft", "user_id": "u1", "agent_id": "a1"}))
        async with self.Session() as db:
            svc = OAuthService(db, SimpleNamespace(client=r))
            with self._http(), patch("app.core.agenten_konto.speichern", AsyncMock(return_value="ok")) as sp, \
                    patch.object(OAuthService, "persist_tokens", AsyncMock()) as pt:
                await svc.exchange_code("microsoft", "code", "st")
        self.assertEqual(r.gelesen, 1)
        self.assertEqual(sp.await_args.args[2:4], ("u1", "a1"))
        pt.assert_not_awaited()

    async def test_unbrauchbare_agenten_kennung_wird_abgelehnt(self):
        for wert in (123, "", ["a1"]):
            r = self._Redis(json.dumps({"provider": "microsoft", "user_id": "u1", "agent_id": wert}))
            async with self.Session() as db:
                svc = OAuthService(db, SimpleNamespace(client=r))
                with self._http(), patch.object(OAuthService, "persist_tokens", AsyncMock()) as pt, \
                        self.assertRaises(ValueError):
                    await svc.exchange_code("microsoft", "code", "st")
                pt.assert_not_awaited()

    async def test_reiner_codetausch_lehnt_agentenzustand_ab(self):
        r = self._Redis(json.dumps({"provider": "microsoft", "user_id": "u1", "agent_id": "a1"}))
        async with self.Session() as db:
            with self._http(), self.assertRaises(ValueError):
                await OAuthService(db, SimpleNamespace(client=r)).exchange_code_tokens("microsoft", "code", "st")

    async def test_agent_ohne_besitzer_bekommt_kein_konto(self):
        async with self.Session() as db:
            (await db.get(Agent, "a1")).user_id = None
            await db.commit()
            svc = OAuthService(db, None)
            svc.kontoname = AsyncMock(return_value="assistenz@example.com")
            with self.assertRaises(ValueError):
                await agenten_konto.speichern(svc, "microsoft", "adm", "a1", {"access_token": "x"})

    async def test_abbruch_bei_microsoft_fuehrt_zum_agenten_zurueck(self):
        from app.api.integrations import oauth_callback

        svc = SimpleNamespace(ruecksprung_fuer=AsyncMock(return_value="/agents/a1?konnektoren=1"))
        antwort = await oauth_callback("microsoft", code=None, state="s", error="access_denied", service=svc)
        self.assertTrue(antwort.headers["location"].startswith("/agents/a1?konnektoren=1&error=access_denied"))


class StandUndMeldungTests(_Basis):
    async def test_stand_meldet_abgelaufen(self):
        async with self.Session() as db:
            await self._agentenkonto(db)
            agent = await db.get(Agent, "a1")
            leer = await agenten_konto.stand(db, SimpleNamespace(id="gibtsnicht"))
            with patch("app.core.integration_health.integration_status", return_value="expired"):
                s = await agenten_konto.stand(db, agent)
        self.assertEqual(leer["verbunden"], False)
        self.assertEqual((s["verbunden"], s["konto"], s["status"]), (True, "assistenz@example.com", "expired"))

    async def test_sicherheitsstatus_warnt_bei_abgelaufener_anmeldung(self):
        from app.core import sicherheitsstatus

        async with self.Session() as db:
            self.assertEqual((await sicherheitsstatus._agenten_konten(db))[0]["stand"], sicherheitsstatus.INFO)
            await self._agentenkonto(db, ablauf=datetime.now(timezone.utc) - timedelta(hours=1))
            punkt = (await sicherheitsstatus._agenten_konten(db))[0]
        self.assertEqual(punkt["stand"], sicherheitsstatus.WARNUNG)
        self.assertIn("Assistenz", punkt["text"])

    async def test_meldung_beim_scheitern_nennt_den_agenten_und_fuehrt_zu_ihm(self):
        async with self.Session() as db:
            await self._agentenkonto(db)
            zeile = (await db.execute(select(OAuthIntegration).where(OAuthIntegration.agent_id == "a1"))).scalar_one()
            svc = OAuthService(db, None)
            db_add = []
            db.add = db_add.append
            db.commit = AsyncMock()
            with patch("app.services.duty_service._publish_telegram", AsyncMock()), \
                    patch("app.core.push.push_to_user", AsyncMock()) as push:
                await svc._alert_refresh_failure(zeile, "HTTP 400 – invalid_grant")
        meldung = db_add[0]
        self.assertEqual(meldung.action_url, "/agents/a1?konnektoren=1")
        self.assertEqual(meldung.agent_id, "a1", "Meldung am Agenten, nicht als Systemmeldung bei allen Admins")
        self.assertIn("Assistenz", meldung.title)
        self.assertEqual(push.await_args.args[1], "u1")


class ApiTests(_Basis):
    """Endpunkte /agents/{id}/microsoft-konto: lesen wer den Agenten sieht, ändern nur Besitzer/Admin."""

    async def _nutzer(self, db, uid):
        return await db.get(User, uid)

    async def test_besitzer_sieht_stand_und_darf_verbinden(self):
        from app.api import agents as api

        async with self.Session() as db:
            await self._agentenkonto(db)
            u = await self._nutzer(db, "u1")
            with patch("app.core.oauth_providers.get_provider_client_id", return_value="client"):
                s = await api.get_agent_microsoft_konto("a1", user=u, db=db, redis=None)
        self.assertEqual((s["verbunden"], s["konto"], s["darf_verbinden"], s["verfuegbar"]),
                         (True, "assistenz@example.com", True, True))

    async def test_fremder_kommt_weder_an_stand_noch_an_verbinden_oder_trennen(self):
        from fastapi import HTTPException

        from app.api import agents as api

        async with self.Session() as db:
            await self._agentenkonto(db)
            u = await self._nutzer(db, "u2")
            for aufruf in (lambda: api.get_agent_microsoft_konto("a1", user=u, db=db, redis=None),
                           lambda: api.verbinden_agent_microsoft_konto("a1", user=u, db=db, redis=None),
                           lambda: api.trennen_agent_microsoft_konto("a1", user=u, db=db, redis=None)):
                with self.assertRaises(HTTPException) as e:
                    await aufruf()
                self.assertIn(e.exception.status_code, (403, 404))
            self.assertEqual(len((await db.execute(select(OAuthIntegration))).scalars().all()), 2)

    async def test_verbinden_liefert_anmeldeadresse_mit_agent_im_zustand(self):
        from app.api import agents as api

        async with self.Session() as db:
            u = await self._nutzer(db, "u1")
            with patch("app.services.oauth_service.OAuthService.generate_auth_url",
                       AsyncMock(return_value="https://login.example/x")) as gen:
                r = await api.verbinden_agent_microsoft_konto("a1", user=u, db=db, redis=None)
        self.assertEqual(r["auth_url"], "https://login.example/x")
        self.assertEqual(gen.await_args.kwargs["agent_id"], "a1")
        self.assertEqual(gen.await_args.kwargs["ruecksprung"], "/agents/a1?konnektoren=1")

    async def test_trennen_durch_besitzer_dann_wieder_das_konto_des_besitzers(self):
        from app.api import agents as api

        async with self.Session() as db:
            await self._agentenkonto(db)
            u = await self._nutzer(db, "u1")
            s = await api.trennen_agent_microsoft_konto("a1", user=u, db=db, redis=SimpleNamespace(client=None))
            self.assertFalse(s["verbunden"])
            self.assertEqual(await agenten_konto.token_fuer(db, await db.get(Agent, "a1")), "tok-mensch")


class EineStelleTests(unittest.TestCase):
    """Jeder Microsoft-Weg eines Agenten holt sein Token über agenten_konto.token_fuer."""

    def test_wege_eines_agenten(self):
        for datei in ("app/api/mcp_msgraph.py", "app/services/teams_gateway.py",
                      "app/services/teams_meetings.py", "app/core/mail_freigabe.py"):
            src = (ORCH / datei).read_text()
            self.assertTrue("token_fuer" in src or "_agenten_token" in src, datei)
            self.assertNotIn('get_valid_token("microsoft", agent.user_id)', src, datei)


if __name__ == "__main__":
    unittest.main()

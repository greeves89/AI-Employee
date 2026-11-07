"""Agenten-Tokens gelten nur aus dem Agentennetz (dependencies.agent_zugang_pruefen).

Live gefunden (2026-11-07): Das Token eines Agenten wurde über die öffentliche Adresse
(Tunnel → Caddy → Orchestrator) genauso angenommen wie direkt aus dem Agentennetz. Es
ist aus ``api_secret_key`` abgeleitet und ändert sich nie — ein einmal ausgelaufenes
Token wäre ein dauerhafter Zugang von außen.

MC/DC der Entscheidung ``gültig = Token passt ∧ Kennung da ∧ ¬über Eingang``:

| Token passt | Kennung | über Eingang | gültig | Test |
|---|---|---|---|---|
| ja   | ja   | nein | ja   | test_innen_gilt |
| nein | ja   | nein | nein | test_falsches_token |
| ja   | nein | nein | nein | test_ohne_kennung |
| ja   | ja   | ja   | nein | test_jeder_eingangskopf_weist_ab |
"""

import ast
import asyncio
import pathlib
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.requests import Request

from app import dependencies as dep
from app.dependencies import agent_zugang_pruefen, make_agent_token, verify_agent_token

APP = pathlib.Path(__file__).resolve().parents[1] / "app"


def _anfrage(client: str = "172.18.0.6", **koepfe) -> Request:
    """Eine Anfrage; ``client`` ist die Gegenstelle (Agenten-Container im Docker-Netz)."""
    return Request({"type": "http", "method": "GET", "path": "/api/v1/x", "query_string": b"",
                    "client": (client, 40000),
                    "headers": [(k.lower().replace("_", "-").encode(), v.encode()) for k, v in koepfe.items()]})


#: /proc/net/route eines Orchestrators in zwei Docker-Netzen (Werte wie auf der Testanlage).
ROUTEN = """Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT
eth0\t00000000\t010012AC\t0003\t0\t0\t0\t00000000\t0\t0\t0
eth0\t000012AC\t00000000\t0001\t0\t0\t0\t0000FFFF\t0\t0\t0
eth1\t000015AC\t00000000\t0001\t0\t0\t0\t0000FFFF\t0\t0\t0
"""


class PruefungTests(unittest.TestCase):

    def setUp(self):
        dep._abgewiesen_zuletzt.clear()
        self.aid = "e225cbe0"
        self.token = make_agent_token(self.aid)

    def test_innen_gilt(self):
        self.assertTrue(agent_zugang_pruefen(_anfrage(), self.aid, self.token))

    def test_falsches_token(self):
        self.assertFalse(agent_zugang_pruefen(_anfrage(), self.aid, "0" * 32))
        self.assertFalse(agent_zugang_pruefen(_anfrage(), self.aid, make_agent_token("anderer1")))

    def test_ohne_kennung(self):
        self.assertFalse(agent_zugang_pruefen(_anfrage(), "", self.token))
        self.assertFalse(agent_zugang_pruefen(_anfrage(), self.aid, ""))

    def test_jeder_eingangskopf_weist_ab(self):
        for kopf in ("X_Forwarded_For", "Forwarded", "X_Real_IP", "Cf_Connecting_IP", "Cf_Ray",
                     "Via", "X_Forwarded_Host"):
            with self.subTest(kopf=kopf), patch.object(dep, "_abweisung_merken") as merken:
                self.assertFalse(agent_zugang_pruefen(_anfrage(**{kopf: "203.0.113.7"}), self.aid, self.token))
                merken.assert_called_once()

    def test_leerer_kopf_zaehlt_auch(self):
        """Es zählt das Vorhandensein: ein leerer Kopf vor dem echten darf nicht durchrutschen."""
        with patch.object(dep, "_abweisung_merken"):
            self.assertFalse(agent_zugang_pruefen(_anfrage(X_Forwarded_For=""), self.aid, self.token))

    def test_ueber_den_veroeffentlichten_port_abgewiesen(self):
        """Host-nginx direkt auf 127.0.0.1:8000: kommt vom Gateway des Docker-Netzes an."""
        with patch.object(dep, "_gateways", return_value=frozenset({"172.18.0.1"})), \
                patch.object(dep, "_gateways_cache", (-1e9, frozenset())), \
                patch.object(dep, "_abweisung_merken") as merken:
            self.assertFalse(agent_zugang_pruefen(_anfrage(client="172.18.0.1"), self.aid, self.token))
            self.assertFalse(agent_zugang_pruefen(_anfrage(client="127.0.0.1"), self.aid, self.token))
            self.assertFalse(agent_zugang_pruefen(_anfrage(client="::1"), self.aid, self.token))
            self.assertTrue(agent_zugang_pruefen(_anfrage(client="172.18.0.6"), self.aid, self.token))
        self.assertEqual(merken.call_count, 3)


    def test_falsches_token_von_aussen_wird_nicht_protokolliert(self):
        """Raten von außen ist kein Leck — nur ein GÜLTIGES Token von außen ist ein Befund."""
        with patch.object(dep, "_abweisung_merken") as merken:
            self.assertFalse(agent_zugang_pruefen(_anfrage(X_Forwarded_For="1.2.3.4"), self.aid, "0" * 32))
            merken.assert_not_called()


class GatewayTests(unittest.TestCase):

    def test_gateways_aus_der_routentabelle(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".route", delete=False) as fh:
            fh.write(ROUTEN)
        self.assertEqual(dep._gateways(fh.name), frozenset({"172.18.0.1", "172.21.0.1"}))
        pathlib.Path(fh.name).unlink()

    def test_ohne_routentabelle_keine(self):
        self.assertEqual(dep._gateways("/gibt/es/nicht"), frozenset())

    def test_agentenadressen_sind_keine_gateways(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".route", delete=False) as fh:
            fh.write(ROUTEN)
        gw = dep._gateways(fh.name)
        pathlib.Path(fh.name).unlink()
        for ip in ("172.18.0.4", "172.18.0.6", "172.21.0.22"):
            self.assertNotIn(ip, gw)


class ProtokollTests(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        dep._abgewiesen_zuletzt.clear()

    async def test_hoechstens_ein_eintrag_je_agent_und_pause(self):
        aufrufe = []

        async def protokollieren(aid, pfad):
            aufrufe.append((aid, pfad))

        with patch.object(dep, "_abweisung_protokollieren", protokollieren):
            for _ in range(5):
                dep._abweisung_merken("aaaa1111", _anfrage(X_Forwarded_For="1.2.3.4"))
            dep._abweisung_merken("bbbb2222", _anfrage(X_Forwarded_For="1.2.3.4"))
            await asyncio.sleep(0)
        self.assertEqual([a for a, _ in aufrufe], ["aaaa1111", "bbbb2222"])

    async def test_logzeile_ebenfalls_gedrosselt(self):
        async def protokollieren(aid, pfad):
            pass

        with patch.object(dep, "_abweisung_protokollieren", protokollieren), \
                self.assertLogs("app.dependencies", level="WARNING") as log:
            for _ in range(5):
                dep._abweisung_merken("aaaa1111", _anfrage(X_Forwarded_For="1.2.3.4"))
            await asyncio.sleep(0)
        self.assertEqual(len(log.records), 1)

    async def test_fehlgeschlagenes_protokoll_gibt_die_drosselung_frei(self):
        dep._abgewiesen_zuletzt["aaaa1111"] = 1.0
        with patch("app.db.session.async_session_factory", side_effect=RuntimeError("db weg")), \
                self.assertLogs("app.dependencies", level="ERROR"):
            await dep._abweisung_protokollieren("aaaa1111", "/x")
        self.assertNotIn("aaaa1111", dep._abgewiesen_zuletzt)

    async def test_eintrag_landet_im_pruefprotokoll(self):
        from app.models.audit_log import AuditEventType, AuditLog
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(AuditLog.metadata.create_all, tables=[AuditLog.__table__])
        fabrik = async_sessionmaker(engine, expire_on_commit=False)
        with patch("app.db.session.async_session_factory", fabrik):
            await dep._abweisung_protokollieren("aaaa1111", "/api/v1/command-policies/for-agent/aaaa1111")
        async with fabrik() as db:
            from sqlalchemy import select
            zeile = (await db.execute(select(AuditLog))).scalar_one()
        await engine.dispose()
        self.assertEqual(zeile.event_type, AuditEventType.AGENT_TOKEN_EXTERN)
        self.assertEqual(zeile.outcome, "blocked")
        self.assertEqual(zeile.agent_id, "aaaa1111")


class EndpunktTests(unittest.TestCase):
    """Über eine echte FastAPI-Abhängigkeit — so, wie die Endpunkte sie nutzen."""

    def setUp(self):
        dep._abgewiesen_zuletzt.clear()
        app = FastAPI()

        @app.get("/probe")
        async def probe(agent=Depends(verify_agent_token)):
            return agent

        self.client = TestClient(app)
        self.koepfe = {"Authorization": f"Bearer {make_agent_token('e225cbe0')}", "X-Agent-ID": "e225cbe0"}

    def test_direkt_200_ueber_caddy_401(self):
        self.assertEqual(self.client.get("/probe", headers=self.koepfe).status_code, 200)
        with patch.object(dep, "_abweisung_merken"):
            r = self.client.get("/probe", headers={**self.koepfe, "X-Forwarded-For": "203.0.113.7"})
        self.assertEqual(r.status_code, 401)


class VerdrahtungTests(unittest.TestCase):
    """Es gibt GENAU EINE Stelle, die ein Agenten-Token mit dem erwarteten Wert vergleicht.

    Vorher waren es sieben, jede mit eigenem ``hmac.compare_digest`` — eine neue Stelle
    nach diesem Muster hätte die Eingangssperre wieder umgangen.
    """

    def test_kein_eigener_vergleich_mit_make_agent_token(self):
        funde = []
        for datei in APP.rglob("*.py"):
            baum = ast.parse(datei.read_text(encoding="utf-8"))
            for knoten in ast.walk(baum):
                if not (isinstance(knoten, ast.Call) and isinstance(knoten.func, ast.Attribute)
                        and knoten.func.attr == "compare_digest"):
                    continue
                if any(isinstance(n, ast.Call) and getattr(n.func, "id", getattr(n.func, "attr", "")) == "make_agent_token"
                       for n in ast.walk(knoten)):
                    funde.append(f"{datei.relative_to(APP)}:{knoten.lineno}")
        erlaubt = [f for f in funde if f.startswith("dependencies.py")]
        self.assertEqual(len(erlaubt), 1, funde)
        self.assertEqual(funde, erlaubt, "Agenten-Token bitte über agent_zugang_pruefen prüfen")

    def test_endpunkte_mit_agenten_token_nutzen_die_pruefung(self):
        for datei in ("api/mcp_email.py", "api/mcp_exchange.py", "api/mcp_msgraph.py",
                      "api/approval_rules.py", "api/url_allowlist.py", "api/egress.py"):
            with self.subTest(datei=datei):
                self.assertIn("agent_zugang_pruefen(request", (APP / datei).read_text(encoding="utf-8"))



class SentinelZugangTests(unittest.IsolatedAsyncioTestCase):
    """Der Sentinel-Zugang ist ebenso abgeleitet und ewig gültig — nur von innen."""

    async def test_von_innen_ja_ueber_eingang_nein(self):
        from fastapi import HTTPException

        from app.dependencies import get_sentinel_token, require_sentinel
        kopf = {"Authorization": f"Bearer {get_sentinel_token()}"}
        self.assertIsInstance(await require_sentinel(_anfrage(**kopf)), dep.SentinelPrincipal)
        for weg in ({"X_Forwarded_For": "1.2.3.4"}, {"client": "127.0.0.1"}):
            with self.subTest(weg=weg), self.assertRaises(HTTPException) as e:
                await require_sentinel(_anfrage(**{**weg, **kopf}))
            self.assertEqual(e.exception.status_code, 403)

    def test_kein_endpunkt_nimmt_ihn_an(self):
        """Stand 1.440.0: kein Router hängt an require_sentinel. Kommt einer dazu, gilt die
        Eingangsprüfung oben — dieser Test erinnert daran, das Sicherheitsmodell nachzuziehen."""
        funde = [f for f in APP.rglob("*.py") if "Depends(require_sentinel)" in f.read_text(encoding="utf-8")]
        self.assertEqual(funde, [], "Sentinel-Endpunkt neu: docs/SICHERHEITSMODELL.md nachziehen")


class SicherheitsstatusTests(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        from app.models.agent import Agent
        from app.models.audit_log import AuditLog
        from app.models.user import User
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(AuditLog.metadata.create_all,
                                tables=[AuditLog.__table__, User.__table__, Agent.__table__])
        self.db = async_sessionmaker(self.engine, expire_on_commit=False)()

    async def asyncTearDown(self):
        await self.db.close()
        await self.engine.dispose()

    async def _punkt(self):
        from app.core import sicherheitsstatus as ss
        return next(p for p in await ss._agenten_token(self.db) if p["schluessel"] == "agenten_token")

    async def test_ohne_versuch_ok(self):
        p = await self._punkt()
        self.assertEqual(p["stand"], "ok")

    async def test_gueltiges_token_von_aussen_ist_kritisch(self):
        from app.models.audit_log import AuditEventType, AuditLog
        self.db.add(AuditLog(agent_id="e225cbe0", event_type=AuditEventType.AGENT_TOKEN_EXTERN, outcome="blocked"))
        await self.db.commit()
        p = await self._punkt()
        self.assertEqual(p["stand"], "kritisch")
        self.assertIn("e225cbe0", p["text"])
        self.assertTrue(p["beheben"])

    async def test_alter_versuch_zaehlt_nicht(self):
        from app.models.audit_log import AuditEventType, AuditLog
        self.db.add(AuditLog(agent_id="e225cbe0", event_type=AuditEventType.AGENT_TOKEN_EXTERN, outcome="blocked",
                             created_at=datetime.now(timezone.utc) - timedelta(days=8)))
        await self.db.commit()
        self.assertEqual((await self._punkt())["stand"], "ok")

    async def test_steht_in_der_gesamtliste(self):
        import inspect
        from app.core import sicherheitsstatus as ss
        self.assertIn("_agenten_token(db)", inspect.getsource(ss.stand))


if __name__ == "__main__":
    unittest.main()

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


def _anfrage(**koepfe) -> Request:
    return Request({"type": "http", "method": "GET", "path": "/api/v1/x", "query_string": b"",
                    "headers": [(k.lower().replace("_", "-").encode(), v.encode()) for k, v in koepfe.items()]})


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
        for kopf in ("X_Forwarded_For", "Forwarded", "X_Real_IP", "Cf_Connecting_IP", "Cf_Ray"):
            with self.subTest(kopf=kopf), patch.object(dep, "_abweisung_merken") as merken:
                self.assertFalse(agent_zugang_pruefen(_anfrage(**{kopf: "203.0.113.7"}), self.aid, self.token))
                merken.assert_called_once()

    def test_leerer_kopf_zaehlt_nicht(self):
        """Ein leer gesetzter Kopf ist kein Proxy-Weg (Caddy setzt immer eine Adresse)."""
        self.assertTrue(agent_zugang_pruefen(_anfrage(X_Forwarded_For=""), self.aid, self.token))

    def test_falsches_token_von_aussen_wird_nicht_protokolliert(self):
        """Raten von außen ist kein Leck — nur ein GÜLTIGES Token von außen ist ein Befund."""
        with patch.object(dep, "_abweisung_merken") as merken:
            self.assertFalse(agent_zugang_pruefen(_anfrage(X_Forwarded_For="1.2.3.4"), self.aid, "0" * 32))
            merken.assert_not_called()


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

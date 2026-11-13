"""Zielliste am Ausgang (core/zielliste) — die erlaubten Adressen verbindlich machen.

Bis 1.449 prüfte nur der Werkzeug-Ausführer IM Agenten die Liste „Erlaubte Adressen“; Claude
Code und Codex gingen mit ihren eigenen Werkzeugen daran vorbei. Festgehalten wird:

* Die Entscheidung im Proxy (``egress_logik.ziel_erlaubt``) als Matrix über alle Eingänge.
* Was der Orchestrator dem Proxy gibt (``fuer_proxy``): nur bei eingeschalteter Liste UND
  Proxy; Modellanbieter, eigener Modell-Endpunkt und Ziele der Zugänge immer dabei.
* Umgebung beim Erstellen: Marke für den Netzwächter + Klartext-HTTP über den Proxy; fail
  closed, wenn die Einstellung nicht lesbar ist.
* Netzwächter: für solche Agenten alles hinaus gesperrt außer den eigenen Docker-Netzen.
* Freigabeliste: Muster in URL-Form zählen mit ihrem Host (passten vorher nie); ändern nur
  Besitzer und Admin.
* API: nur Besitzer/Admin, nur mit Proxy, Neustart beim Umschalten, Protokoll festgeschrieben.
"""

import importlib.util
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("ENCRYPTION_KEY", "c2VjcmV0LWtleS1mb3ItdGVzdHMtb25seS0zMmJ5dGU=")

from fastapi import HTTPException  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.dialects.postgresql import JSONB  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402

from app.core import zielliste  # noqa: E402
from app.models.agent import Agent, AgentState  # noqa: E402
from app.models.agent_access import AgentAccess  # noqa: E402
from app.models.ai_account import AIAccount  # noqa: E402
from app.models.audit_log import AuditLog  # noqa: E402
from app.models.url_allowlist import AgentUrlAllowlist  # noqa: E402
from app.models.user import User, UserRole  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "egress"))
import egress_logik as logik  # noqa: E402

_PFAD = Path(__file__).resolve().parents[2] / "scripts" / "netzwaechter.py"
_spec = importlib.util.spec_from_file_location("netzwaechter_zl", _PFAD)
nw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nw)


@compiles(JSONB, "sqlite")
def _jsonb_as_json(type_, compiler, **kw):
    return "JSON"


class ProxyEntscheidungTests(unittest.TestCase):
    """``ziel_erlaubt`` — jede Bedingung kippt das Ergebnis einmal für sich (MCDC)."""

    Z = {"muster": ["github.com", "*.example.com"], "rolle": None, "immer": ["api.anthropic.com"]}

    def test_matrix(self):
        faelle = [
            (None, "beliebig.example.org", True),                          # Liste aus
            (self.Z, "api.anthropic.com", True),                            # immer
            (self.Z, "github.com", True),                                   # Muster genau
            (self.Z, "a.b.example.com", True),                              # Muster mit Unterdomäne
            (self.Z, "example.com", True),                                  # *.x deckt x selbst
            (self.Z, "evil-github.com", False),                             # kein Teilstring-Treffer
            (self.Z, "github.com.evil.org", False),
            (self.Z, "wikipedia.org", False),                               # nicht auf der Liste
            (self.Z, "", False),
            ({**self.Z, "rolle": ["*.example.com"]}, "github.com", False),  # Rolle verbietet
            ({**self.Z, "rolle": ["*.example.com"]}, "x.example.com", True),
            ({**self.Z, "rolle": []}, "api.anthropic.com", True),           # immer schlägt Rolle
            ({**self.Z, "rolle": []}, "github.com", False),                 # Rolle ohne Muster sperrt
            ({"muster": ["*"], "rolle": None, "immer": []}, "irgendwas.org", True),
            (self.Z, "GitHub.COM.", True),                                  # Groß/klein, Punkt am Ende
        ]
        for ziele, host, erwartet in faelle:
            self.assertEqual(logik.ziel_erlaubt(host, ziele), erwartet, (ziele, host))

    def test_proxy_merkt_die_liste_mit_der_anmeldung(self):
        antworten = iter([(200, {"hosts": [], "zielliste": self.Z}), (200, {"hosts": []})])
        o = logik.Orchestrator("http://o", "t", senden=lambda pfad, daten: next(antworten))
        o.hosts(("a1", "t"))
        self.assertEqual(o.zielliste(("a1", "t")), self.Z)
        o.hosts(("a1", "t"), frisch=True)
        self.assertIsNone(o.zielliste(("a1", "t")), "ausgeschaltet → nicht mehr durchgesetzt")


class NetzwaechterTests(unittest.TestCase):
    AGENT = {"name": "ai-agent-a", "besitzer": "agent-aaaaaaaa",
             "ips": {"ai-employee-network": "172.18.0.3"}}
    SUBNETZE = {"ai-employee-network": "172.18.0.0/16"}

    def test_erkennung_nur_mit_proxy(self):
        agent = {"ai-employee.type": "agent"}
        proxy = "HTTPS_PROXY=http://a1:t@ai-employee-egress-proxy:8080"
        self.assertTrue(nw.mit_zielliste(agent, [proxy, "AIE_ZIELLISTE=1"]))
        self.assertFalse(nw.mit_zielliste(agent, [proxy]))
        self.assertFalse(nw.mit_zielliste(agent, ["AIE_ZIELLISTE=1"]), "ohne Proxy kein Weg hinaus")
        self.assertFalse(nw.mit_zielliste(agent, [proxy, "AIE_ZIELLISTE=0"]))

    def test_alles_hinaus_gesperrt_eigene_netze_davor_frei(self):
        mit = dict(self.AGENT, nur_proxy=True, zielliste=True)
        vor, _ = nw.regeln([mit], self.SUBNETZE, [])
        ip = "172.18.0.3"
        tcp = f"-A AIE-NETZ -s {ip}/32 -p tcp -j REJECT --reject-with tcp-reset"
        rest = f"-A AIE-NETZ -s {ip}/32 -j DROP"
        self.assertIn(tcp, vor)
        self.assertIn(rest, vor)
        eigene = vor.index(f"-A AIE-NETZ -s {ip}/32 -d 172.18.0.0/16 -j RETURN")
        self.assertLess(eigene, vor.index(tcp), "Proxy im eigenen Netz muss erreichbar bleiben")
        self.assertLess(vor.index(tcp), vor.index(rest))

    def test_ohne_zielliste_keine_sperre_aller_ports(self):
        vor, _ = nw.regeln([dict(self.AGENT, nur_proxy=True)], self.SUBNETZE, [])
        self.assertNotIn("-A AIE-NETZ -s 172.18.0.3/32 -j DROP", vor)


class _Db(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            for m in (User, Agent, AgentAccess, AuditLog, AgentUrlAllowlist, AIAccount):
                await conn.run_sync(m.metadata.create_all, tables=[m.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.Session() as db:
            db.add_all([
                User(id="u1", email="besitzer@example.com", name="Besitzer", role=UserRole.MEMBER),
                User(id="u2", email="kollege@example.com", name="Kollege", role=UserRole.MEMBER),
                User(id="adm", email="admin@example.com", name="Admin", role=UserRole.ADMIN),
                Agent(id="a1", name="Assistenz", state=AgentState.RUNNING, user_id="u1", config={},
                      container_id="c1"),
                AgentAccess(agent_id="a1", user_id="u2"),
                AgentUrlAllowlist(agent_id="a1", url_pattern="https://api.example.com/v1/*", is_active=True),
                AgentUrlAllowlist(agent_id="a1", url_pattern="*.github.com", is_active=True),
                AgentUrlAllowlist(agent_id="a1", url_pattern="aus.example.org", is_active=False),
            ])
            await db.commit()
        self._p = patch("app.core.egress.aktiv", return_value=True)
        self._p.start()

    async def asyncTearDown(self):
        self._p.stop()
        await self.engine.dispose()

    async def _als(self, uid, fn, *args, **kw):
        async with self.Session() as db:
            return await fn(*args, user=await db.get(User, uid), db=db, **kw)


class FuerProxyTests(_Db):
    async def test_aus_oder_ohne_proxy_nichts(self):
        async with self.Session() as db:
            a = await db.get(Agent, "a1")
            self.assertIsNone(await zielliste.fuer_proxy(db, a, set()))
            a.config = zielliste.mit(a.config, True)
            with patch("app.core.egress.aktiv", return_value=False):
                self.assertIsNone(await zielliste.fuer_proxy(db, a, set()))

    async def test_liste_rolle_und_immer(self):
        async with self.Session() as db:
            db.add(AIAccount(id=7, name="Eigenes", provider_type="custom", api_endpoint="https://llm.example.net/v1"))
            a = await db.get(Agent, "a1")
            a.config = zielliste.mit(a.config, True)
            a.ai_account_id = 7
            a.llm_config = {"base_url": "https://frei-gewaehlt.example.org/v1"}
            await db.commit()
            with patch("app.core.permissions.get_effective_permissions",
                       AsyncMock(return_value={"url_host_patterns": ["*.github.com", "https://x.example.com/a"]})):
                z = await zielliste.fuer_proxy(db, a, {"git.example.com"})
        self.assertEqual(z["muster"], ["*.github.com", "api.example.com"], "URL-Form → Host, inaktive weg")
        self.assertEqual(z["rolle"], ["*.github.com", "x.example.com"])
        for h in ("api.anthropic.com", "chatgpt.com", "api.openai.com", "llm.example.net", "git.example.com"):
            self.assertIn(h, z["immer"])
        self.assertNotIn("frei-gewaehlt.example.org", z["immer"],
                         "llm_config ändern auch Freigegebene — sein Endpunkt ist kein 'immer'")

    async def test_verbindung_gibt_die_liste_dem_proxy(self):
        from app.api import egress as api_egress

        async with self.Session() as db:
            a = await db.get(Agent, "a1")
            a.config = zielliste.mit(a.config, True)
            await db.commit()
            with patch.object(api_egress, "_freigaben", AsyncMock(return_value=[])), \
                    patch("app.core.anonymisierer.fuer_proxy", return_value=None), \
                    patch("app.core.permissions.get_effective_permissions", AsyncMock(return_value={})):
                r = await api_egress.verbindung(api_egress.Verbindung(agent_id="a1", agent_token="t"),
                                                SimpleNamespace(), db=db)
        self.assertIn("api.example.com", r["zielliste"]["muster"])


class UmgebungTests(_Db):
    async def _umgebung(self):
        from app.core import egress

        with patch("app.db.session.async_session_factory", self.Session):
            return await egress._zielliste_umgebung("a1", "http://a1:t@proxy:8080")

    async def test_marke_und_klartext_ueber_den_proxy(self):
        self.assertEqual(await self._umgebung(), {})
        async with self.Session() as db:
            a = await db.get(Agent, "a1")
            a.config = zielliste.mit(a.config, True)
            await db.commit()
        u = await self._umgebung()
        self.assertEqual(u["AIE_ZIELLISTE"], "1")
        self.assertEqual(u["HTTP_PROXY"], "http://a1:t@proxy:8080")

    async def test_fail_closed_wenn_nicht_lesbar(self):
        from app.core import egress

        def kaputt():
            raise RuntimeError("Datenbank weg")

        with patch("app.db.session.async_session_factory", kaputt), self.assertRaises(RuntimeError):
            await egress._zielliste_umgebung("a1", "http://p")


class FreigabelisteTests(_Db):
    def test_host_muster(self):
        from app.api.url_allowlist import host_muster, url_matches_pattern

        faelle = {"https://api.example.com/v1/*": "api.example.com", "*.GitHub.com": "*.github.com",
                  "example.com:8443/x": "example.com", "*": "*", "": None, "java script:x": None,
                  "a b.com": None, "*.": None}
        for roh, erwartet in faelle.items():
            self.assertEqual(host_muster(roh), erwartet, roh)
        self.assertTrue(url_matches_pattern("https://api.example.com/v2/x", "https://api.example.com/v1/*"),
                        "URL-Muster passte vorher nie")

    async def test_nur_besitzer_und_admin_aendern(self):
        from app.api import url_allowlist as api

        with self.assertRaises(HTTPException) as e:
            await self._als("u2", api.add_agent_url, "a1", api.CreateAgentEntry(url_pattern="evil.example.org"))
        self.assertEqual(e.exception.status_code, 403)
        r = await self._als("u1", api.add_agent_url, "a1", api.CreateAgentEntry(url_pattern="https://neu.example.com/x"))
        self.assertEqual(r["url_pattern"], "neu.example.com")
        with self.assertRaises(HTTPException) as e:
            await self._als("u1", api.add_agent_url, "a1", api.CreateAgentEntry(url_pattern="java script:x"))
        self.assertEqual(e.exception.status_code, 422)
        await self._als("adm", api.add_agent_url, "a1", api.CreateAgentEntry(url_pattern="admin.example.com"))


class ApiTests(_Db):
    async def _setzen(self, uid, aktiv, manager=None):
        from app.api import agents as api

        manager = manager or SimpleNamespace(restart_agent=AsyncMock())
        r = await self._als(uid, api.update_agent_zielliste, "a1", api.ZiellisteUpdate(aktiv=aktiv), manager=manager)
        return r, manager

    async def test_besitzer_schaltet_ein_neustart_und_protokoll(self):
        r, m = await self._setzen("u1", True)
        self.assertTrue(r["aktiv"] and r["wirksam"] and r["neu_gestartet"])
        m.restart_agent.assert_awaited_once_with("a1")
        async with self.Session() as db:
            prot = (await db.execute(select(AuditLog))).scalars().all()
        self.assertEqual([p.meta.get("aktion") for p in prot], ["zielliste"])
        r, m = await self._setzen("u1", True)
        m.restart_agent.assert_not_awaited()

    async def test_freigegebener_darf_nicht(self):
        with self.assertRaises(HTTPException) as e:
            await self._setzen("u2", True)
        self.assertEqual(e.exception.status_code, 403)

    async def test_ohne_proxy_nicht_einschaltbar(self):
        with patch("app.core.egress.aktiv", return_value=False), self.assertRaises(HTTPException) as e:
            await self._setzen("u1", True)
        self.assertEqual(e.exception.status_code, 409)


class SicherheitsstatusTests(_Db):
    async def test_punkt(self):
        from app.core import sicherheitsstatus as st

        async with self.Session() as db:
            self.assertEqual((await st._zielliste(db))[0]["stand"], st.INFO)
            a = await db.get(Agent, "a1")
            a.config = zielliste.mit(a.config, True)
            await db.commit()
            self.assertEqual((await st._zielliste(db))[0]["stand"], st.OK)
            with patch("app.core.egress.aktiv", return_value=False):
                self.assertEqual((await st._zielliste(db))[0]["stand"], st.WARNUNG)


if __name__ == "__main__":
    unittest.main()

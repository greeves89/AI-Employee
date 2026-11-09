"""Anonymisierer — Orchestrator-Seite: Einstellung, Proxy-Anbindung, Endpunkte, Status, Anleitung."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from starlette.requests import Request

from app.core import anonymisierer

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "egress"))
import egress_logik as logik  # noqa: E402


def _agent(aktiv=True, mode="claude_code", begriffe=("Herr Müller",), aid="a1", user_id="u1"):
    return SimpleNamespace(id=aid, name="Demo", mode=mode, user_id=user_id, container_id=None,
                           config={"anonymisierer": {"aktiv": aktiv, "begriffe": list(begriffe)}})


class EinstellungTests(unittest.TestCase):

    def test_bereinigen(self):
        self.assertEqual(anonymisierer.bereinigen(["  Herr   Müller ", "herr müller", "Firma ACME"]),
                         ["Herr Müller", "Firma ACME"])
        for schlecht in (['Name "x"'], ["a\\b"], ["ab"], ["x" * 101]):
            with self.subTest(schlecht=schlecht), self.assertRaises(anonymisierer.UngueltigeBegriffe):
                anonymisierer.bereinigen(schlecht)
        with self.assertRaises(anonymisierer.UngueltigeBegriffe):
            anonymisierer.bereinigen([f"Begriff {i:04d}" for i in range(anonymisierer.MAX_BEGRIFFE + 1)])

    def test_fuer_proxy(self):
        cfg = anonymisierer.fuer_proxy(_agent())
        self.assertEqual(cfg["begriffe"], ["Herr Müller"])
        self.assertIn("api.anthropic.com", cfg["ziele"])
        self.assertEqual(len(cfg["schluessel"]), 44)
        self.assertNotEqual(cfg["schluessel"], anonymisierer.fuer_proxy(_agent(aid="a2"))["schluessel"])
        self.assertIsNone(anonymisierer.fuer_proxy(_agent(aktiv=False)))
        self.assertIsNone(anonymisierer.fuer_proxy(_agent(mode="custom_llm")), "eigene Modelle: nicht abgedeckt")
        self.assertIsNotNone(anonymisierer.fuer_proxy(_agent(mode="codex_cli")))

    def test_telemetrie_aus_wenn_an(self):
        self.assertEqual(anonymisierer.umgebung(_agent())["DISABLE_TELEMETRY"], "1")
        self.assertEqual(anonymisierer.umgebung(_agent(aktiv=False)), {})
        src = (Path(__file__).resolve().parents[1] / "app/core/agent_manager.py").read_text(encoding="utf-8")
        self.assertEqual(src.count("**_plattform_geheimnisse(agent.id),"), src.count("**_anonym_umgebung(agent),"))

    def test_modell_hinweis_nur_wenn_an(self):
        from app.core.agent_manager import _anonym_aktiv, _render_claude_md
        self.assertTrue(_anonym_aktiv(_agent()))
        self.assertFalse(_anonym_aktiv(_agent(aktiv=False)))
        self.assertFalse(_anonym_aktiv(_agent(mode="custom_llm")))
        self.assertIn("Anonymisierte Personendaten", _render_claude_md([], anonym=True))
        self.assertNotIn("Anonymisierte Personendaten", _render_claude_md([], anonym=False))


class VerbindungTests(unittest.IsolatedAsyncioTestCase):

    async def _verbindung(self, agent):
        from app.api import egress as api_egress
        db = SimpleNamespace(get=AsyncMock(return_value=agent))
        anfrage = Request({"type": "http", "method": "POST", "path": "/", "query_string": b"", "headers": [],
                           "client": ("172.18.0.4", 1)})
        with patch.object(api_egress, "_freigaben", AsyncMock(return_value=[SimpleNamespace(hosts=["api.github.com"])])):
            return await api_egress.verbindung(api_egress.Verbindung(agent_id="a1", agent_token="t"), anfrage, db=db)

    async def test_an_ziele_aufbrechen_und_einstellung_mitgeben(self):
        aus = await self._verbindung(_agent())
        self.assertIn("api.anthropic.com", aus["hosts"])
        self.assertIn("api.github.com", aus["hosts"])
        self.assertEqual(aus["anonym"]["begriffe"], ["Herr Müller"])

    async def test_aus_wie_bisher(self):
        aus = await self._verbindung(_agent(aktiv=False))
        self.assertEqual(aus, {"hosts": ["api.github.com"]})


class ProxyKlientTests(unittest.TestCase):
    """egress_logik.Orchestrator merkt sich die Einstellung aus /egress/verbindung."""

    def test_anonym_aus_der_anmeldung(self):
        antworten = {"hosts": ["api.anthropic.com"], "anonym": {"schluessel": "k", "begriffe": [], "ziele": ["api.anthropic.com"]}}
        o = logik.Orchestrator("http://o", "t", senden=lambda pfad, daten: (200, antworten))
        o.hosts(("a1", "tok"))
        self.assertEqual(o.anonym(("a1", "tok"))["schluessel"], "k")
        o2 = logik.Orchestrator("http://o", "t", senden=lambda pfad, daten: (200, {"hosts": []}))
        o2.hosts(("a1", "tok"))
        self.assertIsNone(o2.anonym(("a1", "tok")))

    def test_modellanbieter_frisch_am_speicher_vorbei(self):
        """Gerade eingeschaltet: neue Verbindung zum Anbieter fragt sofort, nicht nach 60 s."""
        aufrufe = []

        def senden(pfad, daten):
            aufrufe.append(1)
            return 200, {"hosts": []}

        o = logik.Orchestrator("http://o", "t", jetzt=lambda: 0.0, senden=senden)
        o.hosts(("a1", "tok"))
        o.hosts(("a1", "tok"))
        self.assertEqual(len(aufrufe), 1, "sonst aus dem Speicher")
        o.hosts(("a1", "tok"), frisch=True)
        self.assertEqual(len(aufrufe), 2)
        self.assertIn("api.anthropic.com", logik.MODELL_ANBIETER)
        self.assertTrue(set(anonymisierer.ZIELE) <= logik.MODELL_ANBIETER, "jedes Ziel wird frisch geprüft")

    def test_bei_ausfall_bleibt_die_letzte_einstellung(self):
        """Orchestrator kurz weg: lieber weiter anonymisieren als plötzlich Klartext."""
        zeit = [0.0]
        antworten = [(200, {"hosts": ["api.anthropic.com"], "anonym": {"schluessel": "k", "ziele": []}})]

        def senden(pfad, daten):
            if antworten:
                return antworten.pop()
            raise logik.OrchestratorFehler("weg")

        o = logik.Orchestrator("http://o", "t", jetzt=lambda: zeit[0], senden=senden)
        o.hosts(("a1", "tok"))
        zeit[0] = logik.ANMELDUNG_FRISCH + 1
        o.hosts(("a1", "tok"))
        self.assertEqual(o.anonym(("a1", "tok"))["schluessel"], "k")


class EndpunktTests(unittest.IsolatedAsyncioTestCase):

    async def _patch(self, agent, user, body):
        from app.api import agents as api
        manager = SimpleNamespace(_get_agent=AsyncMock(return_value=agent), restart_agent=AsyncMock())
        self.manager = manager
        db = SimpleNamespace(commit=AsyncMock())
        with patch.object(api, "_check_owner", AsyncMock()), \
                patch("app.core.audit.protokolliere", AsyncMock()) as protokoll, \
                patch("app.core.egress.aktiv", return_value=True):
            aus = await api.update_agent_anonymisierer("a1", api.AnonymisiererUpdate(**body), user=user, db=db,
                                                       manager=manager)
        return aus, protokoll

    async def test_besitzer_schaltet_ein(self):
        from app.models.user import UserRole
        agent = _agent(aktiv=False, begriffe=())
        aus, protokoll = await self._patch(agent, SimpleNamespace(id="u1", role=UserRole.MEMBER),
                                           {"aktiv": True, "begriffe": ["Herr Müller"]})
        self.assertTrue(aus["aktiv"] and aus["wirksam"])
        self.assertEqual(agent.config["anonymisierer"], {"aktiv": True, "begriffe": ["Herr Müller"]})
        aufruf = protokoll.await_args
        self.assertNotIn("Müller", str(aufruf), "Begriffe sind Namen — nie ins Prüfprotokoll")
        self.assertEqual(aufruf.kwargs["meta"]["begriffe_anzahl"], 1)

    async def test_einschalten_startet_den_laufenden_agenten_neu(self):
        """Offene Verbindungen zum Anbieter liefen sonst ungeprüft weiter."""
        from app.models.user import UserRole
        besitzer = SimpleNamespace(id="u1", role=UserRole.MEMBER)
        laufend = _agent(aktiv=False); laufend.container_id = "c1"
        aus, _ = await self._patch(laufend, besitzer, {"aktiv": True})
        self.manager.restart_agent.assert_awaited_once_with("a1")
        self.assertTrue(aus.get("neu_gestartet"))
        for agent, body in ((_agent(aktiv=True), {"begriffe": ["Neu Name"]}), (_agent(aktiv=False), {"aktiv": True})):
            agent.container_id = "c1" if agent.config["anonymisierer"]["aktiv"] else None
            await self._patch(agent, besitzer, body)
            self.manager.restart_agent.assert_not_awaited()

    async def test_fremder_darf_nicht(self):
        from app.models.user import UserRole
        with self.assertRaises(HTTPException) as e:
            await self._patch(_agent(), SimpleNamespace(id="u2", role=UserRole.MEMBER), {"aktiv": False})
        self.assertEqual(e.exception.status_code, 403)

    async def test_admin_darf(self):
        from app.models.user import UserRole
        aus, _ = await self._patch(_agent(), SimpleNamespace(id="adm", role=UserRole.ADMIN), {"aktiv": False})
        self.assertFalse(aus["aktiv"])

    async def test_eigenes_modell_und_ungueltige_begriffe_abgelehnt(self):
        from app.models.user import UserRole
        besitzer = SimpleNamespace(id="u1", role=UserRole.MEMBER)
        with self.assertRaises(HTTPException) as e:
            await self._patch(_agent(aktiv=False, mode="custom_llm"), besitzer, {"aktiv": True})
        self.assertEqual(e.exception.status_code, 422)
        with self.assertRaises(HTTPException) as e:
            await self._patch(_agent(), besitzer, {"begriffe": ['Name "x"']})
        self.assertEqual(e.exception.status_code, 422)

    async def test_ohne_proxy_wirkt_nicht_und_sagt_das(self):
        from app.api import agents as api
        with patch("app.core.egress.aktiv", return_value=False):
            stand = await api._anonymisierer_stand(_agent())
        self.assertFalse(stand["wirksam"])
        self.assertIn("Egress-Proxy ist aus", stand["hinweis"])


class StatusTests(unittest.IsolatedAsyncioTestCase):

    async def _punkt(self, agenten, proxy=True):
        from app.core import sicherheitsstatus as ss
        zeilen = [(a.id, a.name, a.mode, a.config) for a in agenten]
        db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(all=lambda: zeilen)))
        with patch("app.core.egress.aktiv", return_value=proxy):
            return (await ss._anonymisierer(db))[0]

    async def test_drei_zustaende(self):
        self.assertEqual((await self._punkt([_agent(aktiv=False)]))["stand"], "info")
        self.assertEqual((await self._punkt([_agent()]))["stand"], "ok")
        p = await self._punkt([_agent()], proxy=False)
        self.assertEqual(p["stand"], "warnung")
        self.assertIn("Egress-Proxy", p["beheben"])

    async def test_steht_in_der_gesamtliste(self):
        import inspect
        from app.core import sicherheitsstatus as ss
        self.assertIn("_anonymisierer(db)", inspect.getsource(ss.stand))


if __name__ == "__main__":
    unittest.main()

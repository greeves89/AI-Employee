"""Agenten aktuell halten (core/agenten_aktualisieren.py).

Geprüft wird, was beim Kunden ohne Admin passieren muss:

* ``beschaeftigt``: jeder Grund schlägt an, und ohne Grund ist der Agent frei — in beide
  Richtungen, eine Prüfung, die immer „frei“ sagt, risse Gespräche ab.
* ``tick``: höchstens EIN Agent je Aufruf (gestaffelt), nie ein beschäftigter, nie ein
  aktueller, nie ein gestoppter; „aus“ tut nichts; ein Fehlschlag wird für diese
  Version gemerkt und nicht wiederholt; jeder Lauf steht im Prüfprotokoll.
* ``start_agent``: ein veralteter gestoppter Agent wird neu erstellt, ein aktueller
  normal gestartet.
* Endpunkte: nur Admins, Modus geprüft, Stand mit Gründen.
"""
import inspect
import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import settings as api
from app.config import settings
from app.core import agenten_aktualisieren as aa
from app.dependencies import require_admin
from app.models.agent import Agent, AgentState
from app.models.audit_log import AuditLog
from app.models.chat_message import ChatMessage
from app.models.command_approval import CommandApproval
from app.models.platform_settings import PlatformSettings
from app.models.task import Task, TaskStatus
from app.models.user import User, UserRole

NEU, ALT = "9.9.9", "9.9.8"
ADMIN = User(id="ad", email="admin@example.invalid", name="Admin", role=UserRole.ADMIN)


def _agent(aid, name, *, version=ALT, state=AgentState.RUNNING, **cfg):
    return Agent(id=aid, name=name, user_id="u1", mode="claude_code", state=state,
                 config={"agent_version": version, **cfg})


class Basis(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Agent.metadata.create_all, tables=[
                User.__table__, Agent.__table__, Task.__table__, ChatMessage.__table__,
                AuditLog.__table__, PlatformSettings.__table__, CommandApproval.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        self.db = self.Session()
        self.db.add(User(id="u1", email="u@example.invalid", name="U", role=UserRole.MEMBER))
        await self.db.commit()
        self.redis = SimpleNamespace(get_agent_status=AsyncMock(return_value={}))
        self.besprechung = set()
        self.patches = [
            patch.object(aa, "get_agent_version", return_value=NEU),
            patch("app.core.egress.aktiv", return_value=False),
            patch.object(aa, "_in_besprechung", AsyncMock(side_effect=lambda db, aid: aid in self.besprechung)),
            patch.object(settings, aa.EINSTELLUNG, "leerlauf"),
            patch.object(settings, aa.ANGEHALTEN, ""),
            patch.object(aa, "abbild_bereit", AsyncMock(return_value=True)),
            patch("app.db.session.resilient_session", lambda: self.Session()),
        ]
        for p in self.patches:
            p.start()

    async def asyncTearDown(self):
        for p in self.patches:
            p.stop()
        await self.db.close()
        await self.engine.dispose()

    async def anlegen(self, *agenten):
        self.db.add_all(agenten)
        await self.db.commit()


class BeschaeftigtTests(Basis):
    async def test_frei(self):
        a = _agent("a1", "Frei")
        await self.anlegen(a)
        self.assertIsNone(await aa.beschaeftigt(self.db, self.redis, a, ruhe=aa.RUHE))

    async def test_jeder_grund(self):
        await self.anlegen(_agent("w1", "W", state=AgentState.WORKING), _agent("l1", "L"),
                           _agent("t1", "T"), _agent("m1", "M"), _agent("g1", "G"), _agent("o1", "O"),
                           _agent("f1", "F"), _agent("f2", "F2"))
        self.db.add(CommandApproval(agent_id="f1", command="rm x", status="pending"))
        self.db.add(CommandApproval(agent_id="f2", command="rm x", status="approved"))
        self.db.add(Task(id="task1", title="x", prompt="x", agent_id="t1", status=TaskStatus.RUNNING))
        self.db.add(ChatMessage(agent_id="g1", session_id="s", message_id="m", role="user", content="hi",
                                timestamp=datetime.now(UTC) - timedelta(minutes=3)))
        self.db.add(ChatMessage(agent_id="o1", session_id="s", message_id="m", role="user", content="hi",
                                timestamp=datetime.now(UTC) - timedelta(hours=2)))
        await self.db.commit()
        self.besprechung.add("m1")
        self.redis.get_agent_status = AsyncMock(side_effect=lambda aid: {"state": "working"} if aid == "l1" else {})
        erwartet = {"w1": "arbeitet", "l1": "arbeitet", "t1": "auftrag", "m1": "termin", "g1": "gespraech", "o1": None,
                    "f1": "freigabe", "f2": None}
        for aid, grund in erwartet.items():
            with self.subTest(aid):
                agent = await self.db.get(Agent, aid)
                self.assertEqual(await aa.beschaeftigt(self.db, self.redis, agent, ruhe=aa.RUHE), grund)

    async def test_ohne_ruhe_zaehlt_das_gespraech_nicht(self):
        """Der Leerlauf-Stopp misst Ruhe selbst — dort darf ein Gespräch nicht blockieren."""
        await self.anlegen(_agent("g1", "G"))
        self.db.add(ChatMessage(agent_id="g1", session_id="s", message_id="m", role="user", content="hi",
                                timestamp=datetime.now(UTC)))
        await self.db.commit()
        self.assertIsNone(await aa.beschaeftigt(self.db, self.redis, await self.db.get(Agent, "g1")))

    async def test_redis_ausfall_blockiert_nicht(self):
        await self.anlegen(_agent("a1", "A"))
        self.redis.get_agent_status = AsyncMock(side_effect=ConnectionError("weg"))
        self.assertIsNone(await aa.beschaeftigt(self.db, self.redis, await self.db.get(Agent, "a1")))


class TickTests(Basis):
    def manager(self, fehler_bei=()):
        aktualisiert = []

        class Mgr:
            def __init__(inner, db, docker, redis):
                inner.db = db

            async def update_agent(inner, aid, **k):
                if aid in fehler_bei:
                    raise RuntimeError("Abbild fehlt")
                agent = await inner.db.get(Agent, aid)
                agent.config = {**agent.config, "agent_version": NEU}
                await inner.db.commit()
                aktualisiert.append(aid)
                return agent
        return Mgr, aktualisiert

    async def tick(self, mgr):
        with patch("app.core.agent_manager.AgentManager", mgr):
            return await aa.tick(object(), self.redis)

    async def test_einer_je_durchlauf_nie_beschaeftigt_aktuell_gestoppt(self):
        await self.anlegen(_agent("a1", "Frei1"), _agent("a2", "Frei2"), _agent("ak", "Aktuell", version=NEU),
                           _agent("st", "Gestoppt", state=AgentState.STOPPED), _agent("w1", "Arbeitet", state=AgentState.WORKING))
        mgr, aktualisiert = self.manager()
        e1 = await self.tick(mgr)
        self.assertEqual(len(aktualisiert), 1, "höchstens einer je Durchlauf")
        self.assertEqual((e1["von"], e1["nach"]), (ALT, NEU))
        await self.tick(mgr)
        self.assertEqual(sorted(aktualisiert), ["a1", "a2"])
        self.assertIsNone(await self.tick(mgr), "danach ist niemand mehr dran")
        self.assertNotIn("ak", aktualisiert)
        self.assertNotIn("st", aktualisiert)
        self.assertNotIn("w1", aktualisiert)
        eintraege = (await self.db.execute(select(AuditLog))).scalars().all()
        self.assertEqual(sorted(e.agent_id for e in eintraege), ["a1", "a2"])
        self.assertTrue(all(e.event_type == "agent_updated" and e.outcome == "success" for e in eintraege))

    async def test_aus_tut_nichts(self):
        await self.anlegen(_agent("a1", "A"))
        mgr, aktualisiert = self.manager()
        with patch.object(settings, aa.EINSTELLUNG, "aus"):
            self.assertIsNone(await self.tick(mgr))
        self.assertEqual(aktualisiert, [])

    async def test_fehlschlag_haelt_die_automatik_an(self):
        """update_agent entfernt den alten Behälter zuerst — nach einem Fehlschlag darf
        der nächste Takt NICHT den nächsten Agenten riskieren (Sicherheitsprüfung H1)."""
        await self.anlegen(_agent("a1", "Kaputt"), _agent("a2", "Gut"))
        await self.db.execute(Agent.__table__.update().where(Agent.id == "a1")
                              .values(updated_at=datetime.now(UTC) - timedelta(days=1)))
        await self.db.commit()
        mgr, aktualisiert = self.manager(fehler_bei={"a1"})
        self.assertEqual(await self.tick(mgr), {"gescheitert": "a1"})
        self.assertTrue(aa.angehalten())
        self.assertIsNone(await self.tick(mgr), "angehalten: kein weiterer Agent")
        self.assertEqual(aktualisiert, [])
        async with self.Session() as frisch:
            a1 = await frisch.get(Agent, "a1")
            self.assertEqual(a1.config[aa.FEHLGESCHLAGEN], NEU)
            self.assertEqual(await aa.grund(frisch, self.redis, a1), "fehlgeschlagen")
            self.assertEqual(await aa.grund(frisch, self.redis, await frisch.get(Agent, "a2")), "angehalten")
            self.assertEqual((await frisch.get(PlatformSettings, aa.ANGEHALTEN)).value, NEU, "übersteht Neustart")
            fehl = (await frisch.execute(select(AuditLog).where(AuditLog.outcome == "failure"))).scalars().one()
            self.assertIn("Abbild fehlt", fehl.meta["fehler"])

    async def test_ohne_abbild_wird_nichts_angefasst(self):
        await self.anlegen(_agent("a1", "A"))
        mgr, aktualisiert = self.manager()
        with patch.object(aa, "abbild_bereit", AsyncMock(return_value=False)):
            self.assertEqual(await self.tick(mgr), {"uebersprungen": "abbild_fehlt"})
        self.assertEqual(aktualisiert, [])
        self.assertFalse(aa.angehalten(), "kein Fehlschlag, nur warten")

    async def test_kurz_vor_dem_neuerstellen_beschaeftigt(self):
        """Zwischen Auswahl und Neuerstellen kommt eine Nachricht: dann nicht (Prüfung M3)."""
        await self.anlegen(_agent("a1", "A"))
        mgr, aktualisiert = self.manager()
        zaehler = {"n": 0}
        echt = aa.beschaeftigt

        async def zweites_mal_beschaeftigt(*a, **k):
            zaehler["n"] += 1
            return "gespraech" if zaehler["n"] > 1 else await echt(*a, **k)
        with patch.object(aa, "beschaeftigt", zweites_mal_beschaeftigt):
            self.assertIsNone(await self.tick(mgr))
        self.assertEqual(aktualisiert, [])

    async def test_protokollfehler_fuehrt_nicht_zu_wiederholung(self):
        await self.anlegen(_agent("a1", "Kaputt"))
        mgr, _ = self.manager(fehler_bei={"a1"})
        with patch.object(aa, "_protokollieren", AsyncMock(side_effect=RuntimeError("DB weg"))):
            self.assertEqual(await self.tick(mgr), {"gescheitert": "a1"})
        self.assertTrue(aa.angehalten())

    async def test_gruende_fuer_die_anzeige(self):
        await self.anlegen(_agent("st", "S", state=AgentState.STOPPED), _agent("a1", "A"))
        self.assertEqual(await aa.grund(self.db, self.redis, await self.db.get(Agent, "st")), "gestoppt")
        self.assertEqual(await aa.grund(self.db, self.redis, await self.db.get(Agent, "a1")), "bereit")
        with patch.object(settings, aa.EINSTELLUNG, "aus"):
            self.assertEqual(await aa.grund(self.db, self.redis, await self.db.get(Agent, "a1")), "aus")
        self.assertEqual(set(aa.GRUENDE) >= {"arbeitet", "auftrag", "termin", "gespraech"}, True)

    def test_unbekannter_wert_faellt_auf_vorgabe(self):
        with patch.object(settings, aa.EINSTELLUNG, "irgendwas"):
            self.assertEqual(aa.modus(), "leerlauf")


class StartTests(unittest.IsolatedAsyncioTestCase):
    async def _start(self, version, *, laeuft=False, abbild=True):
        from app.core.agent_manager import AgentManager

        agent = SimpleNamespace(id="a1", container_id="c1", config={"agent_version": version},
                                state=AgentState.RUNNING if laeuft else AgentState.STOPPED)
        mgr = AgentManager.__new__(AgentManager)
        mgr.db = MagicMock(commit=AsyncMock())
        mgr.docker = MagicMock()
        mgr.docker.get_container_status.return_value = "running" if laeuft else "exited"
        mgr._publish_event = AsyncMock()
        mgr._get_agent = AsyncMock(return_value=agent)
        mgr.update_agent = AsyncMock(return_value=agent)
        mgr.refresh_instructions = AsyncMock()
        with patch("app.core.agenten_aktualisieren.get_agent_version", return_value=NEU), \
                patch("app.core.egress.aktiv", return_value=False), \
                patch("app.core.agenten_aktualisieren.abbild_bereit", AsyncMock(return_value=abbild)), \
                patch("app.core.agent_manager.flag_modified"):
            await mgr.start_agent("a1")
        return mgr

    async def test_veraltet_wird_neu_erstellt(self):
        mgr = await self._start(ALT)
        mgr.update_agent.assert_awaited_once_with("a1", nur_wenn_noetig=True)
        mgr.docker.start_container.assert_not_called()

    async def test_aktuell_startet_normal(self):
        mgr = await self._start(NEU)
        mgr.update_agent.assert_not_awaited()
        mgr.docker.start_container.assert_called_once_with("c1")

    async def test_laufender_agent_wird_nicht_abgerissen(self):
        """„Start“ auf einen laufenden veralteten Agenten (Doppelklick, Fortsetzen): kein Neuerstellen."""
        mgr = await self._start(ALT, laeuft=True)
        mgr.update_agent.assert_not_awaited()

    async def test_ohne_abbild_startet_der_alte_behaelter(self):
        """Ohne Abbild wäre der Agent nach dem Entfernen gar nicht mehr startbar (Prüfung M1)."""
        mgr = await self._start(ALT, abbild=False)
        mgr.update_agent.assert_not_awaited()
        mgr.docker.start_container.assert_called_once_with("c1")


class EndpunktTests(Basis):
    def test_nur_admins(self):
        for fn in (api.get_agenten_updates, api.set_agenten_updates):
            with self.subTest(fn.__name__):
                self.assertIs(inspect.signature(fn).parameters["user"].default.dependency, require_admin)

    async def test_stand_und_umschalten(self):
        await self.anlegen(_agent("a1", "Alt"), _agent("a2", "Neu", version=NEU),
                           _agent("st", "Gestoppt", state=AgentState.STOPPED))
        req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=self.redis)))
        with patch.object(api, "_einstellungen_protokollieren", AsyncMock()) as prot:
            stand = await api.set_agenten_updates(api.AgentenUpdates(modus="aus"), req, user=ADMIN, db=self.db)
            self.assertEqual(settings.agenten_auto_update, "aus")
            prot.assert_awaited_once()
        self.assertEqual(stand["modus"], "aus")
        self.assertEqual({a["id"]: a["grund"] for a in stand["ausstehend"]}, {"a1": "aus", "st": "gestoppt"})
        async with self.Session() as frisch:
            self.assertEqual((await frisch.get(PlatformSettings, aa.EINSTELLUNG)).value, "aus")

    async def test_speichern_nimmt_die_automatik_wieder_auf(self):
        settings.agenten_auto_update_angehalten = NEU
        self.assertTrue(aa.angehalten())
        req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=self.redis)))
        with patch.object(api, "_einstellungen_protokollieren", AsyncMock()):
            stand = await api.set_agenten_updates(api.AgentenUpdates(modus="leerlauf"), req, user=ADMIN, db=self.db)
        self.assertFalse(stand["angehalten"])
        async with self.Session() as frisch:
            self.assertEqual((await frisch.get(PlatformSettings, aa.ANGEHALTEN)).value, "-",
                             "nicht leer — leere Werte übergeht das Laden beim Start")

    async def test_unbekannter_modus(self):
        req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=self.redis)))
        with self.assertRaises(HTTPException) as f:
            await api.set_agenten_updates(api.AgentenUpdates(modus="sofort"), req, user=ADMIN, db=self.db)
        self.assertEqual(f.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()


class SprachsitzungTests(Basis):
    """Eine Sprachsitzung schreibt nicht laufend Chat-Zeilen — sie zählt trotzdem als Gespräch."""

    async def test_offene_sprachsitzung_ist_beschaeftigt(self):
        from app.core import sprachsitzungen
        await self.anlegen(_agent("v1", "Stimme"))
        agent = await self.db.get(Agent, "v1")
        k1, k2 = sprachsitzungen.anmelden("v1"), sprachsitzungen.anmelden("v1")   # zwei Geräte
        try:
            self.assertEqual(await aa.beschaeftigt(self.db, self.redis, agent, ruhe=aa.RUHE), "sprache")
            sprachsitzungen.abmelden(k1)
            self.assertTrue(sprachsitzungen.aktiv("v1"), "eine Sitzung ist noch offen")
        finally:
            sprachsitzungen.abmelden(k2)
        self.assertFalse(sprachsitzungen.aktiv("v1"))
        self.assertIsNone(await aa.beschaeftigt(self.db, self.redis, agent, ruhe=aa.RUHE))

    def test_halb_offene_sitzung_zaehlt_nach_zehn_minuten_nicht_mehr(self):
        """Tab offen, Gerät schläft: ohne Lebenszeichen darf sie das Update nicht ewig sperren."""
        from app.core import sprachsitzungen
        jetzt = [1000.0]
        with patch.object(sprachsitzungen.time, "monotonic", lambda: jetzt[0]):
            k = sprachsitzungen.anmelden("v2")
            try:
                jetzt[0] += sprachsitzungen.FRISCH - 1
                self.assertTrue(sprachsitzungen.aktiv("v2"))
                sprachsitzungen.lebenszeichen(k)
                jetzt[0] += sprachsitzungen.FRISCH - 1
                self.assertTrue(sprachsitzungen.aktiv("v2"), "Lebenszeichen hält sie frisch")
                jetzt[0] += 2
                self.assertFalse(sprachsitzungen.aktiv("v2"))
            finally:
                sprachsitzungen.abmelden(k)

    def test_abmelden_unbekannter_kennung_schadet_nicht(self):
        from app.core import sprachsitzungen
        sprachsitzungen.abmelden(-1)
        self.assertFalse(sprachsitzungen.aktiv("nie-da"))

    def test_ws_meldet_an_und_im_finally_ab(self):
        """Verdrahtung im Syntaxbaum (nicht im Text): angemeldet im Sprach-Endpunkt, abgemeldet
        im finally genau des try, das danach kommt; Lebenszeichen in der Empfangsschleife."""
        import ast
        from pathlib import Path
        baum = ast.parse((Path(__file__).resolve().parents[1] / "app" / "api" / "ws.py").read_text())
        # Seit 1.453.0 liegt die Gesprächsschleife in ``_sprachsitzung_betreiben`` — App und
        # Teams-Teilnehmer (``ws_teams_stimme``) laufen beide hindurch.
        fn = next(n for n in ast.walk(baum) if isinstance(n, ast.AsyncFunctionDef) and n.name == "_sprachsitzung_betreiben")
        for endpunkt in ("ws_agent_voice", "ws_teams_stimme"):
            ep = next(n for n in ast.walk(baum) if isinstance(n, ast.AsyncFunctionDef) and n.name == endpunkt)
            self.assertTrue(any(isinstance(c, ast.Call) and getattr(c.func, "id", "") == "_sprachsitzung_betreiben"
                                for c in ast.walk(ep)), endpunkt)

        def ruft(knoten, name):
            return any(isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                       and c.func.attr == name and getattr(c.func.value, "id", "") == "sprachsitzungen"
                       for c in ast.walk(knoten))
        koerper = fn.body
        an = next(i for i, n in enumerate(koerper) if ruft(n, "anmelden"))
        versuch = next(n for n in koerper[an + 1:] if isinstance(n, ast.Try))
        self.assertTrue(any(ruft(n, "abmelden") for n in versuch.finalbody), "abmelden im finally")
        self.assertTrue(any(ruft(n, "lebenszeichen") for n in versuch.body), "Lebenszeichen im Gespräch")
        self.assertFalse(any(ruft(n, "anmelden") for n in koerper[:an]))


if __name__ == "__main__":
    unittest.main()

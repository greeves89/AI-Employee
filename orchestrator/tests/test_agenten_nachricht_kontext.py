"""Rueckfragen und Uebergaben zwischen Agenten brauchen Kontext (#884) — und
die Zielkette, an der sich der Empfaenger orientiert (#881)."""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

from app.core import agenten_nachricht as an
from app.core import zielkette as zk
from app.models.task import Task


class Kontext(unittest.TestCase):
    """MCDC: Typ verlangt Kontext? | Anlass da? | Auswirkung da? | Feld oder Zeile?"""

    def test_einfache_nachricht_braucht_nichts(self):
        for typ in ("message", "response", "notification", "status_update", None):
            self.assertEqual(an.kontext(typ, "Hallo", None, None), {"anlass": "", "auswirkung": ""})

    def test_frage_ohne_kontext_wird_abgelehnt(self):
        with self.assertRaises(an.KontextFehlt) as fehler:
            an.kontext("question", "Soll ich das löschen?", None, None)
        self.assertEqual(fehler.exception.fehlend, ["anlass", "auswirkung"])
        self.assertIn("Anlass", str(fehler.exception))

    def test_nur_ein_teil_fehlt(self):
        with self.assertRaises(an.KontextFehlt) as fehler:
            an.kontext("handoff", "Übernimm bitte", "Ich bin mit dem Export fertig", None)
        self.assertEqual(fehler.exception.fehlend, ["auswirkung"])

    def test_felder_reichen(self):
        werte = an.kontext("question", "Löschen?", "Der Ordner ist voll", "Ohne Löschen stoppt der Export")
        self.assertEqual(werte["anlass"], "Der Ordner ist voll")

    def test_zeilen_im_text_reichen_auch(self):
        """Agenten mit einem aelteren Werkzeug kennen die Felder nicht."""
        text = "Soll ich löschen?\nAnlass: Der Ordner ist voll\nAuswirkung: Ohne Löschen stoppt der Export"
        werte = an.kontext("question", text, None, None)
        self.assertEqual(werte["auswirkung"], "Ohne Löschen stoppt der Export")

    def test_ein_wort_ist_kein_kontext(self):
        with self.assertRaises(an.KontextFehlt):
            an.kontext("question", "Löschen?", "weil", "egal")

    def test_empfaenger_liest_text_kontext_und_auftrag(self):
        werte = {"anlass": "Der Ordner ist voll", "auswirkung": "Ohne Löschen stoppt der Export"}
        text = an.mit_kontext("Soll ich löschen?", werte, "- Monatsbericht\n- Export bauen")
        self.assertTrue(text.startswith("Soll ich löschen?"))
        self.assertIn("Anlass: Der Ordner ist voll", text)
        self.assertIn("Der Absender arbeitet an:", text)
        self.assertIn("- Export bauen", text)

    def test_kontext_aus_dem_text_wird_nicht_doppelt_angehaengt(self):
        text = "Frage\nAnlass: Der Ordner ist voll\nAuswirkung: Der Export stoppt sonst"
        werte = an.kontext("question", text, None, None)
        self.assertEqual(an.mit_kontext(text, werte).count("Anlass:"), 1)


def _db(aufgaben: dict, teams: dict | None = None):
    from app.models.team import Team

    async def get(modell, pk):
        return (teams or {}).get(pk) if modell is Team else aufgaben.get(pk)

    return MagicMock(get=AsyncMock(side_effect=get))


def _task(tid, titel, eltern=None, agent="a1", meta=None):
    return Task(id=tid, title=titel, prompt="p", agent_id=agent, parent_task_id=eltern, metadata_=meta or {})


class Zielkette(unittest.IsolatedAsyncioTestCase):
    async def test_vom_ausgangsauftrag_bis_zum_eigenen(self):
        aufgaben = {"t1": _task("t1", "Monatsbericht"), "t2": _task("t2", "Zahlen holen", "t1"),
                    "t3": _task("t3", "Export bauen", "t2")}
        kette = await zk.zielkette(_db(aufgaben), aufgaben["t3"])
        self.assertEqual([g["titel"] for g in kette], ["Monatsbericht", "Zahlen holen", "Export bauen"])

    async def test_auftrag_ohne_eltern_hat_keinen_vorspann(self):
        einzeln = _task("t1", "Monatsbericht")
        self.assertEqual(await zk.vorspann(_db({"t1": einzeln}), einzeln), "")

    async def test_schleife_in_den_daten_endet(self):
        a, b = _task("a", "A", "b"), _task("b", "B", "a")
        kette = await zk.zielkette(_db({"a": a, "b": b}), a)
        self.assertEqual(len(kette), 2)

    async def test_geloeschter_eltern_auftrag(self):
        waise = _task("t2", "Zahlen holen", "weg")
        self.assertEqual(len(await zk.zielkette(_db({"t2": waise}), waise)), 1)

    async def test_tiefe_ist_begrenzt(self):
        aufgaben = {f"t{i}": _task(f"t{i}", f"Stufe {i}", f"t{i-1}" if i else None) for i in range(20)}
        self.assertEqual(len(await zk.zielkette(_db(aufgaben), aufgaben["t19"])), zk.MAX_TIEFE)

    async def test_team_zweck_kommt_vom_ausgangsauftrag(self):
        team = SimpleNamespace(name="Buchhaltung", description="Belege vorkontieren und Rückfragen sammeln")
        aufgaben = {"t1": _task("t1", "Monatsabschluss", meta={"team_id": "tm"}), "t2": _task("t2", "Belege", "t1")}
        db = _db(aufgaben, {"tm": team})
        self.assertIn("Buchhaltung", await zk.team_zweck(db, aufgaben["t2"]))
        text = await zk.vorspann(db, aufgaben["t2"])
        self.assertIn("Team: Buchhaltung", text)
        self.assertIn("→ Dein Auftrag: Belege", text)
        self.assertIn("1. Monatsabschluss", text)

    async def test_der_vorspann_ist_kurz(self):
        aufgaben = {f"t{i}": _task(f"t{i}", "x" * 500, f"t{i-1}" if i else None) for i in range(10)}
        self.assertLess(len(await zk.vorspann(_db(aufgaben), aufgaben["t9"])), 1200)

    async def test_ein_fehler_haelt_keinen_auftrag_auf(self):
        kaputt = MagicMock(get=AsyncMock(side_effect=RuntimeError("db weg")))
        self.assertEqual(await zk.vorspann(kaputt, _task("t2", "x", "t1")), "")


class Versand(unittest.IsolatedAsyncioTestCase):
    async def test_der_agent_bekommt_die_zielkette_der_gespeicherte_auftrag_bleibt(self):
        from app.core import task_router
        from app.core.task_router import TaskRouter

        aufgaben = {"t1": _task("t1", "Monatsbericht"), "t2": _task("t2", "Zahlen holen", "t1")}
        aufgaben["t2"].prompt = "Hole die Zahlen."
        router = TaskRouter(_db(aufgaben), MagicMock(), MagicMock())
        with patch.object(task_router, "_build_approval_rules_prefix", AsyncMock(return_value="REGELN\n")):
            prompt = await router._prompt_mit_vorspann(aufgaben["t2"], "a1")
        self.assertTrue(prompt.startswith("REGELN\n"))
        self.assertIn("1. Monatsbericht", prompt)
        self.assertTrue(prompt.endswith("Hole die Zahlen."))
        self.assertEqual(aufgaben["t2"].prompt, "Hole die Zahlen.", "Der gespeicherte Auftrag bleibt, wie er erteilt wurde.")


class NachrichtenEndpunkt(unittest.IsolatedAsyncioTestCase):
    """Der Endpunkt, wie ein Agenten-Token ihn aufruft — bis zur ersten
    Redis-Abfrage; ab da ist es der bisherige Ablauf."""

    async def _senden(self, body, erreichbar=("a1", "a2")):
        from app.api import agents, tasks
        from app.dependencies import AgentPrincipal

        class _Ende(Exception):
            pass

        manager = MagicMock(_get_agent=AsyncMock(side_effect=_Ende()))
        redis = MagicMock()
        redis.client.hgetall = AsyncMock(return_value={})
        with patch.object(tasks, "_erreichbare_agenten", AsyncMock(return_value=set(erreichbar))):
            try:
                await agents.send_message_to_agent(
                    "a2", body, user=AgentPrincipal(id="a1", username="agent-a1"),
                    db=MagicMock(get=AsyncMock(return_value=None)), manager=manager, redis=redis)
            except _Ende:
                return 200
            except HTTPException as e:
                return e.status_code

    async def test_frage_ohne_kontext(self):
        from app.api.agents import AgentMessage
        self.assertEqual(await self._senden(AgentMessage(text="Löschen?", message_type="question")), 422)

    async def test_frage_mit_kontext(self):
        from app.api.agents import AgentMessage
        body = AgentMessage(text="Löschen?", message_type="question",
                            anlass="Der Ordner ist voll", auswirkung="Ohne Löschen stoppt der Export")
        self.assertEqual(await self._senden(body), 200)
        self.assertIn("Anlass: Der Ordner ist voll", body.text)

    async def test_absender_laesst_sich_nicht_faelschen(self):
        from app.api.agents import AgentMessage
        body = AgentMessage(text="Hallo", from_agent_id="jemand-anderes")
        await self._senden(body)
        self.assertEqual(body.from_agent_id, "a1")

    async def test_fremder_agent_ist_kein_kollege(self):
        from app.api.agents import AgentMessage
        self.assertEqual(await self._senden(AgentMessage(text="Hallo"), erreichbar=("a1",)), 403)


if __name__ == "__main__":
    unittest.main()

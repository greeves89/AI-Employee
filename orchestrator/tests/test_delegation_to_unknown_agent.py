"""Delegation an einen Agenten, den es nicht mehr gibt, scheitert LAUT.

Kundenfall vom 2026-08-13: Der Lead schickte drei Auftraege an „CodeReview"
(``6e4210c1``). Diesen Agenten gab es einmal — der Nutzer hatte ihn geloescht.
Die Erinnerung des Agenten war also **korrekt**, nur veraltet; niemand hatte ihm
gesagt, dass sich die Welt geaendert hat.

Was das System daraus machte, war das eigentliche Problem: der Auftrag wurde als
``PENDING`` **ohne ``agent_id``** in die Datenbank gelegt und blieb dort liegen.
Der Reparaturlauf sucht ausdruecklich nur ``PENDING``-Auftraege **mit**
``agent_id`` (``task_router``: ``Task.agent_id.isnot(None)``) — diese fuenf hat
nie wieder jemand angefasst. Der Lead wartete auf ein Ergebnis, das nicht kommen
konnte, und meldete brav „noch offen".

Jetzt fliegt ein Fehler, und zwar einer, der dem AGENTEN hilft: er nennt die
Kollegen, die es wirklich gibt. Damit korrigiert er sich im selben Zug selbst,
statt zu warten.
"""

import types
import unittest
from pathlib import Path

from app.core.task_router import TaskRouter, UnknownAgentError

ROOT = Path(__file__).resolve().parents[2]
ROUTER = (ROOT / "orchestrator/app/core/task_router.py").read_text()


class TheErrorTalksToTheAgentTests(unittest.TestCase):
    """Die Meldung ist eine Werkzeug-Antwort, kein Protokolleintrag."""

    def test_it_names_the_unknown_id(self):
        e = UnknownAgentError("6e4210c1")
        self.assertIn("6e4210c1", str(e))

    def test_it_lists_the_real_colleagues(self):
        e = UnknownAgentError("6e4210c1", [("5ff1d0cd", "DevAgent"), ("7610f79d", "MarketingMaker")])
        self.assertIn("DevAgent (5ff1d0cd)", str(e))
        self.assertIn("MarketingMaker (7610f79d)", str(e))

    def test_it_points_at_list_my_team_when_it_knows_nothing(self):
        self.assertIn("list_my_team", str(UnknownAgentError("6e4210c1")))

    def test_it_says_the_memory_may_have_been_right(self):
        """Der Agent hat nichts falsch gemacht — das gehoert in die Meldung,
        sonst 'lernt' er, seiner Erinnerung generell zu misstrauen."""
        self.assertIn("geloescht", str(UnknownAgentError("x")))

    def test_it_keeps_the_id_for_the_caller(self):
        self.assertEqual(UnknownAgentError("6e4210c1").agent_id, "6e4210c1")


class NoMoreOrphansTests(unittest.TestCase):
    def test_the_router_raises_instead_of_filing_a_pending_task(self):
        block = ROUTER.split("if not await self._agent_exists(agent_id):", 1)[1][:1400]
        self.assertIn("raise UnknownAgentError(", block)
        self.assertNotIn("self.db.add(task)", block)

    def test_it_offers_the_colleagues_of_the_delegating_agent(self):
        block = ROUTER.split("if not await self._agent_exists(agent_id):", 1)[1][:1400]
        self.assertIn("_delegatable_agents(created_by_agent)", block)


class _Ergebnis:
    def __init__(self, zeilen):
        self._zeilen = zeilen

    def scalars(self):
        return self

    def all(self):
        return self._zeilen


class _FakeDB:
    """Gerade genug Datenbank fuer ``_delegatable_agents``: erst Teams, dann Agenten.

    Die Agentenzeilen werden aus den TATSAECHLICH angefragten Kennungen gebaut (aus
    den gebundenen Parametern der Abfrage). Gaebe der Doppelgaenger stattdessen eine
    feste Wunschantwort zurueck, pruefte der Test die Mandantentrennung gar nicht —
    er wuerde sie nur behaupten.
    """

    def __init__(self, teams, namen, fehler=None):
        self.teams, self.namen, self.fehler = teams, namen, fehler
        self.angefragt = None
        self.aufrufe = 0

    async def execute(self, stmt):
        self.aufrufe += 1
        if self.fehler is not None:
            raise self.fehler
        if self.aufrufe == 1:
            return _Ergebnis(self.teams)
        # ``in_()`` bindet die Kennungen als EINE Liste ("expanding parameter").
        roh = stmt.compile().params.values()
        ids = {w for wert in roh for w in (wert if isinstance(wert, list) else [wert])
               if isinstance(w, str)}
        self.angefragt = ids
        return _Ergebnis(sorted((i, self.namen[i]) for i in ids if i in self.namen))


def _team(*mitglieder):
    return types.SimpleNamespace(member_agent_ids=list(mitglieder))


def _router(db):
    r = TaskRouter.__new__(TaskRouter)
    r.db = db
    return r


class TenantIsolationHoldsInErrorsTests(unittest.IsolatedAsyncioTestCase):
    """Die Mandantentrennung gilt auch in einer Fehlermeldung — sonst waere sie
    ein bequemer Weg, sich alle Agenten der Anlage auflisten zu lassen.

    Frueher stand hier viermal ein 1200-Zeichen-Fenster ueber dem Quelltext. Das
    prueft, ob eine Zeile in der Naehe steht — nicht, ob die Trennung haelt.
    """

    NAMEN = {"b": "DevAgent", "c": "Fremd1", "d": "Fremd2"}

    async def test_only_team_mates_are_listed(self):
        db = _FakeDB([_team("a", "b"), _team("c", "d")], self.NAMEN)
        self.assertEqual(await _router(db)._delegatable_agents("a"), [("b", "DevAgent")])
        self.assertEqual(
            db.angefragt, {"b"},
            "Die Abfrage fragt Agenten fremder Teams mit ab — die Fehlermeldung "
            "waere ein Verzeichnis der ganzen Anlage.")

    async def test_the_caller_is_not_listed_as_its_own_colleague(self):
        db = _FakeDB([_team("a", "b")], self.NAMEN)
        got = await _router(db)._delegatable_agents("a")
        self.assertNotIn("a", [kennung for kennung, _ in got])

    async def test_without_a_delegating_agent_nothing_is_revealed(self):
        db = _FakeDB([_team("a", "b")], self.NAMEN)
        self.assertEqual(await _router(db)._delegatable_agents(None), [])
        self.assertEqual(db.aufrufe, 0, "Ohne Auftraggeber darf gar nicht erst gesucht werden.")

    async def test_a_caller_without_a_team_gets_nobody(self):
        db = _FakeDB([_team("c", "d")], self.NAMEN)
        self.assertEqual(await _router(db)._delegatable_agents("a"), [])

    async def test_the_lookup_never_breaks_the_error(self):
        """Eine Fehlermeldung, die selbst scheitert, verschluckt den Befund."""
        db = _FakeDB([], self.NAMEN, fehler=RuntimeError("DB weg"))
        self.assertEqual(await _router(db)._delegatable_agents("a"), [])


class ItReachesTheAgentTests(unittest.TestCase):
    """Ein Fehler, der nur im Protokoll steht, aendert am Verhalten nichts."""

    MAIN = (ROOT / "orchestrator/app/main.py").read_text()

    def test_http_turns_it_into_a_readable_400(self):
        self.assertIn("@app.exception_handler(UnknownAgentError)", self.MAIN)
        block = self.MAIN.split("@app.exception_handler(UnknownAgentError)", 1)[1][:400]
        self.assertIn("status_code=400", block)
        self.assertIn("str(exc)", block)

    def test_it_is_registered_once_and_centrally(self):
        """Statt in jedem der zehn Aufrufer einzeln — genau so entstehen
        Loecher."""
        self.assertEqual(self.MAIN.count("@app.exception_handler(UnknownAgentError)"), 1)


class BackgroundPathsDoNotCrashTests(unittest.TestCase):
    """Im Hintergrund hoert niemand zu — dort darf der Fehler keinen Lauf
    abreissen, muss aber trotzdem sichtbar werden."""

    def test_a_workflow_step_fails_with_the_reason_written_down(self):
        wf = (ROOT / "orchestrator/app/services/workflow_engine.py").read_text()
        block = wf.split("except UnknownAgentError as e:", 1)[1][:600]
        self.assertIn('run.status = "failed"', block)
        self.assertIn("run.error =", block)

    def test_a_resumed_job_is_dropped_instead_of_retried_forever(self):
        main = (ROOT / "orchestrator/app/main.py").read_text()
        block = main.split("except UnknownAgentError as e:", 1)[1][:500]
        self.assertIn("delete_job(db, job.id)", block)


if __name__ == "__main__":
    unittest.main()

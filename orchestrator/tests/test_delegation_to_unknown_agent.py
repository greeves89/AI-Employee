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

import unittest
from pathlib import Path

from app.core.task_router import UnknownAgentError

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


class TenantIsolationHoldsInErrorsTests(unittest.TestCase):
    """Die Mandantentrennung gilt auch in einer Fehlermeldung — sonst waere sie
    ein bequemer Weg, sich alle Agenten der Anlage auflisten zu lassen."""

    def test_only_team_mates_are_listed(self):
        src = ROUTER.split("async def _delegatable_agents", 1)[1][:1200]
        self.assertIn("created_by_agent in mitglieder", src)

    def test_the_caller_is_not_listed_as_its_own_colleague(self):
        src = ROUTER.split("async def _delegatable_agents", 1)[1][:1200]
        self.assertIn("ids.discard(created_by_agent)", src)

    def test_without_a_delegating_agent_nothing_is_revealed(self):
        src = ROUTER.split("async def _delegatable_agents", 1)[1][:1200]
        self.assertIn("if not created_by_agent:", src)

    def test_the_lookup_never_breaks_the_error(self):
        """Eine Fehlermeldung, die selbst scheitert, verschluckt den Befund."""
        src = ROUTER.split("async def _delegatable_agents", 1)[1][:1200]
        self.assertIn("except Exception", src)


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

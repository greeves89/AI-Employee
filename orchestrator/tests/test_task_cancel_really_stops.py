"""„Abbrechen" muss abbrechen — und wenn nicht, muss es das sagen.

Nutzerbericht vom 21.08.2026, mit vollstaendigem Gespraechsprotokoll: der Nutzer
sagte DREIMAL „abbrechen", die Stimme antwortete dreimal „Beide Aufgaben wurden
gestoppt" — und die Aufgabe lief Stunden spaeter immer noch:

    tu7hsco5e | Analyse der Excel-Testrechnung | RUNNING | start 10:18 | ende None

Vier Schichten desselben Problems, alle belegt:

1. Die Sprachfront meldete Erfolg, sobald ein Redis-``publish`` ohne Fehler
   zurueckkam. Ein publish gelingt aber auch, wenn NIEMAND zuhoert.
2. Sie kannte nur ``self._planned`` — Aufgaben aus DIESER Sitzung. Das Gespraech
   war fortgesetzt, die Menge also leer.
3. ``TaskRouter.cancel_task`` wies laufende Aufgaben mit einem Fehler ab.
4. Der Kanal ``agent:{id}:task:cancel`` wurde seit jeher besendet — und hatte
   keinen einzigen Zuhoerer.

Seit #726 werden die Schichten am VERHALTEN geprueft, nicht am Quelltext:
der Router wird mit einer Datenbank-Attrappe aufgerufen, die Sprachfront mit
gestellten Aufgabenlisten; der Zuhoerer im Agenten (Schicht 4) liegt in
``agent/tests/test_task_cancel_listener.py`` und wird dort mit einem
Redis-Doppel gefuettert. Ein festes Zeichenfenster hinter ``async def cancel_task``
haette einen laengeren Kommentar fuer einen Regressionsbruch gehalten — und
einen auskommentierten Aufruf fuer vorhanden.
"""

import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.task_router import TaskRouter
from app.models.task import TaskStatus
from app.services import realtime_voice_session as rvs

WURZEL = Path(__file__).resolve().parents[2]
SEITE = (WURZEL / "frontend/src/app/tasks/page.tsx").read_text()


def _bracket_close(text: str, open_idx: int) -> int:
    """Index, der die bei ``open_idx`` geoeffnete Klammer schliesst.

    Behandelt ``()``/``[]``/``{}`` als EINE Verschachtelungsebene — fuer
    echten, syntaktisch gueltigen Quelltext reicht das. Ein fest gewaehltes
    Zeichenfenster beweist nur NAEHE zu einem Stichwort, nicht Zugehoerigkeit
    zu genau dem Block, den das Stichwort einleitet.
    """
    tiefe = 0
    for i in range(open_idx, len(text)):
        if text[i] in "([{":
            tiefe += 1
        elif text[i] in ")]}":
            tiefe -= 1
            if tiefe == 0:
                return i
    raise ValueError(f"unbalancierte Klammer ab Position {open_idx}")


# ---------------------------------------------------------------------------
# Schicht 3: der Router lehnt laufende Aufgaben nicht mehr ab, sondern ruft.
# ---------------------------------------------------------------------------

def _router_mit(task):
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = task
    db.execute = AsyncMock(return_value=result)
    redis = SimpleNamespace(client=SimpleNamespace(publish=AsyncMock()))
    router = TaskRouter(db=db, redis=redis, load_balancer=MagicMock(), docker_service=None)
    router._remove_from_queue = AsyncMock()
    return router, db, redis.client.publish


def _aufgabe(status, agent_id="agent-7", task_id="tu7hsco5e"):
    return SimpleNamespace(id=task_id, status=status, agent_id=agent_id, completed_at=None)


class ARunningTaskCanBeStoppedTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_router_no_longer_refuses_running_tasks(self):
        task = _aufgabe(TaskStatus.RUNNING)
        router, db, _ = _router_mit(task)
        zurueck = await router.cancel_task(task.id)
        self.assertIs(zurueck, task)
        self.assertEqual(task.status, TaskStatus.CANCELLED)
        self.assertIsNotNone(task.completed_at)
        db.commit.assert_awaited()

    async def test_it_signals_the_agent_for_a_running_task(self):
        """Der Kanal MUSS zum Zuhoerer passen — und die Nutzlast die rohe
        Kennung sein, kein JSON (der Zuhoerer liest sie als ID)."""
        task = _aufgabe(TaskStatus.RUNNING, agent_id="agent-7")
        router, _, publish = _router_mit(task)
        await router.cancel_task(task.id)
        publish.assert_awaited_once_with("agent:agent-7:task:cancel", task.id)

    async def test_a_waiting_task_is_removed_without_a_signal(self):
        """Wer noch nicht laeuft, kann nicht unterbrochen werden — nur aus
        der Schlange genommen."""
        task = _aufgabe(TaskStatus.QUEUED, agent_id="agent-7")
        router, _, publish = _router_mit(task)
        await router.cancel_task(task.id)
        publish.assert_not_awaited()
        router._remove_from_queue.assert_awaited_once_with("agent-7", task.id)
        self.assertEqual(task.status, TaskStatus.CANCELLED)

    async def test_only_what_is_really_over_is_refused(self):
        """Abgelehnt wird nur noch, was wirklich vorbei ist."""
        for status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
            with self.subTest(status=status):
                task = _aufgabe(status)
                router, db, publish = _router_mit(task)
                with self.assertRaises(ValueError):
                    await router.cancel_task(task.id)
                publish.assert_not_awaited()
                db.commit.assert_not_awaited()

    async def test_an_unknown_task_is_none_not_an_error(self):
        router, _, _ = _router_mit(None)
        self.assertIsNone(await router.cancel_task("gibt-es-nicht"))


# Schicht 4 (der Zuhoerer im Agenten) liegt in agent/tests/test_task_cancel_listener.py —
# dort ist `app` das Agentenpaket, und der Zuhoerer laesst sich echt fuettern.

# ---------------------------------------------------------------------------
# Schichten 1+2: die Sprachfront sagt, was der Fall ist — und sieht alles.
# ---------------------------------------------------------------------------

class _Sitzung:
    """Eine Datenbank-Sitzung, die fuer die `_offene()`-Abfrage die gestellte
    Liste liefert und die Abfrage selbst festhaelt."""

    def __init__(self, antworten, gesehen):
        self._antworten = antworten
        self._gesehen = gesehen

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, stmt):
        self._gesehen.append(stmt)
        return list(self._antworten.pop(0))


class TheVoiceTellsTheTruthTests(unittest.IsolatedAsyncioTestCase):
    def _front(self, vorher, nachher):
        sitzung = rvs.RealtimeVoiceSession.__new__(rvs.RealtimeVoiceSession)
        sitzung.agent_id = "agent-7"
        sitzung.redis = SimpleNamespace(client=SimpleNamespace(publish=AsyncMock()))
        sitzung._planned = {tid: object() for tid, _ in vorher}
        antworten = [vorher, nachher]
        abfragen: list = []
        abgebrochen: list = []

        class _Router:
            def __init__(self, *a, **kw):
                pass

            async def cancel_task(self, tid):
                abgebrochen.append(tid)

        patches = [
            patch("app.db.session.async_session_factory",
                  side_effect=lambda: _Sitzung(antworten, abfragen)),
            patch("app.core.task_router.TaskRouter", _Router),
            patch("app.core.load_balancer.LoadBalancer", MagicMock()),
            patch.object(rvs.asyncio, "sleep", AsyncMock()),
        ]
        return sitzung, patches, abfragen, abgebrochen

    async def _sagen(self, vorher, nachher):
        sitzung, patches, abfragen, abgebrochen = self._front(vorher, nachher)
        for p in patches:
            p.start()
        try:
            antwort = await sitzung._cancel_task()
        finally:
            for p in patches:
                p.stop()
        return antwort, abfragen, abgebrochen, sitzung

    async def test_it_no_longer_reports_success_from_a_bare_publish(self):
        """Das war die Luege: `publish` gelingt auch ohne Zuhoerer. Ueberlebt
        eine Aufgabe den Abbruch, darf die Antwort keinen Erfolg melden."""
        antwort, _, _, _ = await self._sagen([("t1", "Excel-Analyse")], [("t1", "Excel-Analyse")])
        self.assertNotIn("Es läuft nichts mehr", antwort)
        self.assertIn("läuft/laufen noch", antwort)

    async def test_it_looks_at_all_open_tasks_not_only_this_session(self):
        """Die Menge kommt aus der Datenbank, gefiltert auf DIESEN Agenten und
        die offenen Zustaende — nicht aus `self._planned`."""
        sitzung, patches, abfragen, _ = self._front([("t-fremd", "aus anderer Sitzung")], [])
        sitzung._planned = {}                     # fortgesetztes Gespraech: leer
        for p in patches:
            p.start()
        try:
            antwort = await sitzung._cancel_task()
        finally:
            for p in patches:
                p.stop()
        self.assertIn("1 Aufgabe(n) gestoppt", antwort)
        params = abfragen[0].compile().params
        self.assertEqual(params.get("agent_id_1"), "agent-7")
        offen = {s for s in params.get("status_1", [])}
        self.assertEqual(offen, {TaskStatus.QUEUED, TaskStatus.PENDING, TaskStatus.RUNNING})

    async def test_it_checks_again_afterwards(self):
        """Der eigentliche Fix: nachsehen statt behaupten — die Abfrage laeuft
        VOR und NACH dem Abbruch."""
        _, abfragen, abgebrochen, _ = await self._sagen([("t1", "A"), ("t2", "B")], [])
        self.assertEqual(len(abfragen), 2)
        self.assertEqual(abgebrochen, ["t1", "t2"])

    async def test_it_says_so_when_something_survived(self):
        antwort, _, _, _ = await self._sagen([("t1", "A"), ("t2", "B")], [("t2", "B")])
        self.assertIn("1 Aufgabe(n) gestoppt", antwort)
        self.assertIn("1 läuft/laufen noch", antwort)

    async def test_it_names_what_is_still_running(self):
        """„Etwas laeuft noch" ohne Namen zwingt zur naechsten Rueckfrage."""
        antwort, _, _, _ = await self._sagen(
            [("t1", "Analyse der Excel-Testrechnung")], [("t1", "Analyse der Excel-Testrechnung")])
        self.assertIn("Analyse der Excel-Testrechnung", antwort)

    async def test_when_everything_stopped_it_says_that_too(self):
        antwort, _, _, sitzung = await self._sagen([("t1", "A")], [])
        self.assertEqual(antwort, "Ich habe 1 Aufgabe(n) gestoppt. Es läuft nichts mehr.")
        self.assertNotIn("t1", sitzung._planned)

    async def test_nothing_open_is_not_an_error(self):
        antwort, _, abgebrochen, _ = await self._sagen([], [])
        self.assertIn("nichts", antwort)
        self.assertEqual(abgebrochen, [])


# ---------------------------------------------------------------------------
# Oberflaeche: ein Knopf, der laufende Aufgaben stoppt.
# ---------------------------------------------------------------------------

class TheUiHasAManualStopTests(unittest.TestCase):
    """Ausdruecklicher Wunsch: „ich will bei aufgaben auch noch einen Manuellen
    stop haben"."""

    def test_a_running_task_can_be_stopped_from_the_list(self):
        self.assertIn('const laeuft = task.status === "running"', SEITE)
        self.assertIn("const canCancel = laeuft ||", SEITE)

    def test_the_stop_button_is_visible_without_hovering(self):
        """Wer eine laufende Aufgabe stoppen will, sucht den Knopf sofort —
        nicht erst, wenn er zufaellig darueberfaehrt. Geprueft wird der
        JSX-Block hinter `canCancel && (` bis zu SEINER schliessenden Klammer,
        nicht 1400 Zeichen dahinter."""
        auf = SEITE.index("{canCancel && (") + len("{canCancel && ")
        block = SEITE[auf:_bracket_close(SEITE, auf) + 1]
        # Der Verstecken-Stil gilt nur noch fuer wartende Aufgaben: im
        # Dann-Zweig des `laeuft ? ... : ...` darf er nicht stehen, im
        # Sonst-Zweig muss er. („Ein ? steht davor" waere in JEDEM Ternaer
        # wahr — genau so eine Zusicherung hat die Gegenprobe still gelassen.)
        ternaer = block.index("laeuft")
        dann_ab = block.index("?", ternaer)
        # Der Ternaer-Doppelpunkt beginnt eine Zeile — Tailwind-Klassen wie
        # `hover:bg-...` tragen selbst Doppelpunkte, die zaehlen nicht.
        sonst_ab = re.compile(r"\n\s*:").search(block, dann_ab).start()
        sonst_bis = block.index("\n", sonst_ab + 1)
        dann, sonst = block[dann_ab:sonst_ab], block[sonst_ab:sonst_bis]
        self.assertNotIn("opacity-0 group-hover:opacity-100", dann)
        self.assertIn("opacity-0 group-hover:opacity-100", sonst)

    def test_the_words_distinguish_the_two_cases(self):
        """Eine wartende Aufgabe nimmt man aus der Schlange, eine laufende
        unterbricht man — das sind zwei verschiedene Dinge."""
        self.assertIn('"Stoppen"', SEITE)
        self.assertIn('"Abbrechen"', SEITE)


if __name__ == "__main__":
    unittest.main()

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

import ast
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.task_router import TaskRouter, UnknownAgentError

ROOT = Path(__file__).resolve().parents[2]


def _ohne_kommentare(block: str) -> str:
    """Kommentare aus einem Quelltextblock tilgen (per tokenize, nicht per
    '#'-Suche). Ein auskommentierter Aufruf stuende sonst weiterhin im Block
    und bestuende jedes `assertIn` — die Blindstelle aus #726."""
    import io
    import textwrap
    import tokenize

    text = textwrap.dedent(block)
    zeilen = text.splitlines(keepends=True)
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT:
                (zeile, von), (_, bis) = tok.start, tok.end
                zeilen[zeile - 1] = zeilen[zeile - 1][:von] + zeilen[zeile - 1][bis:]
    except tokenize.TokenError as e:  # unvollstaendiger Block — lieber laut
        raise AssertionError(f"Block nicht tokenisierbar: {e}")
    return "".join(zeilen)


def _knotenquelle(src: str, knoten: ast.AST) -> str:
    """``ast.get_source_segment`` mit korrigierter erster Zeile.

    Die erste Zeile kommt OHNE ihre urspruengliche Einrueckung zurueck (sie
    schneidet ab ``col_offset``), alle folgenden MIT voller Original-
    Einrueckung. Bei einer Geschwister-Klausel auf derselben Spalte (``except``
    zu ``try``) springt eine Zeile dann scheinbar auf Spalte 0 zurueck, ohne
    dass diese Ebene je geoeffnet wurde — ``textwrap.dedent`` findet keinen
    gemeinsamen Praefix mehr. Die fehlende Einrueckung der ersten Zeile hier
    wieder auffuellen, bevor gekuerzt wird."""
    text = ast.get_source_segment(src, knoten) or ""
    return (" " * knoten.col_offset) + text


def _except_block(src: str, funktion: str, ausnahme: str) -> str:
    """Der ``except <ausnahme>``-Block INNERHALB von ``def <funktion>`` — als
    kleinster umschliessender AST-Knoten, nicht als geschaetzte Zeichenzahl.

    ``_resume_agent_task`` sitzt als verschachtelte Funktion tief im
    Start-Vorgang (``lifespan``) und laesst sich nicht isoliert mit Attrappen
    aufrufen, ohne den ganzen Start nachzubauen. Die syntaktische Blockgrenze
    ist trotzdem die tatsaechliche Codegrenze: ein laengerer Kommentar
    daneben verschiebt sie nicht, und Kommentare werden vor der Pruefung
    getilgt — ein auskommentiertes ``delete_job`` faellt hier durch, anders
    als bei einem Zeichenfenster.
    """
    baum = ast.parse(src)
    for fn in ast.walk(baum):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.name == funktion:
            for knoten in ast.walk(fn):
                if (isinstance(knoten, ast.ExceptHandler) and knoten.type is not None
                        and ast.get_source_segment(src, knoten.type) == ausnahme):
                    return _ohne_kommentare(_knotenquelle(src, knoten))
            raise AssertionError(f"except {ausnahme}: nicht in {funktion} gefunden")
    raise AssertionError(f"def {funktion}: nicht gefunden")


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

    def __init__(self, teams, namen, fehler=None, agent_da=False):
        self.teams, self.namen, self.fehler = teams, namen, fehler
        self.agent_da = agent_da
        self.angefragt = None
        self.aufrufe = 0
        self.hinzugefuegt = []

    async def scalar(self, stmt):
        """``_agent_exists``: None heisst 'den Agenten gibt es nicht mehr'."""
        return "da" if self.agent_da else None

    def add(self, obj):
        self.hinzugefuegt.append(obj)

    async def commit(self):
        pass

    async def refresh(self, obj):
        pass

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


class NoMoreOrphansTests(unittest.IsolatedAsyncioTestCase):
    """Der eigentliche Kundenfall, am Verhalten gefahren.

    Frueher stand hier zweimal ein 1400-Zeichen-Fenster ueber dem Quelltext. Das
    beweist, dass eine Zeile in der Naehe STEHT — nicht, dass sie LAEUFT. Genau
    diese Luecke: ein auskommentiertes ``raise`` haette das Fenster bestanden.
    """

    NAMEN = {"b": "DevAgent", "c": "Fremd1", "d": "Fremd2"}

    async def _route(self, db, created_by_agent="a"):
        r = _router(db)
        with patch.object(TaskRouter, "_check_platform_budget", new=AsyncMock()):
            return await r.create_and_route_task(
                title="Bitte pruefen", prompt="…",
                agent_id="6e4210c1", created_by_agent=created_by_agent,
            )

    async def test_it_raises_instead_of_filing_a_pending_task(self):
        db = _FakeDB([_team("a", "b")], self.NAMEN)
        with self.assertRaises(UnknownAgentError):
            await self._route(db)
        self.assertEqual(
            db.hinzugefuegt, [],
            "Der Auftrag wurde trotzdem in die Datenbank gelegt — als Waise, die "
            "der Reparaturlauf (nur PENDING MIT agent_id) nie wieder anfasst.")

    async def test_the_error_offers_the_colleagues_of_the_delegating_agent(self):
        db = _FakeDB([_team("a", "b"), _team("c", "d")], self.NAMEN)
        with self.assertRaises(UnknownAgentError) as gefangen:
            await self._route(db)
        self.assertIn("DevAgent (b)", str(gefangen.exception))
        self.assertNotIn("Fremd", str(gefangen.exception))

    async def test_a_known_agent_gets_past_the_guard(self):
        """Gegenstueck: die Sperre darf den Normalfall nicht treffen.

        Statt den ganzen Zustellweg nachzubauen, wird der naechste Schritt nach der
        Sperre angehalten — kommt DIESE Marke an, war die Sperre offen.
        """
        class Marke(Exception):
            pass

        db = _FakeDB([_team("a", "b")], self.NAMEN, agent_da=True)
        with patch.object(TaskRouter, "_route_model_by_content",
                          new=AsyncMock(side_effect=Marke)):
            with self.assertRaises(Marke):
                await self._route(db)


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
        # Der Doppelgaenger MUSS den Auftraggeber liefern koennen. Mit der Namensliste
        # ohne "a" bestand dieser Test auch dann, wenn die Produktion das discard gar
        # nicht macht — die Gegenprobe vom 13.09. hat genau das aufgedeckt.
        db = _FakeDB([_team("a", "b")], {**self.NAMEN, "a": "Ich selbst"})
        got = await _router(db)._delegatable_agents("a")
        self.assertNotIn("a", [kennung for kennung, _ in got])
        self.assertNotIn("a", db.angefragt, "Der Auftraggeber wird erst gar nicht abgefragt.")

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


class ItReachesTheAgentTests(unittest.IsolatedAsyncioTestCase):
    """Ein Fehler, der nur im Protokoll steht, aendert am Verhalten nichts."""

    MAIN = (ROOT / "orchestrator/app/main.py").read_text()

    async def test_http_turns_it_into_a_readable_400(self):
        """Den registrierten Handler wirklich aufrufen statt Text daneben zu
        lesen — ein auskommentierter ``status_code=400`` bestuende ein
        Zeichenfenster klaglos, hier faellt die Antwort dann auf 500 zurueck."""
        import json

        from app.main import _unknown_agent_handler

        fehler = UnknownAgentError("6e4210c1")
        antwort = await _unknown_agent_handler(None, fehler)
        self.assertEqual(antwort.status_code, 400)
        self.assertEqual(json.loads(antwort.body)["detail"], str(fehler))

    def test_it_is_registered_once_and_centrally(self):
        """Statt in jedem der zehn Aufrufer einzeln — genau so entstehen
        Loecher."""
        self.assertEqual(self.MAIN.count("@app.exception_handler(UnknownAgentError)"), 1)


class BackgroundPathsDoNotCrashTests(unittest.IsolatedAsyncioTestCase):
    """Im Hintergrund hoert niemand zu — dort darf der Fehler keinen Lauf
    abreissen, muss aber trotzdem sichtbar werden."""

    async def test_a_workflow_step_fails_with_the_reason_written_down(self):
        """``advance_run`` wirklich fahren statt den Quelltext daneben zu lesen:
        ein auskommentiertes ``run.status = "failed"`` bestuende ein
        1400-Zeichen-Fenster klaglos, hier bliebe der Run dann faelschlich auf
        'running' stehen."""
        from app.models.workflow import Workflow, WorkflowRun
        from app.services import workflow_engine as we

        wf = Workflow(id="wf1", name="t")
        wf.definition = {"start": "s1", "steps": {
            "s1": {"type": "agent_task", "prompt": "tu was",
                   "agent_id": "6e4210c1", "next": None},
        }}
        run = WorkflowRun(id="r1", workflow_id="wf1")
        run.status, run.context, run.current_step = "running", {}, "s1"
        run.current_task_id, run.resume_at, run.steps_done = None, None, 0

        db = MagicMock()
        db.commit = AsyncMock()
        router = MagicMock()
        router.create_and_route_task = AsyncMock(side_effect=UnknownAgentError("6e4210c1"))

        await we.advance_run(run, wf, db, router)

        self.assertEqual(run.status, "failed")
        self.assertIn("6e4210c1", run.error or "")
        db.commit.assert_awaited()

    def test_a_resumed_job_is_dropped_instead_of_retried_forever(self):
        """``_resume_agent_task`` steckt in ``lifespan`` und laesst sich ohne
        den ganzen Start nicht mit Attrappen aufrufen — deshalb der echte
        syntaktische except-Block (siehe ``_except_block``) statt eines
        geschaetzten Zeichenfensters."""
        main = (ROOT / "orchestrator/app/main.py").read_text()
        block = _except_block(main, "_resume_agent_task", "UnknownAgentError")
        self.assertIn("delete_job(db, job.id)", block)


if __name__ == "__main__":
    unittest.main()

"""Der Aufgabentext des Nutzers gehört nicht in eine URL.

Befund einer automatischen Sicherheitsprüfung an #562: die aufgabenbezogene
Vorauswahl der Erinnerungen hängte den Aufgabentext (bis 500 Zeichen echter
Nutzereingabe) als Abfrageparameter an die URL:

    GET /api/v1/memory/preload/{id}?task_context=Bitte+pruefe+die+Abrechnung+von+…

uvicorn schreibt **jeden Pfad samt Abfrage** ins Zugriffsprotokoll. Damit landen
Bruchstücke echter Aufgaben in jedem Log, das jemand einsammelt, rotiert oder an
eine Auswertung weiterreicht — gegen die eigene Regel, niemals PII zu loggen.
Dass der Endpunkt nur intern erreichbar ist, mindert das, hebt es aber nicht auf.

Deshalb: der Text geht per ``POST`` in den **Rumpf**. Der steht in keinem
Zugriffsprotokoll. ``GET`` bleibt bestehen — aber ohne diesen Parameter, damit er
auf diesem Weg gar nicht erst ankommen kann.
"""

import inspect
import unittest


class NoTaskContextInTheQueryStringTests(unittest.TestCase):
    def test_get_does_not_accept_the_task_context_at_all(self):
        """Nicht bloss „wir benutzen es nicht mehr" — es darf nicht annehmbar sein.

        Solange ``GET`` den Parameter kennt, kann ihn irgendein Aufrufer wieder
        anhängen, und er steht wieder im Log.
        """
        from app.api.memory import preload_critical_memories

        params = inspect.signature(preload_critical_memories).parameters
        self.assertNotIn("task_context", params)
        self.assertNotIn("room", params)

    def test_post_takes_it_in_the_body(self):
        from app.api.memory import PreloadRequest, preload_critical_memories_for_task

        params = inspect.signature(preload_critical_memories_for_task).parameters
        self.assertIn("body", params)
        self.assertIs(params["body"].annotation, PreloadRequest)
        self.assertIn("task_context", PreloadRequest.model_fields)
        self.assertIn("room", PreloadRequest.model_fields)

    def test_the_length_cap_survived_the_move(self):
        """500 Zeichen war schon vorher die Grenze — ein Rumpf ist kein Freibrief."""
        from app.api.memory import PreloadRequest
        from pydantic import ValidationError

        PreloadRequest(task_context="x" * 500)
        with self.assertRaises(ValidationError):
            PreloadRequest(task_context="x" * 501)

    def test_both_routes_exist(self):
        from app.api.memory import router

        preload = [r for r in router.routes if str(getattr(r, "path", "")).endswith("/preload/{agent_id}")]
        methods = set()
        for r in preload:
            methods |= set(getattr(r, "methods", set()))
        self.assertIn("GET", methods, "Alte Agenten rufen weiterhin GET")
        self.assertIn("POST", methods)

    def test_both_routes_share_the_same_ownership_check(self):
        """Ein zweiter Weg zu denselben Daten ist ein zweiter Weg, die Prüfung zu
        vergessen. Beide gehen deshalb durch dieselbe Funktion."""
        from app.api import memory

        for fn in (memory.preload_critical_memories,
                   memory.preload_critical_memories_for_task):
            with self.subTest(fn.__name__):
                self.assertIn("_preload(", inspect.getsource(fn))

    def test_the_agent_no_longer_builds_a_query_string_for_it(self):
        from pathlib import Path

        src = (Path(__file__).resolve().parents[2]
               / "agent/app/runner_hooks.py").read_text()
        block = src.split("def get_memory_preload")[1].split("\ndef ")[0]
        self.assertNotIn("urlencode", block,
                         "Der Aufgabentext darf nicht mehr in die URL wandern")
        self.assertIn('method="POST"', block)

    def test_the_agent_falls_back_instead_of_losing_everything(self):
        """Trifft ein neuer Agent auf einen älteren Orchestrator, kennt der POST
        hier noch nicht. Dann lieber die Grundauswahl ohne Aufgabenbezug als gar
        keine Erinnerungen — die Funktion schluckt Fehler still."""
        from pathlib import Path

        src = (Path(__file__).resolve().parents[2]
               / "agent/app/runner_hooks.py").read_text()
        block = src.split("def get_memory_preload")[1].split("\ndef ")[0]
        self.assertIn("HTTPError", block)
        self.assertIn("405", block)


if __name__ == "__main__":
    unittest.main()

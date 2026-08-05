"""Der Nachrichten-Pfad muss die Namen kennen, die er benutzt.

Vorfall 2026-08-05: Nach v1.140.0 stand im Agenten-Log bei JEDER Kollegen-Nachricht
`Consumer error: name 'ProcessIdleTimeout' is not defined`. Der Wachhund war in
`message_consumer` eingebaut, aber weder er noch seine Ausnahme importiert. Der
Aufruf warf NameError, und beim Auswerten der except-Klausel warf der zweite
fehlende Name gleich hinterher — der Agent-zu-Agent-Pfad war komplett tot.

Die Folge reichte bis in den Chat: Ein Agent fragte einen Kollegen, bekam nie eine
Antwort, verstummte 600 Sekunden lang und wurde vom Stillstands-Wachhund
abgebrochen. Der Wachhund hatte recht — die Ursache lag im fehlenden Import.

Die Tests von v1.140.0 pruefen `proc_watchdog.py` gegen echte Unterprozesse. Nur
den AUFRUFER hat niemand angefasst. Genau diese Luecke schliesst diese Datei.
"""

import ast
import inspect
import pathlib
import unittest


AGENT_APP = pathlib.Path(__file__).resolve().parents[1] / "app"


def _undefined_names(path: pathlib.Path) -> set[str]:
    """Namen, die ein Modul benutzt, aber nirgends bindet.

    Bewusst grob: gebunden zaehlt alles aus Importen (auch funktionslokalen),
    Zuweisungen, def/class, Parametern, Schleifen und with/except-Zielen. Uebrig
    bleiben echte freie Namen — abzueglich der Builtins.
    """
    tree = ast.parse(path.read_text())
    bound: set[str] = set(dir(__builtins__) if isinstance(__builtins__, dict) is False else __builtins__)
    used: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                bound.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                bound.add(a.asname or a.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, ast.Name):
            (bound if isinstance(node.ctx, ast.Store) else used).add(node.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, ast.alias):
            bound.add(node.asname or node.name.split(".")[0])
        elif isinstance(node, ast.Global):
            bound.update(node.names)

    import builtins
    bound.update(dir(builtins))
    return used - bound


class MessageConsumerImportTests(unittest.TestCase):
    def test_no_undefined_names(self):
        """Kein benutzter Name ohne Bindung — das war der ganze Fehler."""
        missing = _undefined_names(AGENT_APP / "message_consumer.py")
        self.assertEqual(missing, set(), f"Unbekannte Namen im Nachrichten-Pfad: {missing}")

    def test_watchdog_is_actually_importable(self):
        """Nicht nur syntaktisch da — der Import muss auch wirklich aufloesen."""
        from app.message_consumer import ProcessIdleTimeout, communicate_with_idle_timeout
        self.assertTrue(issubclass(ProcessIdleTimeout, Exception))
        self.assertTrue(inspect.iscoroutinefunction(communicate_with_idle_timeout))

    def test_the_watchdog_is_the_shared_one(self):
        """Keine zweite Kopie der Regel — sie lebt in proc_watchdog."""
        from app.message_consumer import ProcessIdleTimeout
        from app.proc_watchdog import ProcessIdleTimeout as Shared
        self.assertIs(ProcessIdleTimeout, Shared)


class OtherConsumerImportTests(unittest.TestCase):
    """Dieselbe Pruefung fuer die Nachbarn — der Fehler war nicht modulspezifisch."""

    def test_chat_consumer(self):
        missing = _undefined_names(AGENT_APP / "chat_consumer.py")
        self.assertEqual(missing, set(), f"Unbekannte Namen im Chat-Pfad: {missing}")

    def test_task_consumer(self):
        missing = _undefined_names(AGENT_APP / "task_consumer.py")
        self.assertEqual(missing, set(), f"Unbekannte Namen im Aufgaben-Pfad: {missing}")


if __name__ == "__main__":
    unittest.main()

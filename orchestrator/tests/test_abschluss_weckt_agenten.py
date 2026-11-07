"""Der Abschluss-Lauscher hat Docker — sonst weckt er niemanden.

Ein Auftragsabschluss legt Folge-Läufe an oder meldet Ergebnisse weiter: den blinden
Prüfer (core/pruefung), Teilaufgaben an den Eltern-Agenten, Delegationen an den
Auftraggeber, Wiederholungen der Selbstheilung. Alle diese Wege wecken einen gestoppten
Agenten — aber nur, wenn der ``TaskRouter`` Docker hat. Der Lauscher in ``main.py`` baute
ihn bis 1.437.0 ohne: Live gesehen blieb der Prüflauf 8 Minuten in der Warteschlange
eines gestoppten Agenten liegen.
"""
import ast
import unittest
from pathlib import Path

QUELLE = Path(__file__).resolve().parents[1] / "app" / "main.py"


class LauscherHatDocker(unittest.TestCase):
    def setUp(self):
        self.baum = ast.parse(QUELLE.read_text())

    def test_router_im_lauscher_bekommt_docker(self):
        fn = next(n for n in ast.walk(self.baum)
                  if isinstance(n, ast.AsyncFunctionDef) and n.name == "_listen_task_events")
        aufrufe = [n for n in ast.walk(fn) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "TaskRouter"]
        self.assertTrue(aufrufe)
        for a in aufrufe:
            self.assertIn("docker_service", [k.arg for k in a.keywords], "TaskRouter ohne Docker im Lauscher")
        self.assertIn("docker", [p.arg for p in fn.args.args])

    def test_lauscher_wird_mit_docker_gestartet(self):
        starts = [n for n in ast.walk(self.baum) if isinstance(n, ast.Call)
                  and getattr(n.func, "id", "") == "_listen_task_events"]
        self.assertTrue(starts)
        for s in starts:
            self.assertEqual(len(s.args) + len(s.keywords), 2, "Lauscher ohne Docker gestartet")
            self.assertIn("docker", ast.dump(s.args[-1] if s.args else s.keywords[-1].value))


if __name__ == "__main__":
    unittest.main()

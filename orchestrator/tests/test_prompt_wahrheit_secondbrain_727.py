"""Was der Agent ueber den Second Brain gesagt bekommt, muss wahr sein.

Drei Befunde aus #727, alle im Prompt-Text und alle folgenlos fuer jeden Build:

1. ``DEFAULT_CLAUDE_MD`` behauptete, die Wissensdatenbank sei "SHARED ACROSS ALL
   AGENTS". Sie ist auf den Nutzer begrenzt. Ein Agent, der das glaubt, erklaert
   dem Nutzer Wissen als geteilt, das es nicht ist.
2. Mehrere Stellen schickten den Agenten ausdruecklich zu ``grep``, obwohl die
   Hybrid-Suche laengst lief. Ein Prompt, der das billigere Werkzeug empfiehlt,
   macht den teuren Teil der Anlage wirkungslos — das ist der Kern dieses Issues.
3. Chat- und Proaktiv-Pfad kannten den Second Brain gar nicht.

Geprueft wird Verhalten im Sinne von „welcher Text geht wirklich raus", nicht
Formulierung: Jede Zusicherung ist eine Mengenaussage ueber den ausgelieferten
Prompt-Text, keine Suche nach einem bestimmten Satz.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

QUELLEN = {
    "agent_manager": ROOT / "orchestrator/app/core/agent_manager.py",
    "main": ROOT / "orchestrator/app/main.py",
    "runner_hooks": ROOT / "agent/app/runner_hooks.py",
}


def _text(name: str) -> str:
    return QUELLEN[name].read_text()


class PromptWahrheitTests(unittest.TestCase):

    def test_vorbedingung_alle_quellen_sind_lesbar(self):
        """Ohne das waere jede Abwesenheits-Zusicherung unten gruen, weil die
        Datei leer ist — nicht, weil der Satz weg ist."""
        for name, p in QUELLEN.items():
            with self.subTest(name):
                self.assertTrue(p.exists(), f"{p} fehlt")
                self.assertGreater(len(_text(name)), 5000)

    def test_die_falsche_behauptung_geteilt_ist_weg(self):
        for name in QUELLEN:
            with self.subTest(name):
                self.assertNotIn("SHARED ACROSS ALL AGENTS", _text(name))

    def test_jede_second_brain_stelle_nennt_das_suchwerkzeug(self):
        """Wo der Vault erklaert wird, steht der Weg dorthin — in allen drei
        Prompt-Quellen, also auch im Chat- und Proaktiv-Pfad."""
        for name in QUELLEN:
            with self.subTest(name):
                t = _text(name)
                self.assertIn("/mnt/brains", t)
                self.assertIn("secondbrain_search", t)

    def test_grep_steht_nur_noch_als_rueckfall_am_vault(self):
        """Die eigentliche Zusicherung: in jedem Absatz, der ``grep`` UND den
        Vault nennt, ist ``grep`` als Notnagel gekennzeichnet.

        Absatzweise statt dateiweise, weil dieselben Dateien ``grep`` an anderer
        Stelle voellig zurecht empfehlen (etwa fuer das Fehlerprotokoll).
        """
        marker = ("fall back", "fallback", "Rueckfall", "Rückfall", "notnagel",
                  "Notnagel", "emergency", "unreachable", "nicht erreichbar",
                  "instead of", "statt", "not grep", "nicht grep")
        for name in QUELLEN:
            for absatz in re.split(r"\n\s*\n", _text(name)):
                if "grep" not in absatz or "/mnt/brains" not in absatz:
                    continue
                with self.subTest(name=name, absatz=absatz[:70]):
                    self.assertTrue(
                        any(m in absatz for m in marker),
                        "grep steht hier ohne Kennzeichnung als Rueckfall:\n" + absatz[:400],
                    )

    def test_der_schreibweg_nennt_seinen_grund(self):
        """``secondbrain_write`` statt Dateischreiben ist keine Geschmacksfrage:
        nur dieser Weg indexiert sofort. Steht der Grund nicht dabei, nimmt das
        Modell weiter das naechstliegende Datei-Werkzeug.

        Geprueft wird je ABSATZ, nicht ueber ein Zeichenfenster hinter dem
        Vorkommen: ein Fenster misst den Abstand zweier Zeilen, gemeint ist ihr
        Zusammenhang — und es wird stillschweigend falsch, sobald jemand einen
        Satz dazwischen schreibt.
        """
        for name in QUELLEN:
            absaetze = [a for a in re.split(r"\n\s*\n", _text(name))
                        if "secondbrain_write" in a]
            if not absaetze:
                continue
            with self.subTest(name):
                self.assertTrue(
                    any("index" in a.lower() for a in absaetze),
                    f"{name}: secondbrain_write wird empfohlen, ohne dass irgendein "
                    f"Absatz den Grund nennt (sofortiges Indexieren) — "
                    f"{len(absaetze)} Absatz/Absaetze geprueft",
                )


if __name__ == "__main__":
    unittest.main()

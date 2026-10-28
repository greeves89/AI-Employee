"""Der Browser-Schalter muss auch bei Neustart und Aktualisierung greifen.

Die Oberflaeche sagt beim Umlegen des Schalters: "Restart the agent for the
change to take effect." Genau das stimmte nicht. ``COMPUTER_USE_BROWSER``
stand nur im Erstellungsweg; wer den Schalter umlegte und den Agenten neu
startete oder aktualisierte, bekam einen Container ohne Browser -- ohne
Fehlermeldung, nur ohne Wirkung.

Aufgefallen beim Bau der Browser-Arbeitsflaeche (#828): Der Agent war auf
``browser_mode = True`` gesetzt und neu erstellt, im Container stand die
Variable trotzdem leer, und die Strom-Route fehlte entsprechend -- der
Orchestrator bekam ein 404 vom Agenten.

Geprueft wird die Quelle statt eines gebauten Containers: Ein echter Lauf
braucht Docker, ein Abbild und ein paar Minuten; die Regel dagegen ist
einfach und soll auch dann noch gelten, wenn jemand einen vierten Bauweg
ergaenzt.
"""

import ast
import unittest
from pathlib import Path

_QUELLE = Path(__file__).resolve().parents[1] / "app" / "core" / "agent_manager.py"

#: Jede Methode, die einen Agenten-Container baut.
BAUWEGE = ("create_agent", "restart_agent", "update_agent")


def _quelltext(name: str) -> str:
    baum = ast.parse(_QUELLE.read_text(encoding="utf-8"))
    text = _QUELLE.read_text(encoding="utf-8")
    for knoten in ast.walk(baum):
        if isinstance(knoten, (ast.AsyncFunctionDef, ast.FunctionDef)) and knoten.name == name:
            return ast.get_source_segment(text, knoten) or ""
    raise AssertionError(f"{name} nicht in agent_manager.py gefunden")


class BrowserSchalterWirktUeberall(unittest.TestCase):

    def test_jeder_bauweg_setzt_den_schalter(self):
        for weg in BAUWEGE:
            with self.subTest(bauweg=weg):
                self.assertIn(
                    "COMPUTER_USE_BROWSER", _quelltext(weg),
                    f"{weg}() baut einen Container, setzt den Browser-Schalter aber "
                    f"nicht — der Agent laeuft dann ohne Browser, obwohl er "
                    f"eingeschaltet ist.",
                )

    def test_neustart_und_aktualisierung_lesen_den_gespeicherten_wert(self):
        """Nicht ein Argument, sondern den Stand am Agenten.

        ``create_agent`` bekommt ``browser_mode`` uebergeben; die beiden
        anderen Wege haben kein solches Argument und muessen deshalb
        ``agent.browser_mode`` lesen. Eine Kopie des Arguments waere dort
        stillschweigend immer ``False``.
        """
        for weg in ("restart_agent", "update_agent"):
            with self.subTest(bauweg=weg):
                self.assertIn("agent.browser_mode", _quelltext(weg))

    def test_jeder_bauweg_setzt_die_freigabeliste(self):
        """Dieselbe Klasse, diesmal sicherheitsrelevant (#828, Schritt 5).

        Fehlt ``BROWSER_ALLOWED_DOMAINS`` in einem Bauweg, startet der
        Container dort OHNE die Variable -- und "nicht gesetzt" heisst im
        Agenten bewusst "unbeschraenkt" (Rueckwaertskompatibilitaet zum
        Prototyp). Der Besitzer haette also eine Freigabeliste gesetzt, der
        Agent wuerde sie ignorieren, und auffallen wuerde es nirgends: alles
        funktioniert weiter, nur die Schranke fehlt. Genau so war es beim
        Schalter selbst, nur dass es damals nur Bequemlichkeit kostete.
        """
        for weg in BAUWEGE:
            with self.subTest(bauweg=weg):
                self.assertIn(
                    "BROWSER_ALLOWED_DOMAINS", _quelltext(weg),
                    f"{weg}() baut einen Container, gibt die Domain-Freigabeliste "
                    f"aber nicht mit — der Agenten-Browser duerfte dort ueberall "
                    f"hin, obwohl der Besitzer das eingeschraenkt hat.",
                )

    def test_neustart_und_aktualisierung_lesen_die_gespeicherte_freigabeliste(self):
        """Nicht eine leere Vorgabe, sondern den Stand am Agenten.

        ``create_agent`` MUSS hier leer bleiben: an dieser Stelle gibt es noch
        kein ``config`` (dasselbe Stolperstein, das im Quelltext an der
        Modell-Zuordnung eigens kommentiert ist). Die Freigabeliste entsteht
        erst danach per PATCH. Die beiden anderen Wege dagegen bauen einen
        Container fuer einen Agenten, der sie schon haben KANN -- lesen sie
        sie nicht, wirkt ein PATCH nie.
        """
        for weg in ("restart_agent", "update_agent"):
            with self.subTest(bauweg=weg):
                self.assertIn("browser_allowed_domains", _quelltext(weg))

    def test_alle_bauwege_sind_erfasst(self):
        """Wache gegen einen vierten Bauweg, den niemand hier eintraegt.

        Betrachtet werden nur die Methoden der Klasse selbst. Innerhalb von
        ``restart_agent`` und ``update_agent`` steckt jeweils eine gleichnamige
        Hilfsfunktion (``_create_agent_container``), die das Umfeld ihrer
        Methode benutzt -- sie ist kein eigener Weg, sondern Teil desselben.
        """
        text = _QUELLE.read_text(encoding="utf-8")
        baum = ast.parse(text)
        methoden = [
            k
            for klasse in ast.walk(baum)
            if isinstance(klasse, ast.ClassDef)
            for k in klasse.body
            if isinstance(k, (ast.AsyncFunctionDef, ast.FunctionDef))
        ]
        bauend = {
            k.name for k in methoden
            if "create_container" in (ast.get_source_segment(text, k) or "")
        }
        unbekannt = bauend - set(BAUWEGE)
        self.assertEqual(
            unbekannt, set(),
            f"Neue(r) Bauweg(e) {unbekannt}: bitte den Browser-Schalter dort "
            f"ebenfalls setzen und hier eintragen.",
        )


if __name__ == "__main__":
    unittest.main()

"""Ein selbst angelegter Menuepunkt darf nur nach http/https zeigen.

Ein ``javascript:``-Wert im ``href`` waere fremder Code, der beim Klick in
unserer eigenen Oberflaeche laeuft — mit der Sitzung des Angemeldeten. Angelegt
werden Menuepunkte nur von Administratoren; genau deshalb steht im Validator des
Servers auch schon der richtige Satz: der Administrator ist vertrauenswuerdig,
ein **uebernommener** Zugang ist es nicht, und ein Tippfehler nie.

Der Server prueft das seit jeher beim Anlegen UND beim Aendern. Hier geht es um
die zweite Sperre in der Oberflaeche — fuer Zeilen, die vor dem Validator
entstanden sind oder auf anderem Weg in die Datenbank gelangen.
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SIDEBAR = (ROOT / "frontend/src/components/layout/sidebar.tsx").read_text()
API = (ROOT / "orchestrator/app/api/custom_pages.py").read_text()


class TheServerRefusesThemTests(unittest.TestCase):
    """Die eigentliche Sperre — sie war schon da und bleibt geprueft."""

    def test_only_http_and_https_pass(self):
        block = API.split("def _validate_url", 1)[1][:900]
        self.assertIn('parsed.scheme.lower() not in ("http", "https")', block)

    def test_a_host_is_required(self):
        """``http:///boese`` hat ein gueltiges Schema und trotzdem kein Ziel."""
        block = API.split("def _validate_url", 1)[1][:900]
        self.assertIn("if not parsed.netloc:", block)

    def test_creating_validates(self):
        self.assertIn("url = _validate_url(body.url)", API)

    def test_changing_validates_too(self):
        """Nur das Anlegen zu pruefen hiesse, den Wert danach frei setzen zu
        koennen — die haeufigste halbe Absicherung."""
        self.assertIn("page.url = _validate_url(body.url)", API)


class TheInterfaceRefusesThemAsWellTests(unittest.TestCase):
    def test_the_menu_filters_the_scheme(self):
        self.assertIn("nurWebAdresse(p.url)", SIDEBAR)

    def test_the_check_is_a_whitelist(self):
        """Eine Liste verbotener Schemata vergisst immer eines (``data:``,
        ``vbscript:``, ``jAvAsCrIpT:``)."""
        block = SIDEBAR.split("function nurWebAdresse", 1)[1][:400]
        self.assertIn("/^https?:\\/\\//i.test(", block)

    def test_a_bad_entry_disappears_instead_of_pointing_nowhere(self):
        """``#`` waere ein Menuepunkt, der nichts tut — das sieht aus wie ein
        Fehler und niemand meldet ihn."""
        block = SIDEBAR.split("function nurWebAdresse", 1)[1][:400]
        self.assertIn(": undefined;", block)

    def test_no_raw_url_reaches_the_href(self):
        self.assertNotIn("external: p.open_mode === \"link\" ? p.url", SIDEBAR)


if __name__ == "__main__":
    unittest.main()

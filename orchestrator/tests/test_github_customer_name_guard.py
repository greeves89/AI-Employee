"""Die Kundennamen-Wache muss auch fuer GitHub-Inhalte gelten.

Der Datei-Test prueft den Quelltext dieses oeffentlichen Repos — und hatte damit
nur die Haelfte abgedeckt. Issues, Pull-Request-Beschreibungen und Kommentare
stehen auf derselben oeffentlichen Seite und werden genauso indiziert, wurden
aber nie geprueft.

Gefunden am 03.09.2026 beim Oeffnen von Issue #478: der Klarname eines Kunden
stand dort in der ersten Zeile. Die Nachpruefung ueber 1573 Objekte ergab neun
Issues, einen Pull Request und zwei Kommentare — alle bereinigt.

Dieser Test prueft das Werkzeug, nicht GitHub: ein Netzzugriff im Testlauf waere
langsam, flatterhaft und in der CI nicht immer moeglich.
"""

import importlib.util
import unittest
from pathlib import Path

_PFAD = Path(__file__).resolve().parents[2] / "scripts" / "check_github_customer_names.py"
_spec = importlib.util.spec_from_file_location("check_github_customer_names", _PFAD)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
_QUELLE = _PFAD.read_text()


class EsPrueftAlleOeffentlichenOrteTests(unittest.TestCase):
    def test_issues_und_pull_requests(self):
        self.assertIn('for art in ("issue", "pr"):', _QUELLE)

    def test_auch_kommentare(self):
        """Der Fund vom 03.09. steckte zweimal in Kommentaren — sie sind
        genauso oeffentlich wie der Beschreibungstext."""
        self.assertIn("issues/comments", _QUELLE)
        self.assertIn("pulls/comments", _QUELLE)

    def test_auch_geschlossene_eintraege(self):
        """Ein geschlossenes Issue ist nicht weniger oeffentlich; sieben der
        neun Funde waren geschlossen."""
        self.assertIn('"--state", "all"', _QUELLE)


class EsBenutztDIESELBEErkennungTests(unittest.TestCase):
    def test_es_laedt_die_vorhandene_wache(self):
        """Zwei getrennte Begriffslisten wuerden auseinanderlaufen — und die
        zweite waere wieder ein Klartextverzeichnis."""
        self.assertIn("test_no_customer_names_in_repo.py", _QUELLE)
        w = _mod._wache()
        self.assertTrue(hasattr(w, "zeile_verboten"))
        self.assertTrue(hasattr(w, "VERBOTEN_HASHES"))

    def test_es_erkennt_den_pruefbegriff(self):
        w = _mod._wache()
        self.assertTrue(w.zeile_verboten(f"Kundenfeedback ({w.PRUEFBEGRIFF}, 2026-08-04)"))
        self.assertFalse(w.zeile_verboten("Kundenfeedback (2026-08-04)"))

    def test_es_haelt_keinen_klartext_bereit(self):
        self.assertNotIn("VERBOTEN = (", _QUELLE)


class DieMeldungVerraetDenBegriffNichtTests(unittest.TestCase):
    def test_gemeldet_wird_nur_der_ort(self):
        """Sonst stuende der Name im CI-Protokoll — bei einem oeffentlichen
        Repo ebenso oeffentlich wie im Text selbst (#688)."""
        block = _QUELLE.split("def pruefe", 1)[1]
        self.assertIn('funde.append(f"{art} #{e[\'number\']}")', block)
        self.assertIn("Der getroffene Begriff steht hier bewusst nicht.", _QUELLE)


class EsScheitertNichtAmFehlendenZugangTests(unittest.TestCase):
    def test_ohne_gh_gibt_es_keinen_fehlalarm(self):
        """Kein GitHub-Zugang ist kein Fund — sonst waere der Lauf ueberall
        rot, wo kein Token liegt, und wuerde bald ignoriert."""
        self.assertIn("except (subprocess.CalledProcessError, FileNotFoundError)", _QUELLE)
        block = _QUELLE.split("GitHub nicht erreichbar", 1)[1][:200]
        self.assertIn("return 0", block)

    def test_ein_fund_scheitert_hart(self):
        block = _QUELLE.split("if funde:", 1)[1][:600]
        self.assertIn("return 1", block)


class DieSeitenweiseAbfrageIstRichtigGebautTests(unittest.TestCase):
    def test_mehrere_json_dokumente_werden_gelesen(self):
        """`gh --paginate` liefert je Seite EIN Dokument. Ein schlichtes
        json.loads() bricht ab der zweiten Seite ab — genau daran ist mein
        erster Anlauf gescheitert."""
        self.assertIn("raw_decode", _QUELLE)

    def test_es_liest_wirklich_mehrere_seiten(self):
        roh = '[{"id": 1, "body": "a"}]\n[{"id": 2, "body": "b"}]\n'
        import unittest.mock
        with unittest.mock.patch.object(
            _mod.subprocess, "run",
            return_value=unittest.mock.Mock(stdout=roh),
        ):
            self.assertEqual(len(_mod._gh_json(["api", "x", "--paginate"])), 2)


if __name__ == "__main__":
    unittest.main()


class EsHaengtInDerCiTests(unittest.TestCase):
    """Ein Skript, das keine CI aufruft, prueft gar nichts — dieselbe Lehre
    wie bei der Release-Spur (#699)."""

    CI = (Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml").read_text()

    def test_es_gibt_einen_job(self):
        self.assertIn("github-customer-names:", self.CI)

    def test_er_ruft_das_skript_auf(self):
        self.assertIn("scripts/check_github_customer_names.py", self.CI)

    def test_er_hat_leserechte_auf_issues_und_prs(self):
        """Ohne sie liefert `gh` nichts und der Lauf waere immer gruen."""
        block = self.CI.split("github-customer-names:", 1)[1].split("\n  compose-config:", 1)[0]
        self.assertIn("issues: read", block)
        self.assertIn("pull-requests: read", block)
        self.assertIn("GH_TOKEN:", block)

    def test_er_prueft_nur_das_juengste_fenster(self):
        """Ein Lauf ueber alles kostet bei jedem Push mehrere hundert
        API-Aufrufe; der Altbestand ist bereinigt."""
        self.assertIn("--seit 30", self.CI)

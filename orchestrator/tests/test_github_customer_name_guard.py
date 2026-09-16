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
import unittest.mock
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
        Repo ebenso oeffentlich wie im Text selbst (#688). `pruefe` wird mit
        einer gh-Attrappe gefahren, die den Pruefbegriff in einem Issue und
        einem Kommentar liefert; die Funde duerfen ihn nicht enthalten."""
        w = _mod._wache()
        begriff = w.PRUEFBEGRIFF

        def gh(args):
            if args[1] == "list":
                return [{"number": 7, "title": f"Rueckmeldung {begriff}", "body": "",
                         "updatedAt": "2026-09-01T00:00:00Z"},
                        {"number": 8, "title": "harmlos", "body": "", "updatedAt": ""}]
            if "issues/comments" in args[1]:
                return [{"id": 99, "body": f"siehe {begriff}",
                         "issue_url": "https://example.invalid/issues/3"}]
            return []

        with unittest.mock.patch.object(_mod, "_gh_json", gh):
            funde = _mod.pruefe()
        self.assertEqual(funde, ["issue #7", "pr #7", "Kommentar 99 an #3"])
        for f in funde:
            self.assertNotIn(begriff, f)
        self.assertIn("Der getroffene Begriff steht hier bewusst nicht.", _QUELLE)


class EsScheitertNichtAmFehlendenZugangTests(unittest.TestCase):
    """`main()` wird wirklich gefahren — mit einer Attrappe fuer `pruefe`, damit
    kein `gh` laeuft. Was zaehlt, ist der Rueckgabewert, nicht der Quelltext."""

    def _main(self, pruefe):
        import contextlib
        import io
        import unittest.mock
        ausgabe = io.StringIO()
        with unittest.mock.patch.object(_mod, "pruefe", pruefe), \
                unittest.mock.patch.object(_mod.sys, "argv", ["check_github_customer_names.py"]), \
                contextlib.redirect_stdout(ausgabe):
            rc = _mod.main()
        return rc, ausgabe.getvalue()

    def test_ohne_gh_gibt_es_keinen_fehlalarm(self):
        """Kein GitHub-Zugang ist kein Fund — sonst waere der Lauf ueberall
        rot, wo kein Token liegt, und wuerde bald ignoriert."""
        for fehler in (FileNotFoundError("gh"),
                       _mod.subprocess.CalledProcessError(1, ["gh"])):
            with self.subTest(type(fehler).__name__):
                rc, text = self._main(unittest.mock.Mock(side_effect=fehler))
                self.assertEqual(rc, 0)
                self.assertIn("uebersprungen", text)

    def test_ein_programmfehler_wird_nicht_als_kein_zugang_verbucht(self):
        """Die Ausnahme-Liste darf nicht zu `except Exception` werden: ein
        KeyError in `pruefe` waere dann ein gruener Lauf."""
        with self.assertRaises(KeyError):
            self._main(unittest.mock.Mock(side_effect=KeyError("number")))

    def test_ein_fund_scheitert_hart(self):
        rc, text = self._main(lambda tage=None: ["issue #4711"])
        self.assertEqual(rc, 1)
        self.assertIn("issue #4711", text)

    def test_ohne_fund_ist_der_lauf_gruen(self):
        """Die Gegenprobe: `return 1` darf nicht bedingungslos sein."""
        rc, _text = self._main(lambda tage=None: [])
        self.assertEqual(rc, 0)


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

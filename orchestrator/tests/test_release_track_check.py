"""Der Waechter ueber die Release-Spur muss selbst verlaesslich sein.

Ein CI-Check, der falsch anschlaegt, wird nach kurzer Zeit ignoriert — dann ist
er schlimmer als keiner. Deshalb pruefen diese Tests vor allem die Faelle, in
denen er NICHT anschlagen darf.

Hintergrund #699: Die Release-Spur (VERSION + Dockerfile-Label + CHANGELOG) war
reine Disziplin. Am 02.09.2026 trugen zwei Paare offener PRs dieselbe Nummer,
drei lagen unter main, ein Direkt-Commit hatte gar keinen Eintrag.
"""

import importlib.util
import unittest
import unittest.mock
from pathlib import Path

_PFAD = Path(__file__).resolve().parents[2] / "scripts" / "release_track_check.py"
_spec = importlib.util.spec_from_file_location("release_track_check", _PFAD)
rt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rt)


class VersionenWerdenAlsZahlenVerglichenTests(unittest.TestCase):
    def test_der_klassische_zeichenketten_fehler(self):
        """Als Text waere 1.276.9 groesser als 1.276.11 — der Check waere dann
        schlimmer als keiner, weil er echte Rueckschritte durchwinkt und
        korrekte Spruenge blockiert."""
        self.assertTrue(rt.steigt_streng("1.276.9", "1.276.11"))
        self.assertFalse(rt.steigt_streng("1.276.11", "1.276.9"))

    def test_gleichstand_ist_kein_anstieg(self):
        self.assertFalse(rt.steigt_streng("1.277.0", "1.277.0"))

    def test_luecken_sind_erlaubt(self):
        """Liegen mehrere versionierte Branches in der Warteschlange, entstehen
        Luecken regulaer (belegt an 1.276.7). Geprueft wird Monotonie."""
        self.assertTrue(rt.steigt_streng("1.276.6", "1.276.8"))
        self.assertTrue(rt.steigt_streng("1.276.11", "1.280.0"))

    def test_stellenwechsel(self):
        self.assertTrue(rt.steigt_streng("1.9.9", "1.10.0"))
        self.assertTrue(rt.steigt_streng("1.286.0", "2.0.0"))

    def test_unlesbares_wird_gemeldet_nicht_verschluckt(self):
        with self.assertRaises(ValueError):
            rt.als_zahlen("dev")


class DasLabelWirdAusDemDockerfileGelesenTests(unittest.TestCase):
    def test_normalfall(self):
        self.assertEqual(
            rt.label_aus_dockerfile('FROM x\nLABEL ai-employee.version="1.2.3"\n'),
            "1.2.3")

    def test_fehlt_es_ganz(self):
        self.assertIsNone(rt.label_aus_dockerfile("FROM python:3.12-slim\n"))

    def test_ein_erwaehnung_im_kommentar_zaehlt_nicht(self):
        """Sonst liesse sich der Check mit einem Kommentar austricksen."""
        self.assertIsNone(rt.label_aus_dockerfile(
            '# LABEL ai-employee.version="9.9.9" (alt)\nFROM x\n'))


class DerChangelogMussDieNummerKennenTests(unittest.TestCase):
    CL = "# Changelog\n\n---\n\n## [1.286.0] - 2026-09-02\n\n### Behoben\n- x\n"

    def test_vorhanden(self):
        self.assertTrue(rt.changelog_kennt(self.CL, "1.286.0"))

    def test_fehlt(self):
        self.assertFalse(rt.changelog_kennt(self.CL, "1.287.0"))

    def test_eine_erwaehnung_im_fliesstext_reicht_nicht(self):
        """„siehe 1.287.0" ist kein Eintrag."""
        self.assertFalse(rt.changelog_kennt(
            self.CL + "\nDetails zu 1.287.0 folgen spaeter.\n", "1.287.0"))

    def test_teiltreffer_zaehlen_nicht(self):
        """`## [1.286.01]` darf nicht als `1.286.0` durchgehen."""
        self.assertFalse(rt.changelog_kennt("## [1.286.01] - x\n", "1.286.0"))


class ReineDokuBrauchtKeinenVersionssprungTests(unittest.TestCase):
    def test_ein_readme_nachtrag(self):
        self.assertTrue(rt.nur_doku_beruehrt(["README.md", "docs/anleitung.md"]))

    def test_der_changelog_allein_zaehlt_auch_als_doku(self):
        """Einen Tippfehler im CHANGELOG zu heilen darf nicht am Check scheitern."""
        self.assertTrue(rt.nur_doku_beruehrt(["CHANGELOG.md"]))

    def test_code_daneben_hebt_die_ausnahme_auf(self):
        self.assertFalse(rt.nur_doku_beruehrt(["README.md", "orchestrator/app/main.py"]))

    def test_ein_test_ist_kein_dokument(self):
        self.assertFalse(rt.nur_doku_beruehrt(["orchestrator/tests/test_x.py"]))

    def test_eine_leere_aenderung(self):
        self.assertTrue(rt.nur_doku_beruehrt([]))


class DiePrWarnungenTrifftDieEchtenFaelleTests(unittest.TestCase):
    """Nachgestellt sind die vier Befunde aus #699 vom 02.09.2026."""

    def test_zwei_offene_prs_auf_derselben_nummer(self):
        """Der reale Fall: #696 und #685 trugen beide 1.277.0."""
        self.assertEqual(
            rt.doppelt_vergeben("1.277.0", {"696": "1.277.0", "669": "1.269.5"}),
            ["#696 (1.277.0)"])

    def test_mehrere_kollisionen_werden_alle_genannt(self):
        self.assertEqual(
            rt.doppelt_vergeben("1.276.12", {"686": "1.276.12", "661": "1.276.12"}),
            ["#661 (1.276.12)", "#686 (1.276.12)"])

    def test_ohne_kollision_bleibt_es_still(self):
        self.assertEqual(rt.doppelt_vergeben("1.286.0", {"649": "1.268.4"}), [])

    def test_leerzeichen_taeuschen_keine_verschiedenheit_vor(self):
        """`1.277.0\n` und `1.277.0` sind dieselbe Nummer."""
        self.assertEqual(rt.doppelt_vergeben("1.277.0\n", {"696": " 1.277.0 "}),
                         ["#696 (1.277.0)"])

    def test_ein_branch_unter_main(self):
        self.assertFalse(rt.steigt_streng("1.276.11", "1.269.5"))
        self.assertFalse(rt.steigt_streng("1.276.11", "1.268.4"))


class DieWarteschlangeSiehtAuchBranchesOhnePrTests(unittest.TestCase):
    """Folgefund zu #699 (#707): `pruefe_pull_request` bekommt Vergleichsnummern
    nur aus offenen PRs — ein Branch ohne PR (z. B. waehrend einer PR-Sperre)
    ist fuer sie strukturell unsichtbar. Zwei solche Branches mit derselben
    Nummer werden erst beim ZWEITEN Merge bemerkt, main ist dann schon rot.
    """

    def test_der_reale_fall_vom_05_09(self):
        """Beobachtet: zwei Branches ohne PR trugen beide 1.314.0."""
        self.assertEqual(
            rt.kollisionen_in_warteschlange({
                "fix/a": "1.314.0", "fix/b": "1.314.0", "fix/c": "1.315.2",
            }),
            ["1.314.0 ist auf 2 Branches vergeben: fix/a, fix/b"],
        )

    def test_mehrere_kollidierende_gruppen_werden_alle_genannt(self):
        self.assertEqual(
            rt.kollisionen_in_warteschlange({
                "a": "1.1.0", "b": "1.1.0", "c": "1.2.0", "d": "1.2.0", "e": "1.3.0",
            }),
            ["1.1.0 ist auf 2 Branches vergeben: a, b",
             "1.2.0 ist auf 2 Branches vergeben: c, d"],
        )

    def test_ohne_kollision_bleibt_es_still(self):
        self.assertEqual(
            rt.kollisionen_in_warteschlange({"a": "1.1.0", "b": "1.2.0"}), [])

    def test_ein_einzelner_branch_kollidiert_nie(self):
        self.assertEqual(rt.kollisionen_in_warteschlange({"a": "1.1.0"}), [])

    def test_eine_leere_warteschlange(self):
        self.assertEqual(rt.kollisionen_in_warteschlange({}), [])

    def test_leerzeichen_taeuschen_keine_verschiedenheit_vor(self):
        self.assertEqual(
            rt.kollisionen_in_warteschlange({"a": "1.1.0\n", "b": " 1.1.0 "}),
            ["1.1.0 ist auf 2 Branches vergeben: a, b"],
        )

    def test_drei_branches_auf_derselben_nummer(self):
        self.assertEqual(
            rt.kollisionen_in_warteschlange({"a": "1.1.0", "b": "1.1.0", "c": "1.1.0"}),
            ["1.1.0 ist auf 3 Branches vergeben: a, b, c"],
        )

class BranchVersionenFiltertDenEchtenBestandTests(unittest.TestCase):
    """`branch_versionen` treibt echtes `git`, deshalb wird hier `rt.git`
    ersetzt und das VERHALTEN geprueft — nicht der Quelltext an einer
    Zeichenmarke (genau das waere die Fehlerklasse, die #726 abstellt).

    Aufbau: vier Fern-Branches. Einer ist Dependabot (Regel: ausgeschlossen,
    kennt die VERSION-Pflicht nicht), einer ist Monate alt (Regel:
    ausgeschlossen, keine wartende Aenderung mehr), einer hat keine
    VERSION-Datei (Regel: uebersprungen, nichts zu vergleichen), einer ist
    frisch und zaehlt.
    """

    JETZT = 1_800_000_000  # fester Bezugspunkt, macht den Test zeitunabhaengig

    def setUp(self):
        self.zeit_patch = unittest.mock.patch.object(rt.time, "time", return_value=self.JETZT)
        self.zeit_patch.start()
        self.addCleanup(self.zeit_patch.stop)

        branches = {
            "origin/main": {"commit": self.JETZT, "version": "9.9.9"},
            "origin/HEAD": {"commit": self.JETZT, "version": "9.9.9"},
            "origin/dependabot/pip/foo-1.2.3": {"commit": self.JETZT, "version": "1.0.0"},
            "origin/fix/alt-und-vergessen": {
                "commit": self.JETZT - (rt.WARTESCHLANGE_TAGE + 5) * 86400, "version": "1.0.0",
            },
            "origin/fix/ohne-version-datei": {"commit": self.JETZT, "version": None},
            "origin/fix/frisch": {"commit": self.JETZT - 3600, "version": "1.5.0"},
        }

        def fake_git(*args):
            if args[0] == "for-each-ref":
                return "\n".join(branches)
            if args[0] == "log":
                return str(branches[args[3]]["commit"])
            if args[0] == "show":
                ref = args[1].split(":", 1)[0]
                version = branches[ref]["version"]
                if version is None:
                    raise rt.subprocess.CalledProcessError(1, "git show")
                return version
            raise AssertionError(f"unerwarteter git-Aufruf: {args}")

        self.git_patch = unittest.mock.patch.object(rt, "git", side_effect=fake_git)
        self.git_patch.start()
        self.addCleanup(self.git_patch.stop)

    def test_dependabot_wird_ausgeschlossen(self):
        self.assertNotIn("dependabot/pip/foo-1.2.3", rt.branch_versionen())

    def test_main_und_head_werden_ausgeschlossen(self):
        ergebnis = rt.branch_versionen()
        self.assertNotIn("main", ergebnis)
        self.assertNotIn("HEAD", ergebnis)

    def test_ein_monatealter_branch_zaehlt_nicht_mehr_zur_warteschlange(self):
        self.assertNotIn("fix/alt-und-vergessen", rt.branch_versionen())

    def test_ein_branch_ohne_version_datei_wird_uebersprungen_statt_zu_krachen(self):
        self.assertNotIn("fix/ohne-version-datei", rt.branch_versionen())

    def test_ein_frischer_branch_mit_version_zaehlt(self):
        self.assertEqual(rt.branch_versionen(), {"fix/frisch": "1.5.0"})


class DerNeueModusHaengtWirklichInDerCiTests(unittest.TestCase):
    """Ein Skript, das keine CI aufruft, prueft gar nichts (siehe #699)."""

    WORKFLOWS = (Path(__file__).resolve().parents[2] / ".github" / "workflows")

    def test_es_gibt_einen_eigenen_lauf(self):
        dateien = list(self.WORKFLOWS.glob("*.yml"))
        treffer = [d for d in dateien if "release_track_check.py warteschlange" in d.read_text()]
        self.assertTrue(treffer, "kein Workflow ruft den warteschlange-Modus auf")

    def test_der_lauf_darf_tatsaechlich_scheitern(self):
        """Anders als der PR-Lauf blockiert dieser niemanden einzelnen — er darf
        deshalb rot werden, statt nur zu warnen."""
        dateien = list(self.WORKFLOWS.glob("*.yml"))
        treffer = [d for d in dateien if "release_track_check.py warteschlange" in d.read_text()]
        for d in treffer:
            self.assertNotIn("continue-on-error: true", d.read_text())


class DerCheckBlockiertNiemalsEinenPullRequestTests(unittest.TestCase):
    def test_der_pr_modus_endet_immer_mit_null(self):
        """Ein PR darf am Versionsstand nicht scheitern — der Merge kann ihn
        aufloesen. Nur der Push auf main ist hart."""
        quelle = _PFAD.read_text()
        block = quelle.split('if args.modus == "push":', 1)[1]
        self.assertIn("return 0  # bei einem PR NIE blockieren", block)

    def test_der_push_modus_kann_scheitern(self):
        quelle = _PFAD.read_text()
        self.assertIn("return 1", quelle)


if __name__ == "__main__":
    unittest.main()


class DerCheckHaengtWirklichInDerCiTests(unittest.TestCase):
    """Ein Skript, das keine CI aufruft, prueft gar nichts.

    Genau diese Luecke war das Thema von #699: die Regel existierte, nur eben
    nirgends als Gate.
    """

    CI = (Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml").read_text()

    def test_es_gibt_einen_job(self):
        self.assertIn("release-track:", self.CI)

    def test_der_push_zweig_ist_hart(self):
        """Auf main muss der Lauf scheitern koennen — sonst aendert sich nichts."""
        self.assertIn("release_track_check.py push", self.CI)
        self.assertIn("if: github.event_name == 'push'", self.CI)

    def test_der_pr_zweig_laeuft_getrennt(self):
        self.assertIn("release_track_check.py pr", self.CI)
        self.assertIn("if: github.event_name == 'pull_request'", self.CI)

    def test_die_historie_wird_vollstaendig_geholt(self):
        """Mit einem flachen Klon gibt es keinen Vorgaenger zum Vergleichen."""
        block = self.CI.split("release-track:", 1)[1].split("\n  compose-config:", 1)[0]
        self.assertIn("fetch-depth: 0", block)

    def test_der_vergleichspunkt_kommt_vom_ereignis(self):
        self.assertIn("--vorher \"${{ github.event.before }}\"", self.CI)

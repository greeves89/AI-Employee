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

"""Release-Läufer (scripts/release.py): erst prüfen, nur mit --ausfuehren ändern.

Jede Beanstandung wird einzeln ausgelöst (MC/DC: jede Bedingung allein kippt das
Ergebnis), der Probelauf darf keinen ändernden Aufruf machen, und die Ausführung hält
die Reihenfolge Merge → Version auf main prüfen → Tag → Release ein.
"""

import json
import sys
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import release  # noqa: E402

CHANGELOG = """# Changelog

## [1.500.0] - 2026-11-07

### Sicherheit
- Etwas Wichtiges.

## [1.499.0] - 2026-11-06

- Älteres.
"""


def gut() -> release.Fakten:
    return release.Fakten(
        pr=7, zustand="OPEN", basis="main", mergebar="MERGEABLE", kopf="a" * 40,
        version="1.500.0", label="1.500.0", changelog=CHANGELOG, version_main="1.499.0",
        tag_vorhanden=False, pruefungen=[{"name": "Tests", "state": "SUCCESS"},
                                         {"name": "Bridge", "state": "SKIPPED"}],
        ci_main={"status": "completed", "conclusion": "success", "headSha": "b" * 40})


class BeanstandungTests(unittest.TestCase):

    def test_alles_gut(self):
        self.assertEqual(release.beanstandungen(gut()), [])

    def test_jede_bedingung_allein(self):
        faelle = {
            "nicht offen": dict(zustand="MERGED"),
            "nicht auf main": dict(basis="release"),
            "Konflikt": dict(mergebar="CONFLICTING"),
            "keine Versionsnummer": dict(version="1.500"),
            "Label weicht ab": dict(label="1.499.0"),
            "Changelog fehlt": dict(changelog="# Changelog\n\n## [1.499.0] - x\n"),
            "nicht größer als main": dict(version_main="1.500.0"),
            "Tag schon da": dict(tag_vorhanden=True),
            "keine Prüfungen": dict(pruefungen=[]),
            "Prüfung rot": dict(pruefungen=[{"name": "Tests", "state": "FAILURE"}]),
            "Prüfung läuft": dict(pruefungen=[{"name": "Tests", "state": "PENDING"}]),
            "kein Lauf auf main": dict(ci_main=None),
            "main rot": dict(ci_main={"status": "completed", "conclusion": "failure"}),
            "main läuft": dict(ci_main={"status": "in_progress", "conclusion": ""}),
        }
        for name, aenderung in faelle.items():
            with self.subTest(name):
                self.assertEqual(len(release.beanstandungen(replace(gut(), **aenderung))), 1, name)


class NotizenTests(unittest.TestCase):

    def test_abschnitt_ohne_ueberschrift(self):
        n = release.notizen(CHANGELOG, "1.500.0")
        self.assertTrue(n.startswith("### Sicherheit"))
        self.assertNotIn("1.499.0", n)
        self.assertNotIn("Älteres", n)

    def test_letzter_abschnitt_und_unbekannte_version(self):
        self.assertIn("Älteres", release.notizen(CHANGELOG, "1.499.0"))
        self.assertEqual(release.notizen(CHANGELOG, "9.9.9"), "")


class GhDouble:
    """Antwortet wie gh/git und merkt sich jeden Aufruf."""

    AENDERND = (("gh", "pr", "merge"), ("git", "tag"), ("git", "push"), ("gh", "release", "create"))

    def __init__(self, version_nach_merge="1.500.0", **fakten):
        self.aufrufe: list[tuple] = []
        self.version_nach_merge = version_nach_merge
        self.f = {**gut().__dict__, **fakten}
        self.gemergt = False

    def __call__(self, *args, eingabe=None):
        self.aufrufe.append(args)
        f = self.f
        if args[:3] == ("gh", "pr", "view"):
            return json.dumps({"state": f["zustand"], "baseRefName": f["basis"],
                               "mergeable": f["mergebar"], "headRefOid": f["kopf"]})
        if args[:2] == ("git", "show"):
            ref, pfad = args[2].split(":", 1)
            if ref == "origin/main":
                return (self.version_nach_merge if self.gemergt else f["version_main"]) + "\n"
            return {"VERSION": f["version"] + "\n",
                    "agent/Dockerfile": f'FROM x\nLABEL ai-employee.version="{f["label"]}"\n',
                    "CHANGELOG.md": f["changelog"]}[pfad]
        if args[:2] == ("git", "ls-remote"):
            return "abc refs/tags/v1.500.0\n" if f["tag_vorhanden"] else ""
        if args[:3] == ("gh", "pr", "checks"):
            return json.dumps(f["pruefungen"])
        if args[:3] == ("gh", "run", "list"):
            return json.dumps([f["ci_main"]] if f["ci_main"] else [])
        if args[:3] == ("gh", "pr", "merge"):
            self.gemergt = True
            return ""
        if args[:2] == ("git", "rev-parse"):
            return "c" * 40 + "\n"
        if args[:3] == ("gh", "release", "create"):
            self.notizen = eingabe
            return "https://github.com/example/releases/tag/v1.500.0\n"
        return ""

    def aendernde(self):
        return [a for a in self.aufrufe if any(a[:len(p)] == p for p in self.AENDERND)]


class LaeuferTests(unittest.TestCase):

    def _main(self, double, *argv):
        return release.main(["7", *argv], gh=release.GitHub("example/repo", run=double))

    def test_probelauf_aendert_nichts(self):
        d = GhDouble()
        self.assertEqual(self._main(d), 0)
        self.assertEqual(d.aendernde(), [])

    def test_beanstandung_verhindert_auch_mit_ausfuehren(self):
        d = GhDouble(ci_main={"status": "completed", "conclusion": "failure"})
        self.assertEqual(self._main(d, "--ausfuehren"), 1)
        self.assertEqual(d.aendernde(), [])

    def test_ausfuehren_in_der_richtigen_reihenfolge(self):
        d = GhDouble()
        self.assertEqual(self._main(d, "--ausfuehren"), 0)
        schritte = [a[:3] for a in d.aendernde()]
        self.assertEqual(schritte, [("gh", "pr", "merge"), ("git", "tag", "v1.500.0"),
                                    ("git", "push", "-q"), ("gh", "release", "create")])
        merge = d.aendernde()[0]
        self.assertIn("--match-head-commit", merge)
        self.assertEqual(merge[merge.index("--match-head-commit") + 1], "a" * 40)
        self.assertTrue(d.notizen.startswith("### Sicherheit"))

    def test_falsche_version_nach_merge_kein_tag(self):
        d = GhDouble(version_nach_merge="1.501.0")
        with self.assertRaises(RuntimeError):
            self._main(d, "--ausfuehren")
        self.assertEqual([a[:3] for a in d.aendernde()], [("gh", "pr", "merge")])

    def test_warten_bis_ci_fertig(self):
        d = GhDouble(pruefungen=[{"name": "Tests", "state": "PENDING"}])
        gh = release.GitHub("example/repo", run=d)
        zaehler = {"n": 0}
        orig = d.__call__

        def mit_umschalten(*args, eingabe=None):
            if args[:3] == ("gh", "pr", "checks"):
                zaehler["n"] += 1
                if zaehler["n"] >= 2:
                    d.f["pruefungen"] = [{"name": "Tests", "state": "SUCCESS"}]
            return orig(*args, eingabe=eingabe)

        gh.run = mit_umschalten
        from unittest.mock import patch
        with patch.object(release.time, "sleep"):
            release.warten_auf_ci(gh, 7, minuten=1, takt=1)
        self.assertGreaterEqual(zaehler["n"], 2)


if __name__ == "__main__":
    unittest.main()

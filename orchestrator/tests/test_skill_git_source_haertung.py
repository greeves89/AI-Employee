"""Issue #968 Punkt 3: der Klon fremder Repos lief ohne die gemeinsame git-Haertung.

``skill_crawler._crawl_git_source`` hatte ``core.symlinks=false``, ``--`` und eine
Zeitgrenze, erbte aber ``os.environ`` ohne die Schalter aus #963/#967:
``core.hooksPath``, ``core.fsmonitor`` und ``credential.helper`` starten jeweils
ein PROGRAMM, und zwar im Orchestrator. Anderer Bedrohungsweg als #964 (die URL
setzt ein Administrator, und beim Klonen liest git die Konfiguration der
Gegenseite nicht) — es war aber die letzte Stelle im Orchestrator ohne die
Haertung.

Gemessen wird das ARGV, das git wirklich bekommt (ueber ein vorgeschobenes
``git`` im PATH), nicht die Schreibweise im Quelltext: ein ``-c`` HINTER
``clone`` waere ein git-Fehler, und ein ``-c`` hinter ``--`` waere ein
Verzeichnisname. Dazu eine Messung an echtem git, die belegt, dass der Schalter
wirklich beisst.
"""

import asyncio
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.skill_crawler import SkillCrawlerService

# Vollstaendig, nicht beispielhaft: die Anzahl unten rechnet gegen diese Liste,
# ein fehlender Eintrag waere sonst nur von einer magischen Zahl gedeckt.
HAERTUNG = ("core.symlinks=false", "core.hooksPath=/dev/null", "core.fsmonitor=false",
            "core.pager=cat", "credential.helper=",
            "protocol.ext.allow=never", "protocol.file.allow=never")


def _git_attrappe(verzeichnis: str, protokoll: str) -> str:
    """Ein ``git`` im PATH, das sein argv aufschreibt und den Klon vortaeuscht."""
    bin_dir = os.path.join(verzeichnis, "bin")
    os.makedirs(bin_dir, exist_ok=True)
    pfad = os.path.join(bin_dir, "git")
    Path(pfad).write_text(
        "#!/bin/sh\n"
        f'for a in "$@"; do printf "%s\\n" "$a" >> {protokoll}; done\n'
        # Das Zielverzeichnis ist das letzte Argument — der Aufrufer sucht darin
        # anschliessend nach SKILL.md, es muss also existieren.
        'eval "ziel=\\${$#}"\n'
        'mkdir -p "$ziel"\n'
        "exit 0\n"
    )
    os.chmod(pfad, 0o755)
    return bin_dir


class ArgvDesKlonsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.protokoll = os.path.join(self.tmp, "argv.txt")
        bin_dir = _git_attrappe(self.tmp, self.protokoll)
        self.umgebung = {**os.environ, "PATH": bin_dir + os.pathsep + os.environ.get("PATH", "")}

    def _argv(self, quelle: dict) -> list[str]:
        svc = SkillCrawlerService.__new__(SkillCrawlerService)
        with patch.dict(os.environ, self.umgebung, clear=False):
            ergebnis = asyncio.run(svc._crawl_git_source(quelle))
        self.assertEqual(ergebnis, [], "die Attrappe liefert ein leeres Repo")
        with open(self.protokoll) as f:
            return [z.rstrip("\n") for z in f]

    def test_jeder_haertungsschalter_steht_im_argv(self):
        argv = self._argv({"location": "https://example.invalid/x.git"})

        for schalter in HAERTUNG:
            with self.subTest(schalter=schalter):
                self.assertIn(schalter, argv, f"argv: {argv}")

    def test_die_schalter_stehen_vor_dem_unterbefehl_und_vor_dem_trenner(self):
        """Trennschaerfe: „der Schalter kommt im argv vor" waere auch wahr, wenn er
        hinter ``clone`` steht (git-Fehler) oder hinter ``--`` (Verzeichnisname)."""
        argv = self._argv({"location": "https://example.invalid/x.git"})
        grenze = min(argv.index("clone"), argv.index("--"))

        for schalter in HAERTUNG:
            with self.subTest(schalter=schalter):
                self.assertLess(argv.index(schalter), grenze, f"argv: {argv}")
        self.assertEqual(argv.count("-c"), len(HAERTUNG),
                         f"jeder Schalter braucht sein eigenes -c; argv: {argv}")

    def test_die_haertung_bleibt_auch_mit_zweig_und_trenner_erhalten(self):
        argv = self._argv({"location": "https://example.invalid/x.git", "ref": "main"})

        self.assertIn("--branch", argv)
        self.assertLess(argv.index("core.hooksPath=/dev/null"), argv.index("--branch"))
        self.assertIn("--", argv)

    def test_eine_url_mit_bindestrich_erreicht_git_gar_nicht(self):
        """Vorbedingung aus #371: die Sperre gegen Flag-Schmuggel bleibt davor.

        Der Lauf MUSS dabei ueber die Attrappe gehen. Gegen echtes git waere der
        Test scheingruen: dann wirft der fehlgeschlagene Klon denselben
        ``RuntimeError``, auch wenn die Sperre entfernt wurde (gemessen).
        """
        svc = SkillCrawlerService.__new__(SkillCrawlerService)
        with patch.dict(os.environ, self.umgebung, clear=False):
            with self.assertRaises(RuntimeError):
                asyncio.run(svc._crawl_git_source({"location": "--upload-pack=sh"}))
        self.assertFalse(os.path.exists(self.protokoll), "git wurde trotzdem gestartet")


class EchtesGitBeisstTests(unittest.TestCase):
    """Die Gegenprobe an echtem git: der Schalter ist kein Zierrat.

    Ohne sie wuerde eine umbenannte oder entfernte git-Option unbemerkt zur
    leeren Zusage — das argv saehe weiter richtig aus.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.quelle = os.path.join(self.tmp, "src")
        subprocess.run(["git", "init", "-q", self.quelle], check=True)
        Path(self.quelle, "SKILL.md").write_text("# x\n")
        for args in (["add", "-A"],
                     ["-c", "user.name=t", "-c", "user.email=t@example.invalid",
                      "commit", "-qm", "x"]):
            subprocess.run(["git", *args], cwd=self.quelle, check=True)

    def _klone(self, *schalter: str) -> subprocess.CompletedProcess:
        ziel = tempfile.mkdtemp(dir=self.tmp)
        os.rmdir(ziel)
        return subprocess.run(["git", *schalter, "clone", "--depth", "1", "--", self.quelle, ziel],
                              capture_output=True, text=True,
                              env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})

    def test_ohne_den_schalter_gelingt_der_lokale_klon(self):
        """Vorbedingung: ohne diese Zeile koennte der Test unten auch gruen sein,
        weil der Klon aus einem anderen Grund scheitert."""
        self.assertEqual(self._klone().returncode, 0)

    def test_mit_dem_schalter_verweigert_git_den_lokalen_pfad(self):
        r = self._klone("-c", "protocol.file.allow=never")

        self.assertNotEqual(r.returncode, 0)
        # Nicht auf "file" pruefen: das steht wegen
        # "warning: --depth is ignored in local clones; use file:// instead"
        # IMMER in stderr, auch bei einem verschriebenen Schalternamen.
        self.assertIn("transport 'file' not allowed", r.stderr)
        self.assertNotIn("unknown value", r.stderr,
                         "git bricht an der Konfiguration ab, statt den Transport zu sperren")


if __name__ == "__main__":
    unittest.main()

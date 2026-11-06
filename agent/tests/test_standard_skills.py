"""Mitgelieferte Standard-Skills im Abbild (agent/skills_standard/ → /opt/skills-standard).

Beim Start legt jeder Agent sie in den Skill-Ordner SEINER Laufzeit — Claude Code
liest `.claude/skills`, Codex `.agents/skills`, der Skill-Index für eigene Modelle
beide. Ohne Netz und mit festem, geprüftem Stand (statt `npx skills add` von GitHub).
"""
import os
import tempfile
import unittest
from pathlib import Path

from app import standard_skills as ss

ABBILD = Path(__file__).resolve().parents[1] / "skills_standard"


def _skill(quelle: Path, name: str, stand: str, text: str = "Inhalt") -> None:
    ordner = quelle / name
    (ordner / "kit").mkdir(parents=True, exist_ok=True)
    (ordner / "SKILL.md").write_text(f"---\nname: {name}\n---\n{text}\n")
    (ordner / "kit" / "teil.mjs").write_text("export const x = 1;\n")
    (ordner / "STAND").write_text(stand + "\n")


class Bereitstellen(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.quelle, self.ws = tmp / "opt", tmp / "workspace"
        self.quelle.mkdir()
        self.ws.mkdir()
        _skill(self.quelle, "anatomy", "aaa111")

    def tearDown(self):
        self._tmp.cleanup()

    def _ziel(self, laufzeit: str) -> Path:
        return self.ws / ss.ZIELE[laufzeit] / "anatomy"

    def test_jede_laufzeit_bekommt_den_ganzen_skill_in_ihren_ordner(self):
        for laufzeit, ordner in (("claude_code", ".claude/skills"), ("codex_cli", ".agents/skills"),
                                 ("custom_llm", ".agents/skills")):
            with self.subTest(laufzeit=laufzeit):
                ss.bereitstellen(laufzeit, quelle=self.quelle, workspace=self.ws)
                ziel = self.ws / ordner / "anatomy"
                self.assertTrue((ziel / "SKILL.md").is_file())
                self.assertTrue((ziel / "kit" / "teil.mjs").is_file(), "das ganze Paket, nicht nur SKILL.md")

    def test_gleicher_stand_bleibt_unangetastet(self):
        ss.bereitstellen("claude_code", quelle=self.quelle, workspace=self.ws)
        notiz = self._ziel("claude_code") / "kit" / "notiz.txt"
        notiz.write_text("vom Agenten")
        self.assertEqual(ss.bereitstellen("claude_code", quelle=self.quelle, workspace=self.ws), [])
        self.assertTrue(notiz.exists())

    def test_neuer_stand_im_abbild_wird_nachgezogen(self):
        ss.bereitstellen("claude_code", quelle=self.quelle, workspace=self.ws)
        _skill(self.quelle, "anatomy", "bbb222", text="Neu")
        self.assertEqual(ss.bereitstellen("claude_code", quelle=self.quelle, workspace=self.ws), ["anatomy"])
        self.assertIn("Neu", (self._ziel("claude_code") / "SKILL.md").read_text())
        self.assertEqual((self._ziel("claude_code") / ss.MARKE).read_text().strip(), "bbb222")

    def test_gleichnamiger_eigener_skill_wird_nie_ueberschrieben(self):
        eigen = self._ziel("claude_code")
        eigen.mkdir(parents=True)
        (eigen / "SKILL.md").write_text("mein eigener")
        self.assertEqual(ss.bereitstellen("claude_code", quelle=self.quelle, workspace=self.ws), [])
        self.assertEqual((eigen / "SKILL.md").read_text(), "mein eigener")

    def test_ohne_quelle_oder_mit_kaputtem_skill_kein_abbruch(self):
        self.assertEqual(ss.bereitstellen("claude_code", quelle=self.ws / "fehlt", workspace=self.ws), [])
        (self.quelle / "kaputt").mkdir()  # ohne SKILL.md
        self.assertEqual(ss.bereitstellen("claude_code", quelle=self.quelle, workspace=self.ws), ["anatomy"])
        self.assertFalse((self.ws / ".claude/skills/kaputt").exists())

    def test_symlink_als_ziel_wird_nicht_verfolgt(self):
        fremd = self.ws / "fremd"
        fremd.mkdir()
        (self.ws / ".claude/skills").mkdir(parents=True)
        os.symlink(fremd, self._ziel("claude_code"))
        self.assertEqual(ss.bereitstellen("claude_code", quelle=self.quelle, workspace=self.ws), [])
        self.assertEqual(list(fremd.iterdir()), [])


class MitgelieferteKopie(unittest.TestCase):
    """Die geprüfte Kopie im Repo: Herkunft dokumentiert, nichts, was nach außen schickt."""

    def test_anatomy_mit_lizenz_stand_und_ohne_veroeffentlichen(self):
        a = ABBILD / "anatomy"
        for datei in ("SKILL.md", "LICENSE", "STAND", "HERKUNFT.md", "kit/iso-kit.mjs"):
            self.assertTrue((a / datei).is_file(), datei)
        self.assertFalse((a / "scripts/publish.mjs").exists())
        self.assertFalse((a / "references/publish.md").exists())
        self.assertNotIn("gallery", (a / "SKILL.md").read_text().lower())

    def test_kit_spricht_kein_netz(self):
        for datei in (ABBILD / "anatomy" / "kit").rglob("*"):
            if datei.suffix in (".mjs", ".ts", ".tsx", ".js"):
                text = datei.read_text()
                for muster in ("fetch(", "child_process", "XMLHttpRequest", "new Function", "eval("):
                    self.assertNotIn(muster, text, f"{datei.name}: {muster}")


if __name__ == "__main__":
    unittest.main()

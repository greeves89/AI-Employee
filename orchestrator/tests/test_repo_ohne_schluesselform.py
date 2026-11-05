"""Kein schluesselfoermiges Literal im Repo.

Der Sentinel haelt einen Agenten an, sobald dessen Ausgabe einen Text in
Schluesselform enthaelt. Liest ein Agent eine Datei dieses Repos mit einem solchen
Testwert, steht er still, und alle seine Zeitplaene mit ihm: so geschehen am
04.11.2026, gut 21 Stunden lang, ausgeloest durch einen Platzhalter in einem Test.

Testwerte werden deshalb im Quelltext geteilt (``"AK" "IA..."``). Zur Laufzeit ist
der Wert derselbe, in der Datei trifft ihn die Erkennung nicht. Nebenbei faengt
diese Wache auch einen echten Schluessel, der versehentlich eingecheckt wird.
"""
import subprocess
import unittest
from pathlib import Path

from app.core import dlp

WURZEL = Path(__file__).resolve().parents[2]
#: Nur als Bremse gegen Riesendateien: CHANGELOG.md liegt schon ueber 1 MB.
GROESSER_ALS = 20_000_000


def _dateien() -> list[Path]:
    """Die eingecheckten Dateien. Ohne git (etwa im Abbild): alles unter der Wurzel."""
    try:
        ausgabe = subprocess.run(
            ["git", "ls-files", "-z"], cwd=WURZEL, capture_output=True, text=True, check=True,
        ).stdout
        return [WURZEL / n for n in ausgabe.split("\0") if n]
    except (OSError, subprocess.CalledProcessError):
        auslassen = {".git", "node_modules", ".next", "__pycache__", ".venv"}
        return [
            p for p in WURZEL.rglob("*")
            if p.is_file() and not auslassen.intersection(p.parts)
        ]


class TestRepoOhneSchluesselform(unittest.TestCase):
    def test_die_wache_sieht_wirklich_dateien(self):
        """Eine leere Liste waere ein stilles Gruen."""
        namen = {p.name for p in _dateien()}
        self.assertIn("dlp.py", namen)
        self.assertGreater(len(namen), 200)

    def test_keine_datei_enthaelt_schluesselform(self):
        """Auch einzelne Koepfe und Fuesse zaehlen.

        Der Sentinel prueft je Werkzeugergebnis, nicht je Datei: ``cat a b`` setzt
        aus einem Kopf in a und einem Fuss in b einen Block zusammen.
        """
        funde = []
        for pfad in _dateien():
            if not pfad.is_file() or pfad.stat().st_size > GROESSER_ALS:
                continue
            roh = pfad.read_bytes()
            if b"\0" in roh[:8192]:  # Binaerdatei
                continue
            text = roh.decode("utf-8", errors="replace")
            anzahl = len(dlp.find_high_confidence_secrets(text)) + len(dlp.fremde_blockmarken(text))
            if anzahl:
                funde.append(f"{pfad.relative_to(WURZEL)}: {anzahl}")
        self.assertEqual(
            funde, [],
            "Testwert im Quelltext teilen, damit der Sentinel den lesenden Agenten nicht anhaelt",
        )


if __name__ == "__main__":
    unittest.main()

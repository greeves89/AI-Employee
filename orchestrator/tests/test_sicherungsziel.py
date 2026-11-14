"""Sicherungsziel ohne root (Plan Sicherheit 2026-11, Punkt 6).

Am 14.11.2026 brach ``update.sh`` als ``pi`` ab: ``/var/backups/ai-employee`` hatte ein
früheres ``sudo update.sh`` als root mit 700 angelegt. Das Update unterblieb, niemand
sicherte. ``sicherungsziel_waehlen`` weicht jetzt aus — nur ohne root und nur beim
Standardziel. Geprüft mit echter bash und echten Verzeichnisrechten.

MC/DC über (root, Standardziel, beschreibbar).
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

LIB = Path(__file__).resolve().parents[2] / "scripts" / "lib" / "sicherung.sh"


def waehlen(ziel, standard, uid, heim, gesetzt="", mount=""):
    aus = subprocess.run(
        ["bash", "-c", f'. "{LIB}"; sicherungsziel_waehlen "$1" "$2" "$3" "$4" "$5" "$6"', "_",
         str(ziel), str(standard), str(uid), str(heim), gesetzt, mount],
        capture_output=True, text=True, check=True)
    return aus.stdout.strip()


@unittest.skipIf(os.geteuid() == 0, "Rechte-Test braucht einen normalen Nutzer")
class SicherungszielTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.basis = Path(self.tmp.name)
        self.heim = self.basis / "heim"
        self.gesperrt = self.basis / "gesperrt"
        self.gesperrt.mkdir()
        self.gesperrt.chmod(0o500)          # wie root-eigen mit 700: nicht beschreibbar
        self.offen = self.basis / "offen"
        self.offen.mkdir()

    def tearDown(self):
        self.gesperrt.chmod(0o700)
        self.tmp.cleanup()

    def test_standard_nicht_beschreibbar_weicht_aus(self):
        self.assertEqual(waehlen(self.gesperrt, self.gesperrt, 1000, self.heim),
                         f"{self.heim}/ai-employee-sicherungen")

    def test_standard_fehlt_und_eltern_gesperrt_weicht_aus(self):
        ziel = self.gesperrt / "ai-employee"
        self.assertEqual(waehlen(ziel, ziel, 1000, self.heim), f"{self.heim}/ai-employee-sicherungen")

    def test_standard_beschreibbar_bleibt(self):
        self.assertEqual(waehlen(self.offen, self.offen, 1000, self.heim), str(self.offen))

    def test_als_root_nie_ausweichen(self):
        self.assertEqual(waehlen(self.gesperrt, self.gesperrt, 0, self.heim), str(self.gesperrt))

    def test_ausdruecklich_gesetztes_ziel_bleibt(self):
        self.assertEqual(waehlen(self.gesperrt, "/var/backups/ai-employee", 1000, self.heim),
                         str(self.gesperrt))


    def test_gesetztes_ziel_auch_gleich_standard_bleibt(self):
        """--dest /var/backups/ai-employee ist eine Ansage — nicht ausweichen."""
        self.assertEqual(waehlen(self.gesperrt, self.gesperrt, 1000, self.heim, gesetzt="ja"),
                         str(self.gesperrt))

    def test_mit_sicherungsplatte_nie_auf_die_anlage(self):
        self.assertEqual(waehlen(self.gesperrt, self.gesperrt, 1000, self.heim, mount="/mnt/nas"),
                         str(self.gesperrt))

    def test_ohne_home_kein_ausweichen(self):
        self.assertEqual(waehlen(self.gesperrt, self.gesperrt, 1000, ""), str(self.gesperrt))

    def test_gesperrter_unterordner_weicht_aus(self):
        (self.offen / "daily").mkdir()
        (self.offen / "daily").chmod(0o500)
        try:
            self.assertEqual(waehlen(self.offen, self.offen, 1000, self.heim),
                             f"{self.heim}/ai-employee-sicherungen")
        finally:
            (self.offen / "daily").chmod(0o700)


class ZielAusEnv(unittest.TestCase):
    """Update-Sicherung an dasselbe Ziel wie die naechtliche (aus der .env)."""

    def test_env_wert_liest_ziel(self):
        with tempfile.TemporaryDirectory() as d:
            env = Path(d) / ".env"
            env.write_text('X=1\nBACKUP_DIR="/mnt/nas/sicherung"\nBACKUP_MOUNT=/mnt/nas\n', encoding="utf-8")
            aus = subprocess.run(["bash", "-c", f'. "{LIB}"; env_wert "$1" BACKUP_DIR; env_wert "$1" BACKUP_MOUNT', "_", str(env)],
                                 capture_output=True, text=True, check=True).stdout.split()
            self.assertEqual(aus, ["/mnt/nas/sicherung", "/mnt/nas"])

    def test_backup_liest_env_vor_der_auswahl(self):
        text = (LIB.parents[1] / "backup.sh").read_text(encoding="utf-8")
        lesen = text.index('BACKUP_DIR="$(env_wert "${INSTALL_DIR}/.env" BACKUP_DIR)"')
        self.assertLess(lesen, text.index('ZIEL_GESETZT="${BACKUP_DIR:+ja}"'))
        self.assertIn('BACKUP_MOUNT="$(env_wert "${INSTALL_DIR}/.env" BACKUP_MOUNT)"', text)


class UpdateWartetAufSicherung(unittest.TestCase):
    """Ein Update bricht eine laufende Sicherung ab (Neustart der Dienste) — am
    14.11.2026 so geschehen. ``update.sh`` verweigert dann und sagt warum."""

    def _laeuft(self):
        return subprocess.run(["bash", "-c", f'. "{LIB}"; sicherung_laeuft'], capture_output=True).returncode == 0

    def test_erkennt_laufende_sicherung(self):
        import time

        if self._laeuft():
            self.skipTest("auf diesem Rechner laeuft gerade ein backup.sh")
        p = subprocess.Popen(["bash", "-c", "exec -a /opt/x/scripts/backup.sh sleep 20"])
        try:
            time.sleep(0.3)
            self.assertTrue(self._laeuft())
        finally:
            p.kill()
            p.wait()
        self.assertFalse(self._laeuft())

    def test_update_prueft_vor_der_eigenen_sicherung(self):
        text = (LIB.parents[1] / "update.sh").read_text(encoding="utf-8")
        pruefung = text.index('sicherung_laeuft; then')
        self.assertLess(text.index(". ./scripts/lib/sicherung.sh"), pruefung)
        self.assertLess(pruefung, text.index("./scripts/backup.sh \\"))


class UpdateSicherungRaeumtNichtAuf(unittest.TestCase):
    """14.11.2026: Die Sicherung vor einem Update raeumte mit dem Standard (7) eine
    NAS-Ablage auf, die die naechtliche Sicherung mit 14 fuehrt — 8 Tagessicherungen weg."""

    def setUp(self):
        self.backup = (LIB.parents[1] / "backup.sh").read_text(encoding="utf-8")
        self.update = (LIB.parents[1] / "update.sh").read_text(encoding="utf-8")

    def test_update_sagt_nicht_aufraeumen(self):
        self.assertIn("BACKUP_NICHT_AUFRAEUMEN=1 ./scripts/backup.sh", self.update)

    def test_backup_raeumt_dann_nicht_auf(self):
        block = self.backup.split('if [ "${BACKUP_NICHT_AUFRAEUMEN:-0}" = "1" ]; then', 1)[1].split("fi", 1)[0]
        vorher, nachher = block.split("else", 1)
        self.assertNotIn("aufraeumen", vorher)
        self.assertIn('aufraeumen daily "$KEEP_DAILY"', nachher)
        self.assertEqual(self.backup.count('aufraeumen daily "$KEEP_DAILY"'), 1)

    def test_aufbewahrung_auch_aus_env(self):
        self.assertLess(self.backup.index('ENV_DATEI="${INSTALL_DIR}/.env"'),
                        self.backup.index('env_wert "$ENV_DATEI" BACKUP_KEEP_DAILY'))
        self.assertIn('env_wert "$ENV_DATEI" BACKUP_KEEP_WEEKLY', self.backup)


class BackupSkriptRuftAuf(unittest.TestCase):
    def test_backup_nutzt_die_auswahl(self):
        text = (LIB.parents[1] / "backup.sh").read_text(encoding="utf-8")
        self.assertIn('sicherungsziel_waehlen "$BACKUP_DIR" "$STANDARD_ZIEL"', text)
        self.assertIn('"${ZIEL_GESETZT:-}" "${BACKUP_MOUNT:-}"', text)
        self.assertIn('--dest) BACKUP_DIR="$2"; ZIEL_GESETZT=ja', text)


if __name__ == "__main__":
    unittest.main()

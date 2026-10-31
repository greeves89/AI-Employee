"""update.sh startet sich neu, wenn der Pull eine neue Fassung von sich selbst bringt.

Beim Umstieg auf die Registry-Abbilder (v1.364) lief auf einer Anlage nach
``git pull`` die ALTE Fassung weiter — bash liest aus der bereits geoeffneten
Datei — und baute lokal, statt die Abbilder zu holen. Nachgestellt mit einem
echten Git-Repo, einem Remote mit geaenderter update.sh und Attrappen fuer
docker, curl und die Sicherung.
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

_ATTRAPPE = "#!/usr/bin/env bash\necho \"$(basename \"$0\") $*\" >> \"$ATTRAPPE_LOG\"\nexit 0\n"


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"})


class NeueFassungLaeuft(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        quelle = self.tmp / "quelle"
        (quelle / "scripts" / "lib").mkdir(parents=True)
        shutil.copy(REPO / "scripts" / "update.sh", quelle / "scripts" / "update.sh")
        shutil.copy(REPO / "scripts" / "lib" / "abbilder.sh", quelle / "scripts" / "lib" / "abbilder.sh")
        shutil.copy(REPO / "scripts" / "lib" / "sicherung.sh", quelle / "scripts" / "lib" / "sicherung.sh")
        (quelle / "scripts" / "backup.sh").write_text("#!/bin/sh\necho SICHERUNG >> \"$ATTRAPPE_LOG\"\n")
        (quelle / "scripts" / "backup.sh").chmod(0o755)
        (quelle / "VERSION").write_text("1.0.0\n")
        _git("init", "-q", "-b", "main", cwd=quelle)
        _git("add", ".", cwd=quelle)
        _git("commit", "-q", "-m", "alt", cwd=quelle)
        self.remote = self.tmp / "remote.git"
        _git("clone", "-q", "--bare", str(quelle), str(self.remote), cwd=self.tmp)
        self.anlage = self.tmp / "anlage"
        _git("clone", "-q", str(self.remote), str(self.anlage), cwd=self.tmp)
        self.quelle = quelle
        bin_ = self.tmp / "bin"
        bin_.mkdir()
        for name in ("docker", "curl"):
            (bin_ / name).write_text(_ATTRAPPE)
            (bin_ / name).chmod(0o755)
        self.log = self.tmp / "log"
        self.env = {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}", "ATTRAPPE_LOG": str(self.log),
                    "AI_EMPLOYEE_STATUS_DIR": str(self.tmp / "status")}
        for k in ("UPDATE_NEU_GESTARTET", "UPDATE_VORHER_COMMIT", "UPDATE_OHNE_SICHERUNG"):
            self.env.pop(k, None)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _neue_fassung_veroeffentlichen(self, aendern: bool):
        skript = self.quelle / "scripts" / "update.sh"
        text = skript.read_text()
        if aendern:
            text = text.replace("set -euo pipefail\n", "set -euo pipefail\necho NEUE_FASSUNG_LAEUFT\n", 1)
        skript.write_text(text)
        (self.quelle / "VERSION").write_text("1.1.0\n")
        _git("commit", "-q", "-am", "neu", cwd=self.quelle)
        _git("push", "-q", str(self.remote), "main", cwd=self.quelle)

    def _update(self):
        return subprocess.run(["bash", "scripts/update.sh"], cwd=self.anlage, env=self.env,
                              capture_output=True, text=True, timeout=120)

    def test_geaenderte_fassung_wird_neu_gestartet_ohne_doppelte_sicherung(self):
        self._neue_fassung_veroeffentlichen(aendern=True)
        lauf = self._update()
        self.assertEqual(lauf.returncode, 0, lauf.stdout + lauf.stderr)
        self.assertIn("starte die neue Fassung", lauf.stdout)
        self.assertIn("NEUE_FASSUNG_LAEUFT", lauf.stdout)
        self.assertIn("schon erledigt", lauf.stdout)
        aufrufe = self.log.read_text().splitlines()
        self.assertEqual(aufrufe.count("SICHERUNG"), 1, "die Sicherung laeuft nur einmal")
        self.assertIn("docker compose pull -q orchestrator frontend embedding-service stt-service", aufrufe)
        self.assertEqual((self.anlage / "VERSION").read_text().strip(), "1.1.0")

    def test_unveraenderte_fassung_laeuft_einfach_weiter(self):
        self._neue_fassung_veroeffentlichen(aendern=False)
        lauf = self._update()
        self.assertEqual(lauf.returncode, 0, lauf.stdout + lauf.stderr)
        self.assertNotIn("starte die neue Fassung", lauf.stdout)


if __name__ == "__main__":
    unittest.main()

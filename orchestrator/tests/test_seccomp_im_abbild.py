"""Das Seccomp-Profil der Agenten gehört ins Orchestrator-Abbild.

Bis 1.426.0 kam es nur über den Code-Mount vom Host in den Orchestrator. Der
Docker-Wächter läuft aus demselben Abbild OHNE Mount und kannte es nicht — er
hätte jedes Anlegen eines Agenten mit Profil abgelehnt.
"""

import unittest
from pathlib import Path

ORCH = Path(__file__).resolve().parents[1]


class SeccompImAbbild(unittest.TestCase):
    def test_dockerfile_kopiert_das_profil(self):
        self.assertIn("COPY seccomp-profile.json ./seccomp-profile.json", (ORCH / "Dockerfile").read_text())
        self.assertTrue((ORCH / "seccomp-profile.json").is_file())

    def test_dockerignore_schliesst_es_nicht_aus(self):
        ignore = ORCH / ".dockerignore"
        if ignore.exists():
            self.assertNotIn("seccomp-profile.json", ignore.read_text())


if __name__ == "__main__":
    unittest.main()

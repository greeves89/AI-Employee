"""Fertige Abbilder aus der Registry (#889).

Geprüft wird das Verhalten von ``scripts/lib/abbilder.sh`` (gemeinsam für
setup.sh und update.sh) mit einer Docker-Attrappe, und dass Workflow, Compose
und Skript dieselben Dienste und dieselbe Versionsnummer meinen.
"""

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HILFSDATEI = REPO / "scripts" / "lib" / "abbilder.sh"

_DOCKER_ATTRAPPE = r"""#!/usr/bin/env bash
echo "$*" >> "$ATTRAPPE_LOG"
case "$*" in
  "compose pull"*) [ "${PULL_GEHT:-1}" = 1 ] || exit 1 ;;
  "pull "*) [ "${PULL_GEHT:-1}" = 1 ] || exit 1 ;;
esac
exit 0
"""


class AbbilderBereitstellen(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.anlage = self.tmp / "anlage"
        self.anlage.mkdir()
        (self.anlage / "VERSION").write_text("1.400.2\n")
        (self.anlage / ".env").write_text("POSTGRES_PASSWORD=x\n")
        bin_ = self.tmp / "bin"
        bin_.mkdir()
        (bin_ / "docker").write_text(_DOCKER_ATTRAPPE)
        (bin_ / "docker").chmod(0o755)
        self.log = self.tmp / "docker.log"
        self.env = {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}", "ATTRAPPE_LOG": str(self.log)}
        self.env.pop("AI_EMPLOYEE_REGISTRY", None)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _lauf(self, lokal="0", **env):
        return subprocess.run(
            ["bash", "-c", f'set -euo pipefail; source "{HILFSDATEI}"; abbilder_bereitstellen "$1"; echo "QUELLE=$ABBILD_QUELLE"',
             "_", lokal],
            cwd=self.anlage, env={**self.env, **env}, capture_output=True, text=True, timeout=30,
        )

    def _aufrufe(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_registry_erreichbar_holt_alles_und_baut_nichts(self):
        lauf = self._lauf()
        self.assertEqual(lauf.returncode, 0, lauf.stderr)
        self.assertIn("QUELLE=registry", lauf.stdout)
        aufrufe = self._aufrufe()
        self.assertIn("compose pull -q orchestrator frontend embedding-service stt-service", aufrufe)
        self.assertIn("pull -q ghcr.io/greeves89/ai-employee-agent:1.400.2", aufrufe)
        self.assertIn("tag ghcr.io/greeves89/ai-employee-agent:1.400.2 ai-employee-agent:latest", aufrufe)
        self.assertFalse([a for a in aufrufe if a.startswith("build") or a == "compose build"])

    def test_ohne_registry_wird_lokal_gebaut(self):
        lauf = self._lauf(PULL_GEHT="0")
        self.assertEqual(lauf.returncode, 0, lauf.stderr)
        self.assertIn("QUELLE=lokal", lauf.stdout)
        aufrufe = self._aufrufe()
        self.assertIn("build -t ai-employee-agent:latest ./agent", aufrufe)
        self.assertIn("compose build", aufrufe)

    def test_lokal_bauen_fragt_die_registry_gar_nicht(self):
        self._lauf(lokal="1")
        self.assertFalse([a for a in self._aufrufe() if "pull" in a])

    def test_version_steht_genau_einmal_in_der_env(self):
        self._lauf()
        (self.anlage / "VERSION").write_text("1.400.3\n")
        self._lauf()
        zeilen = [z for z in (self.anlage / ".env").read_text().splitlines() if z.startswith("AI_EMPLOYEE_VERSION=")]
        self.assertEqual(zeilen, ["AI_EMPLOYEE_VERSION=1.400.3"])
        self.assertIn("POSTGRES_PASSWORD=x", (self.anlage / ".env").read_text())

    def test_eigene_registry(self):
        self._lauf(AI_EMPLOYEE_REGISTRY="registry.example.invalid/ki")
        self.assertIn("pull -q registry.example.invalid/ki/ai-employee-agent:1.400.2", self._aufrufe())


class EineListeEineNummer(unittest.TestCase):
    """Workflow, Compose und Skript meinen dieselben Dienste und dieselbe Version."""

    def setUp(self):
        self.workflow = (REPO / ".github" / "workflows" / "images.yml").read_text()
        self.compose = (REPO / "docker-compose.yml").read_text()
        self.skript = HILFSDATEI.read_text()

    def _skript_dienste(self) -> set[str]:
        return set(re.search(r'^ABBILD_DIENSTE="([^"]+)"', self.skript, re.M).group(1).split())

    def test_workflow_baut_genau_die_dienste_des_skripts_plus_agent(self):
        gebaut = set(re.findall(r"- \{ name: ([a-z-]+), context:", self.workflow))
        self.assertEqual(gebaut, self._skript_dienste() | {"agent"})
        gefuehrt = set(re.search(r"dienst: \[([^\]]+)\]", self.workflow).group(1).replace(" ", "").split(","))
        self.assertEqual(gefuehrt, gebaut)

    def test_jeder_gebaute_compose_dienst_hat_sein_registry_abbild(self):
        for dienst in self._skript_dienste():
            with self.subTest(dienst=dienst):
                self.assertIn(
                    f"image: ${{AI_EMPLOYEE_REGISTRY:-ghcr.io/greeves89}}/ai-employee-{dienst}:${{AI_EMPLOYEE_VERSION:-latest}}",
                    self.compose,
                )

    def test_beide_architekturen(self):
        self.assertIn("arch: amd64", self.workflow)
        self.assertIn("arch: arm64", self.workflow)

    def test_tag_label_und_version_muessen_uebereinstimmen(self):
        self.assertIn('"${GITHUB_REF_NAME#v}" = "$VERSION"', self.workflow)
        self.assertIn('"$LABEL" = "$VERSION"', self.workflow)

    def test_versionslabel_steht_am_ende_des_agenten_dockerfiles(self):
        zeilen = [z for z in (REPO / "agent" / "Dockerfile").read_text().splitlines() if z.strip() and not z.startswith("#")]
        self.assertTrue(zeilen[-1].startswith("LABEL ai-employee.version="), zeilen[-1])

    def test_skripte_nutzen_die_gemeinsame_funktion(self):
        for name in ("setup.sh", "update.sh"):
            with self.subTest(skript=name):
                self.assertIn("abbilder_bereitstellen", (REPO / "scripts" / name).read_text())


if __name__ == "__main__":
    unittest.main()

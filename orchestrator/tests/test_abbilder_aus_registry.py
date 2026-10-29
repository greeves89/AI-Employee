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
[ "$*" = "compose build" ] && echo "APP_VERSION=${APP_VERSION:-}" >> "$ATTRAPPE_LOG"
case "$*" in
  "compose pull"*) [ "${PULL_GEHT:-1}" = 1 ] || exit 1 ;;
  "pull "*) [ "${PULL_GEHT:-1}" = 1 ] || exit 1 ;;
  "image inspect "*)
    [ "${DIGEST_DA:-1}" = 1 ] || exit 0
    ref="${@: -1}"
    echo "${ref%:*}@sha256:0000000000000000000000000000000000000000000000000000000000000001"
    ;;
esac
exit 0
"""

_COSIGN_ATTRAPPE = r"""#!/usr/bin/env bash
echo "cosign $*" >> "$ATTRAPPE_LOG"
[ "${COSIGN_GEHT:-1}" = 1 ] || exit 1
exit 0
"""

DIGEST = "sha256:0000000000000000000000000000000000000000000000000000000000000001"


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
        # cosign als Attrappe mitliefern, damit das Ergebnis nicht davon
        # abhaengt, ob die Maschine cosign installiert hat (#938).
        (bin_ / "cosign").write_text(_COSIGN_ATTRAPPE)
        (bin_ / "cosign").chmod(0o755)
        self.bin = bin_
        self.log = self.tmp / "docker.log"
        self.env = {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}", "ATTRAPPE_LOG": str(self.log)}
        for name in ("AI_EMPLOYEE_REGISTRY", "AI_EMPLOYEE_SIGNATUR_PRUEFEN", "AI_EMPLOYEE_SIGNATUR_IDENTITAET"):
            self.env.pop(name, None)

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

    def test_lokaler_bau_bekommt_die_version(self):
        """Sonst steht im Frontend-Bundle "dev" und der Versionsabgleich der
        Oberflaeche (update-banner.tsx) meldet falsch."""
        self._lauf(lokal="1")
        aufrufe = self._aufrufe()
        self.assertIn("APP_VERSION=1.400.2", aufrufe[aufrufe.index("compose build") + 1:])

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


class AbbildSignaturen(AbbilderBereitstellen):
    """Signaturpruefung der geholten Abbilder (#938).

    Die Abbilder sind oeffentlich und werden per TAG geholt. Wer ein Konto oder
    den Bau-Vorgang uebernimmt, kann unter einem gueltigen Tag ein fremdes
    Abbild ablegen; ohne Pruefung wuerde jede Anlage es beim naechsten Update
    starten. Die Klasse erbt die Grundtests: sie laufen damit ein zweites Mal
    MIT vorhandenem cosign.
    """

    DIENSTE = ("orchestrator", "frontend", "embedding-service", "stt-service", "agent")

    def _cosign_aufrufe(self):
        return [a for a in self._aufrufe() if a.startswith("cosign ")]

    def _identitaet(self):
        return re.search(r"--certificate-identity-regexp (\S+)", self._cosign_aufrufe()[0]).group(1)

    def test_signatur_wird_geprueft_bevor_das_abbild_benutzt_wird(self):
        lauf = self._lauf()
        self.assertEqual(lauf.returncode, 0, lauf.stderr)
        self.assertIn("QUELLE=registry", lauf.stdout)
        aufrufe = self._aufrufe()
        gepruefte = [a.split()[-1] for a in self._cosign_aufrufe()]
        self.assertEqual(
            gepruefte,
            [f"ghcr.io/greeves89/ai-employee-{d}@{DIGEST}" for d in self.DIENSTE],
        )
        taggen = [i for i, a in enumerate(aufrufe) if a.startswith("tag ")]
        letzte_pruefung = max(i for i, a in enumerate(aufrufe) if a.startswith("cosign "))
        self.assertTrue(taggen, aufrufe)
        self.assertLess(letzte_pruefung, taggen[0], aufrufe)

    def test_geprueft_wird_der_digest_nicht_der_tag(self):
        """Ein Tag kann nach der Pruefung auf ein anderes Abbild zeigen."""
        self._lauf()
        for aufruf in self._cosign_aufrufe():
            self.assertIn(f"@{DIGEST}", aufruf.split()[-1], aufruf)
            self.assertNotIn(":1.400.2", aufruf.split()[-1], aufruf)

    def test_jede_pruefung_verlangt_die_zusage_fuer_genau_diesen_dienst(self):
        """Ohne sie ist nur der Digest signiert, nicht WOFUER: ein signiertes
        Manifest liesse sich samt Signatur auf den Tag eines anderen Dienstes
        kopieren, und der Agent liefe als Orchestrator — mit dessen
        Zugaengen."""
        self._lauf()
        aufrufe = self._cosign_aufrufe()
        self.assertEqual(len(aufrufe), len(self.DIENSTE))
        for dienst, aufruf in zip(self.DIENSTE, aufrufe):
            with self.subTest(dienst=dienst):
                self.assertIn(f"-a dienst={dienst} ", aufruf + " ")
                self.assertIn(f"/ai-employee-{dienst}@", aufruf)

    def test_ungueltige_signatur_wird_dauerhaft_vermerkt(self):
        """Der Orchestrator holt das Agenten-Abbild selbst nach, wenn es lokal
        fehlt (#889). Ohne diesen Vermerk kaeme genau das hier verworfene Abbild
        auf diesem Weg wieder herein, sobald ein "docker image prune" den lokalen
        Bau wegnimmt — die Ablehnung waere nicht dauerhaft."""
        lauf = self._lauf(COSIGN_GEHT="0")
        self.assertEqual(lauf.returncode, 0, lauf.stderr)
        self.assertIn(
            "AI_EMPLOYEE_ABBILD_ABGELEHNT=1.400.2",
            (self.anlage / ".env").read_text(),
        )

    def test_geprüfte_version_hebt_einen_alten_vermerk_auf(self):
        """Sonst blieb die Anlage nach einem einzigen Fehlschlag dauerhaft am
        lokalen Bau haengen, auch nach der naechsten, geprueften Freigabe."""
        env = self.anlage / ".env"
        env.write_text(env.read_text() + "AI_EMPLOYEE_ABBILD_ABGELEHNT=1.400.1\n")
        lauf = self._lauf()
        self.assertEqual(lauf.returncode, 0, lauf.stderr)
        self.assertIn("QUELLE=registry", lauf.stdout)
        self.assertIn("AI_EMPLOYEE_ABBILD_ABGELEHNT=\n", env.read_text())

    def test_ungueltige_signatur_verhindert_die_benutzung_und_baut_lokal(self):
        lauf = self._lauf(COSIGN_GEHT="0")
        self.assertEqual(lauf.returncode, 0, lauf.stderr)
        self.assertIn("QUELLE=lokal", lauf.stdout)
        aufrufe = self._aufrufe()
        self.assertFalse([a for a in aufrufe if a.startswith("tag ")], aufrufe)
        self.assertIn("build -t ai-employee-agent:latest ./agent", aufrufe)
        self.assertIn("compose build", aufrufe)
        # Nach dem ersten Fehlschlag wird nicht weiter geprueft.
        self.assertEqual(len(self._cosign_aufrufe()), 1)

    def test_abgelehnte_abbilder_werden_entfernt(self):
        """`docker compose pull` hat sie unter genau den Referenzen abgelegt,
        die docker-compose.yml als image: fuehrt. Bleiben sie liegen, startet
        ein spaeteres `docker compose up -d` von Hand das abgelehnte Abbild —
        der lokale Bau ueberschreibt nur, wenn er auch durchlaeuft."""
        self._lauf(COSIGN_GEHT="0")
        entfernt = [a for a in self._aufrufe() if a.startswith("image rm")]
        self.assertEqual(
            entfernt,
            [f"image rm -f ghcr.io/greeves89/ai-employee-{d}:1.400.2" for d in self.DIENSTE],
        )

    def test_fehlender_digest_fuehrt_zum_lokalen_bau(self):
        """Kein Digest heisst: nicht pruefbar. Dann gilt dasselbe wie bei einer
        ungueltigen Signatur — sonst waere die Pruefung durch ein stilles
        Ausbleiben der Digest-Antwort umgehbar."""
        lauf = self._lauf(DIGEST_DA="0")
        self.assertIn("QUELLE=lokal", lauf.stdout)
        self.assertEqual(self._cosign_aufrufe(), [])
        self.assertIn("compose build", self._aufrufe())

    def test_identitaet_ist_an_konto_repo_workflow_und_version_gebunden(self):
        self._lauf()
        aufruf = self._cosign_aufrufe()[0]
        self.assertIn("--certificate-oidc-issuer https://token.actions.githubusercontent.com", aufruf)
        muster = self._identitaet()
        passend = "https://github.com/greeves89/AI-Employee/.github/workflows/images.yml@refs/tags/v1.400.2"
        self.assertRegex(passend, muster)
        # Konto-Name darf in der Schreibweise abweichen (ghcr.io schreibt klein,
        # das Zertifikat traegt die GitHub-Schreibweise).
        self.assertRegex(passend.replace("greeves89", "Greeves89"), muster)
        for name, falsch in {
            "Hauptzweig statt Versions-Tag": passend.replace("refs/tags/v1.400.2", "refs/heads/main"),
            "fremdes Konto": passend.replace("/greeves89/", "/jemand-anders/"),
            "anderes Repo desselben Kontos": passend.replace("/AI-Employee/", "/anderes-repo/"),
            "andere Version": passend.replace("v1.400.2", "v1.400.25"),
            "anderer Workflow": passend.replace("images.yml", "etwas-anderes.yml"),
            "andere Datei, nur andere Schreibweise": passend.replace("images.yml", "Images.yml"),
            "angehaengt": passend + ".evil",
        }.items():
            with self.subTest(fall=name):
                self.assertNotRegex(falsch, muster)

    def test_verschachtelter_registry_pfad_nennt_das_richtige_konto(self):
        """ghcr.io/<konto>/<unterpfad>: das Konto ist die ERSTE Komponente.
        Mit der letzten wuerde die Signatur eines fremden Kontos akzeptiert."""
        self._lauf(AI_EMPLOYEE_REGISTRY="ghcr.io/greeves89/spiegel")
        self.assertIn("github\\.com/(?i:greeves89/", self._identitaet())

    def test_ohne_cosign_bleibt_es_beim_bisherigen_verhalten(self):
        """cosign ist eine neue Abhaengigkeit — eine bestehende Anlage ohne
        cosign darf nicht ploetzlich alles selbst bauen."""
        (self.bin / "cosign").unlink()
        if shutil.which("cosign", path=self.env["PATH"]):
            self.skipTest("cosign ist auf dieser Maschine systemweit installiert")
        lauf = self._lauf()
        self.assertIn("QUELLE=registry", lauf.stdout)
        self.assertIn("cosign ist nicht installiert", lauf.stdout)
        self.assertIn("tag ghcr.io/greeves89/ai-employee-agent:1.400.2 ai-employee-agent:latest", self._aufrufe())

    def test_pruefung_abschaltbar(self):
        lauf = self._lauf(AI_EMPLOYEE_SIGNATUR_PRUEFEN="0")
        self.assertIn("QUELLE=registry", lauf.stdout)
        self.assertEqual(self._cosign_aufrufe(), [])

    def test_nur_ausdrueckliches_aus_schaltet_aus(self):
        """Ein Sicherheitsschalter, bei dem PRUEFEN=true still "nicht pruefen"
        bedeutet, schaltet sich beim ersten gutgemeinten Eintrag selbst ab."""
        for wert in ("true", "ja", "1", "on"):
            with self.subTest(wert=wert):
                self.log.unlink(missing_ok=True)
                self._lauf(AI_EMPLOYEE_SIGNATUR_PRUEFEN=wert)
                self.assertEqual(len(self._cosign_aufrufe()), len(self.DIENSTE))

    def test_schalter_wirkt_auch_aus_der_env_datei(self):
        """setup.sh/update.sh laden .env nicht — docker compose macht das selbst.
        Das Handbuch nennt aber genau .env als Ort dieser Angaben."""
        (self.anlage / ".env").write_text("POSTGRES_PASSWORD=x\nAI_EMPLOYEE_SIGNATUR_PRUEFEN=0\n")
        lauf = self._lauf()
        self.assertIn("QUELLE=registry", lauf.stdout)
        self.assertEqual(self._cosign_aufrufe(), [])

    def test_eigener_spiegel_aus_der_env_datei_baut_nicht_ploetzlich_lokal(self):
        """Der Bruch, den eine Pruefung gegen die falsche Registry ausloest:
        Compose holt vom Spiegel, geprueft wuerde ghcr.io — dort liegt nichts,
        und jede Spiegel-Anlage baute ab sofort bei JEDEM Update alles selbst
        (20 bis 40 Minuten auf kleiner Hardware)."""
        (self.anlage / ".env").write_text(
            "POSTGRES_PASSWORD=x\nAI_EMPLOYEE_REGISTRY=registry.example.invalid/ki\n"
        )
        lauf = self._lauf()
        self.assertIn("QUELLE=registry", lauf.stdout)
        self.assertIn("pull -q registry.example.invalid/ki/ai-employee-agent:1.400.2", self._aufrufe())
        self.assertEqual(self._cosign_aufrufe(), [])
        self.assertIn("werden NICHT geprueft", lauf.stdout)

    def test_eigene_registry_ohne_identitaet_wird_nicht_geprueft(self):
        lauf = self._lauf(AI_EMPLOYEE_REGISTRY="registry.example.invalid/ki")
        self.assertIn("QUELLE=registry", lauf.stdout)
        self.assertEqual(self._cosign_aufrufe(), [])
        self.assertIn("werden NICHT geprueft", lauf.stdout)

    def test_eigene_registry_mit_eigener_identitaet_wird_geprueft(self):
        lauf = self._lauf(
            AI_EMPLOYEE_REGISTRY="registry.example.invalid/ki",
            AI_EMPLOYEE_SIGNATUR_IDENTITAET="^https://example\\.invalid/bau$",
        )
        self.assertIn("QUELLE=registry", lauf.stdout)
        aufrufe = self._cosign_aufrufe()
        self.assertEqual(len(aufrufe), len(self.DIENSTE), aufrufe)
        self.assertIn("--certificate-identity-regexp ^https://example\\.invalid/bau$", aufrufe[0])
        self.assertIn(f"registry.example.invalid/ki/ai-employee-agent@{DIGEST}", aufrufe[-1])

    def test_unerwarteter_kontoname_wird_nicht_ins_muster_gebaut(self):
        """Der Konto-Name landet in einem Regexp. Metazeichen wuerden dessen
        Bedeutung aendern — dann lieber nicht pruefen als falsch pruefen."""
        lauf = self._lauf(AI_EMPLOYEE_REGISTRY="ghcr.io/ko.*to+")
        self.assertIn("QUELLE=registry", lauf.stdout)
        self.assertEqual(self._cosign_aufrufe(), [])
        self.assertIn("keinen gueltigen Konto-Namen", lauf.stdout)

    def test_ohne_version_datei_wird_nicht_versionsgebunden_geprueft(self):
        """Ohne VERSION ist die Version "latest"; ein an "vlatest" gebundenes
        Muster passte nie und jede Anlage baute lokal."""
        (self.anlage / "VERSION").unlink()
        lauf = self._lauf()
        self.assertIn("QUELLE=registry", lauf.stdout)
        self.assertEqual(self._cosign_aufrufe(), [])
        self.assertIn("ohne VERSION-Datei", lauf.stdout)


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

    def test_der_registry_bau_backt_die_version_ins_bundle(self):
        """Das Gegenstueck zu test_lokaler_bau_bekommt_die_version — fuer den
        Weg, den die Anlage seit #889 im NORMALFALL nimmt.

        Lokal setzt abbilder.sh ``APP_VERSION`` vor ``docker compose build``.
        Werden die Abbilder dagegen fertig geholt, hat die Anlage gar nichts
        mehr gebaut: dann entscheidet allein dieser Workflow, was im Bundle
        steht. Fehlt hier das Build-Arg, greift der Vorgabewert ``dev`` aus
        dem Frontend-Dockerfile, der Abgleich in update-banner.tsx scheitert
        still an der SEMVER-Pruefung, und der Hinweis "Veraltete Oberflaeche"
        erscheint nach einem Update nie wieder. Genau der Vorfall vom
        21.08.2026, nur auf dem neuen Weg."""
        self.assertIn("APP_VERSION=${{ needs.version.outputs.tag }}", self.workflow)

    def test_tag_label_und_version_muessen_uebereinstimmen(self):
        self.assertIn('"${GITHUB_REF_NAME#v}" = "$VERSION"', self.workflow)
        self.assertIn('"$LABEL" = "$VERSION"', self.workflow)

    def test_versionslabel_steht_am_ende_des_agenten_dockerfiles(self):
        zeilen = [z for z in (REPO / "agent" / "Dockerfile").read_text().splitlines() if z.strip() and not z.startswith("#")]
        self.assertTrue(zeilen[-1].startswith("LABEL ai-employee.version="), zeilen[-1])

    def test_workflow_signiert_jedes_manifest_keyless(self):
        """Ohne diese drei Zeilen gibt es nichts zu pruefen: die Anlage wuerde
        die Signatur vergeblich suchen und ab sofort immer lokal bauen."""
        self.assertIn("id-token: write", self.workflow)
        self.assertIn(
            'cosign sign --yes -a "dienst=${{ matrix.dienst }}" "${IMAGE}@${DIGEST}"',
            self.workflow,
        )
        self.assertIn("cosign verify", self.workflow)

    def test_signatur_sagt_fuer_welchen_dienst_sie_gilt(self):
        """Die Anlage verlangt die Zusage dienst=<name> (siehe
        test_jede_pruefung_verlangt_die_zusage_fuer_genau_diesen_dienst).
        Fehlt sie beim Signieren, schlaegt jede Pruefung fehl und alle Anlagen
        bauen ab sofort lokal."""
        self.assertEqual(
            self.workflow.count('-a "dienst=${{ matrix.dienst }}"'),
            3,  # einmal signieren, zweimal gegenpruefen
            self.workflow,
        )

    def test_workflow_prueft_mit_dem_muster_der_anlage_gegen(self):
        """Sonst kann die Signatur im Bau-Lauf gueltig sein und auf der Anlage
        trotzdem abgelehnt werden — bemerkt wuerde das erst dort."""
        # Nicht als Teilstring: "gegengeprueft. scripts/lib/abbilder.sh" im
        # Kopfkommentar wuerde sonst mitzaehlen und der Test blieb gruen,
        # obwohl die Gegenprobe entfernt waere.
        self.assertRegex(self.workflow, r"(?m)^\s+\. scripts/lib/abbilder\.sh$")
        self.assertIn('abbilder_identitaet "${{ needs.version.outputs.registry }}"', self.workflow)

    def test_signiert_wird_der_digest_des_manifests(self):
        """Ein Tag zeigt morgen vielleicht auf ein anderes Abbild."""
        self.assertIn(
            """DIGEST="$(docker buildx imagetools inspect --format '{{.Manifest.Digest}}'""",
            self.workflow,
        )
        self.assertRegex(self.workflow, r'\[\[ "\$DIGEST" =~ \^sha256:')

    def test_cosign_installer_ist_auf_einen_commit_gepinnt(self):
        """Wie die Docker-Actions (v1.364.1): ein Tag ist verschiebbar."""
        self.assertRegex(self.workflow, r"uses: sigstore/cosign-installer@[0-9a-f]{40} # v\d+")

    def test_pruefung_und_skript_meinen_denselben_aussteller(self):
        self.assertIn("https://token.actions.githubusercontent.com", self.workflow)
        self.assertIn("https://token.actions.githubusercontent.com", self.skript)

    def test_skripte_nutzen_die_gemeinsame_funktion(self):
        for name in ("setup.sh", "update.sh"):
            with self.subTest(skript=name):
                self.assertIn("abbilder_bereitstellen", (REPO / "scripts" / name).read_text())


if __name__ == "__main__":
    unittest.main()

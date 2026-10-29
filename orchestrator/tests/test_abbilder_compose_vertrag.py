"""Ein Abbild fuer alle Anlagen: der Vertrag zwischen Compose, Dockerfile und .env (#889).

Zwei Fehlerklassen, die beide STILL sind — nichts bricht, es wirkt nur nicht:

1. Ein Bau-Argument, das das Dockerfile nicht als ``ARG`` annimmt. Docker warnt
   einmal und baut weiter. ``deploy/docker-compose.prod.yml`` uebergab so
   jahrelang ``NEXT_PUBLIC_API_URL: https://${DOMAIN}`` — mit der klaren Absicht,
   die Domain ins Bundle zu backen, und ohne jede Wirkung.
2. Ein Schalter, den die Bereitstellung aus ``.env`` liest, den aber
   ``.env.example`` nicht nennt. Die Upgrade-Anleitung schickt den Betreiber
   ausdruecklich zum Vergleich mit ``.env.example`` — was dort fehlt, findet er nie.
   Genau so war ``AI_EMPLOYEE_REGISTRY`` (eigener Spiegel) nicht auffindbar.
"""
import pathlib
import re
import unittest

import yaml

WURZEL = pathlib.Path(__file__).resolve().parents[2]
COMPOSE_DATEIEN = [
    "docker-compose.yml",
    "docker-compose.community.yml",
    "docker-compose.aiemployee.yml",
    "deploy/docker-compose.prod.yml",
]


def _dienste(pfad):
    daten = yaml.safe_load(pfad.read_text()) or {}
    return daten.get("services") or {}


class BauArgumenteSindDeklariert(unittest.TestCase):
    def test_jedes_bau_argument_steht_als_ARG_im_dockerfile(self):
        gefunden = 0
        for name in COMPOSE_DATEIEN:
            pfad = WURZEL / name
            if not pfad.exists():
                continue
            for dienst, spec in _dienste(pfad).items():
                bau = (spec or {}).get("build")
                if not isinstance(bau, dict):
                    continue
                args = bau.get("args") or {}
                if not args:
                    continue
                kontext = bau.get("context") or "."
                dockerfile = WURZEL / kontext.lstrip("./") / (bau.get("dockerfile") or "Dockerfile")
                if not dockerfile.exists():
                    continue
                deklariert = set(re.findall(r"^ARG\s+([A-Za-z0-9_]+)", dockerfile.read_text(), re.M))
                for schluessel in (args if isinstance(args, dict) else {}):
                    gefunden += 1
                    self.assertIn(
                        schluessel, deklariert,
                        f"{name}: Dienst {dienst} uebergibt das Bau-Argument {schluessel}, "
                        f"aber {dockerfile.relative_to(WURZEL)} nimmt es nicht an — wirkungslos.",
                    )
        # Ohne diese Zusage wuerde der Test auch bestehen, wenn gar keine
        # Bau-Argumente mehr gefunden werden (z. B. nach einem Umbau der Dateinamen).
        self.assertGreater(gefunden, 0, "Keine Bau-Argumente geprueft — prueft der Test noch etwas?")

    def test_das_frontend_backt_keine_adresse_ein(self):
        """Ein gemeinsames Abbild darf keine anlagenspezifische Adresse enthalten;
        die Adresse ergibt sich im Browser (frontend/src/lib/config.ts)."""
        for name in COMPOSE_DATEIEN:
            pfad = WURZEL / name
            if not pfad.exists():
                continue
            for dienst, spec in _dienste(pfad).items():
                bau = (spec or {}).get("build")
                args = bau.get("args") or {} if isinstance(bau, dict) else {}
                umgebung = (spec or {}).get("environment") or {}
                if isinstance(umgebung, list):
                    umgebung = {e.split("=", 1)[0]: None for e in umgebung if isinstance(e, str)}
                # Laufzeitwerte zaehlen mit: in der Community-Zusammenstellung
                # standen die beiden Adressen unter "environment:" — auf einem
                # fertigen Abbild genauso wirkungslos, denn Next.js backt
                # NEXT_PUBLIC_* beim Bau ins Browser-Bundle.
                for schluessel in list(args if isinstance(args, dict) else {}) + list(umgebung):
                    self.assertFalse(
                        schluessel.startswith("NEXT_PUBLIC_"),
                        f"{name}: {dienst} gibt {schluessel} mit — wirkungslos (#889).",
                    )


class EnvBeispielNenntDieSchalter(unittest.TestCase):
    def test_jeder_aus_env_gelesene_schluessel_steht_in_env_example(self):
        lib = (WURZEL / "scripts/lib/abbilder.sh").read_text()
        # abbilder_env <NAME> ist die EINZIGE Stelle, die .env selbst liest.
        namen = set(re.findall(r"abbilder_env\s+([A-Z_][A-Z0-9_]*)", lib))
        self.assertIn("AI_EMPLOYEE_REGISTRY", namen, "Liest abbilder.sh die Registry nicht mehr aus .env?")
        beispiel = (WURZEL / ".env.example").read_text()
        for name in sorted(namen):
            self.assertIn(
                name, beispiel,
                f"{name} wird aus .env gelesen, steht aber nicht in .env.example — "
                "der Betreiber findet den Schalter nie (#889).",
            )

    def test_die_version_der_abbilder_ist_dokumentiert(self):
        """Sie verknuepft Code (git) und Abbild. Wer sie nicht kennt, pinnt sie
        versehentlich auf einen alten Stand oder gar nicht."""
        beispiel = (WURZEL / ".env.example").read_text()
        self.assertIn("AI_EMPLOYEE_VERSION", beispiel)


class SchalterHoerenAufIhrenDokumentiertenNamen(unittest.TestCase):
    """Die Einstellungen haben KEIN env_prefix (config.py: ``env_prefix: ""``) —
    der Feldname IST der Umgebungsname. Ein Feld ``abbild_holen`` hoerte deshalb
    auf ``ABBILD_HOLEN``, waehrend Compose, ``.env.example`` und die Anleitung
    ``AI_EMPLOYEE_ABBILD_HOLEN`` versprachen: der Schalter wirkte nicht, und kein
    Test sah es, weil alle Tests das FELD patchen statt die Umgebung zu setzen.
    Dieser hier setzt die Umgebung.
    """

    SCHALTER = {
        "AI_EMPLOYEE_REGISTRY": ("ai_employee_registry", "registry.example.com/ai", "registry.example.com/ai"),
        "AI_EMPLOYEE_ABBILD_HOLEN": ("ai_employee_abbild_holen", "0", False),
        "AI_EMPLOYEE_ABBILD_ABGELEHNT": ("ai_employee_abbild_abgelehnt", "1.2.3", "1.2.3"),
    }

    def test_jeder_schalter_wirkt_unter_seinem_namen(self):
        import os
        import subprocess
        import sys
        for umgebung, (feld, wert, erwartet) in self.SCHALTER.items():
            with self.subTest(umgebung=umgebung):
                # Eigener Prozess: die Einstellungen werden beim Import EINMAL
                # gelesen, ein os.environ-Patch im laufenden Test kaeme zu spaet.
                code = (
                    "import sys; sys.path.insert(0, '.');"
                    "from app.config import Settings;"
                    f"print(repr(Settings().{feld}))"
                )
                r = subprocess.run(
                    [sys.executable, "-c", code],
                    cwd=WURZEL / "orchestrator",
                    env={**os.environ, umgebung: wert},
                    capture_output=True, text=True,
                )
                self.assertEqual(0, r.returncode, r.stderr[-800:])
                self.assertEqual(
                    repr(erwartet), r.stdout.strip(),
                    f"{umgebung}={wert} wirkt nicht auf Settings.{feld} — "
                    "heisst das Feld noch wie die Umgebungsvariable?",
                )

    def test_die_namen_stehen_in_compose_und_env_example(self):
        """Ein Schalter, der im Code wirkt, aber nirgends durchgereicht wird, ist
        fuer den Betreiber nicht vorhanden."""
        compose = (WURZEL / "docker-compose.yml").read_text()
        beispiel = (WURZEL / ".env.example").read_text()
        for umgebung in self.SCHALTER:
            with self.subTest(umgebung=umgebung):
                self.assertIn(umgebung, compose, f"{umgebung} erreicht den Orchestrator nicht")
                self.assertIn(umgebung, beispiel, f"{umgebung} ist nicht dokumentiert")


if __name__ == "__main__":
    unittest.main()

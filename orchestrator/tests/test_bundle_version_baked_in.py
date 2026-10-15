"""Das Frontend-Bundle muss seine eigene Version kennen.

Vorfall 2026-08-21: Nach dem Deploy sah der Nutzer weiter die alte Oberflaeche
(„schon deployed? -.-"). Der Code WAR ausgeliefert — sein Browser hielt nur die
bereits geladene Seite. Genau dafuer gibt es den Hinweis „Veraltete Oberflaeche
— neu laden"; er konnte aber nie erscheinen, weil das Image von Hand ohne
`APP_VERSION` gebaut worden war. Im Bundle stand „dev", der Abgleich gegen die
Backend-Version faellt dann still aus.

Geprueft wird die Kette: compose reicht die Variable durch, das Update-Skript
setzt sie aus der VERSION-Datei, und das Bundle meldet sich, wenn sie fehlt.
"""

import unittest
from pathlib import Path

_WURZEL = Path(__file__).resolve().parents[2]
_COMPOSE = (_WURZEL / "docker-compose.yml").read_text()
_UPDATE_SH = (_WURZEL / "scripts" / "update.sh").read_text()
_DOCKERFILE = (_WURZEL / "frontend" / "Dockerfile").read_text()
_BANNER = (_WURZEL / "frontend" / "src" / "components" / "layout"
           / "update-banner.tsx").read_text()


class DieVersionKommtBisInsBundleTests(unittest.TestCase):
    def test_compose_reicht_sie_als_build_arg_durch(self):
        self.assertIn("APP_VERSION: ${APP_VERSION:-dev}", _COMPOSE)

    def test_das_dockerfile_backt_sie_ein(self):
        self.assertIn("ARG APP_VERSION=dev", _DOCKERFILE)
        self.assertIn("ENV NEXT_PUBLIC_APP_VERSION=$APP_VERSION", _DOCKERFILE)

    def test_das_update_skript_nutzt_die_gemeinsame_bereitstellung(self):
        """Seit #889 holt/baut scripts/lib/abbilder.sh die Abbilder — dort wird
        APP_VERSION aus der VERSION-Datei gesetzt. Dass sie beim lokalen Bau
        wirklich ankommt, prueft test_abbilder_aus_registry mit einer
        Docker-Attrappe (test_lokaler_bau_bekommt_die_version)."""
        self.assertIn("abbilder_bereitstellen", _UPDATE_SH)
        self.assertNotIn("docker compose up -d --build", _UPDATE_SH,
                         "kein zweiter, eigener Bauweg neben der gemeinsamen Funktion")


class EinBundleOhneVersionFaelltAufTests(unittest.TestCase):
    def test_es_meldet_sich_im_produktionsbuild(self):
        self.assertIn('process.env.NODE_ENV === "production"', _BANNER)
        self.assertIn("!SEMVER.test(BUNDLE_VERSION)", _BANNER)
        self.assertIn("console.warn(", _BANNER)

    def test_der_hinweis_selbst_bleibt_an_den_versionsvergleich_geknuepft(self):
        """Ohne SEMVER-Pruefung wuerde jede Entwicklungsumgebung dauerwarnen."""
        self.assertIn("BUNDLE_VERSION !== backendVersion", _BANNER)


class DerHinweisErreichtDenNutzerZeitnahTests(unittest.TestCase):
    def test_beim_zurueckkommen_auf_den_tab_wird_geprueft(self):
        """30 Minuten Takt sind zu traege, wenn jemand die Seite offen laesst."""
        self.assertIn('document.addEventListener("visibilitychange"', _BANNER)
        self.assertIn('document.visibilityState === "visible"', _BANNER)

    def test_der_horcher_wird_wieder_abgeraeumt(self):
        self.assertIn('document.removeEventListener("visibilitychange"', _BANNER)


if __name__ == "__main__":
    unittest.main()

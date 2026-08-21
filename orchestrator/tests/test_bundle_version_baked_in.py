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

    def test_das_update_skript_nimmt_sie_aus_der_VERSION_datei(self):
        """Genau dieser Schritt fehlte beim Bauen von Hand."""
        self.assertIn('export APP_VERSION="$(cat VERSION', _UPDATE_SH)

    def test_das_skript_setzt_sie_vor_dem_bauen(self):
        vor = _UPDATE_SH.index("export APP_VERSION=")
        bauen = _UPDATE_SH.index("docker compose up -d --build")
        self.assertLess(vor, bauen, "Ein spaeter gesetztes ARG wirkt nicht mehr")


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

"""Das Frontend-Bundle muss seine eigene Version kennen.

Vorfall 2026-08-21: Nach dem Deploy sah der Nutzer weiter die alte Oberflaeche
(„schon deployed? -.-"). Der Code WAR ausgeliefert — sein Browser hielt nur die
bereits geladene Seite. Genau dafuer gibt es den Hinweis „Veraltete Oberflaeche
— neu laden"; er konnte aber nie erscheinen, weil das Image von Hand ohne
`APP_VERSION` gebaut worden war. Im Bundle stand „dev", der Abgleich gegen die
Backend-Version faellt dann still aus.

Geprueft wird die Kette: compose reicht die Variable durch, das Abbild bekommt
sie aus der VERSION-Datei, und das Bundle meldet sich, wenn sie fehlt.

Seit #889 gibt es dafuer ZWEI Wege, und beide muessen die Version einbacken:
lokal baut ``scripts/lib/abbilder.sh`` (gemeinsam von ``setup.sh`` und
``update.sh`` genutzt), in der Registry baut ``.github/workflows/images.yml``.
Vorher stand das ``export`` in ``update.sh`` selbst — die Pruefung hing an dem
Ort statt an der Kette und wurde beim Umbau rot, obwohl die Kette hielt. Den
Registry-Weg deckte sie dagegen gar nicht ab; genau dort haetten die
ausgelieferten Abbilder still mit ``dev`` im Bundle landen koennen.
"""

import unittest
from pathlib import Path

_WURZEL = Path(__file__).resolve().parents[2]
_COMPOSE = (_WURZEL / "docker-compose.yml").read_text()
_UPDATE_SH = (_WURZEL / "scripts" / "update.sh").read_text()
_SETUP_SH = (_WURZEL / "scripts" / "setup.sh").read_text()
_ABBILDER_SH = (_WURZEL / "scripts" / "lib" / "abbilder.sh").read_text()
_IMAGES_YML = (_WURZEL / ".github" / "workflows" / "images.yml").read_text()
_DOCKERFILE = (_WURZEL / "frontend" / "Dockerfile").read_text()
_BANNER = (_WURZEL / "frontend" / "src" / "components" / "layout"
           / "update-banner.tsx").read_text()


class DieVersionKommtBisInsBundleTests(unittest.TestCase):
    def test_compose_reicht_sie_als_build_arg_durch(self):
        self.assertIn("APP_VERSION: ${APP_VERSION:-dev}", _COMPOSE)

    def test_das_dockerfile_backt_sie_ein(self):
        self.assertIn("ARG APP_VERSION=dev", _DOCKERFILE)
        self.assertIn("ENV NEXT_PUBLIC_APP_VERSION=$APP_VERSION", _DOCKERFILE)

    def test_der_lokale_bau_nimmt_sie_aus_der_VERSION_datei(self):
        """Genau dieser Schritt fehlte beim Bauen von Hand."""
        self.assertIn("< VERSION", _ABBILDER_SH)
        self.assertIn('export APP_VERSION="$version"', _ABBILDER_SH)

    def test_der_lokale_bau_setzt_sie_vor_dem_bauen(self):
        """Ein spaeter gesetztes ARG wirkt nicht mehr."""
        vor = _ABBILDER_SH.index("export APP_VERSION=")
        bauen = _ABBILDER_SH.index("docker compose build")
        self.assertLess(vor, bauen)

    def test_beide_einstiege_gehen_ueber_diesen_bau(self):
        """Sonst haette ein Einstieg wieder sein eigenes, stilles Verhalten."""
        for name, skript in (("update.sh", _UPDATE_SH), ("setup.sh", _SETUP_SH)):
            with self.subTest(skript=name):
                self.assertIn("scripts/lib/abbilder.sh", skript)
                self.assertIn("abbilder_bereitstellen", skript)

    def test_die_registry_backt_sie_ebenfalls_ein(self):
        """Der Weg, den der Betreiber seit #889 im Normalfall nimmt: die
        Abbilder werden nicht mehr bei ihm gebaut, sondern geholt. Fehlt das
        Build-Arg hier, steht in JEDEM ausgelieferten Bundle wieder ``dev``."""
        self.assertIn("APP_VERSION=${{ needs.version.outputs.tag }}", _IMAGES_YML)


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

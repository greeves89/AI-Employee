"""Ein App-Paket als ZIP in den Arbeitsbereich importieren.

Gegenstueck zu ``test_folder_export_zip.py``: was der Export herausgibt, muss
der Import wieder hereinbekommen.

Der Schwerpunkt liegt auf dem, was schiefgehen kann, wenn ein Archiv von
aussen kommt — Pfadausbruch, Symlinks, gesperrte Endungen, Zip-Bombe. Der
Vault-Import (``core/vault_transfer.py``) hat dieselben Fallen schon einmal
entschaerft; diese Tests halten fest, dass der App-Import sie ebenfalls kennt.

Dazu der Schnell-Checkup: Eine App ohne Compose-Datei laesst sich auf der
Plattform nicht starten, und eine, deren Compose-Datei zu tief liegt, findet
der Verzeichnis-Scan nicht einmal. Beides soll der Nutzer beim Import erfahren
und nicht erst beim Startversuch.
"""

import io
import unittest
import zipfile
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.core.file_manager import (
    MAX_IMPORT_ARCHIV_BYTES,
    FileManager,
    pruefe_app_paket,
)


def _zip(eintraege: dict[str, bytes], *, symlinks: set[str] = frozenset()) -> bytes:
    puffer = io.BytesIO()
    with zipfile.ZipFile(puffer, "w") as z:
        for name, inhalt in eintraege.items():
            info = zipfile.ZipInfo(name)
            if name in symlinks:
                # 0xA im oberen Nibble = Symlink
                info.external_attr = 0xA1FF << 16
            else:
                info.external_attr = 0o100644 << 16
            z.writestr(info, inhalt)
    return puffer.getvalue()


def _manager() -> tuple[FileManager, MagicMock]:
    docker = SimpleNamespace(
        client=SimpleNamespace(containers=MagicMock()),
        write_files_in_container=MagicMock(),
    )
    return FileManager(docker), docker.write_files_in_container


COMPOSE = b"services:\n  web:\n    image: nginx\n"


class ImportGrundfall(unittest.TestCase):
    def test_paket_wird_geschrieben(self):
        mgr, schreiben = _manager()
        bericht = mgr.importiere_ordner_zip("c1", "/workspace", _zip({
            "meine-app/docker-compose.yml": COMPOSE,
            "meine-app/src/index.js": b"console.log(1)",
        }))

        self.assertEqual(bericht.geschrieben, 2)
        self.assertEqual(bericht.ordner, "meine-app")
        self.assertTrue(bericht.startklar)

        # Verschachtelte Pfade muessen erhalten bleiben — sonst entsteht aus
        # einem Projekt eine Dateiwolke.
        geschriebene = dict(schreiben.call_args[0][2])
        self.assertIn("meine-app/src/index.js", geschriebene)

    def test_leeres_archiv_wird_abgelehnt(self):
        mgr, _ = _manager()
        with self.assertRaises(ValueError):
            mgr.importiere_ordner_zip("c1", "/workspace", _zip({}))

    def test_kein_zip(self):
        mgr, _ = _manager()
        with self.assertRaises(ValueError):
            mgr.importiere_ordner_zip("c1", "/workspace", b"kein zip")

    def test_zu_grosses_archiv(self):
        mgr, _ = _manager()
        with self.assertRaises(ValueError) as ctx:
            mgr.importiere_ordner_zip("c1", "/workspace", b"x" * (MAX_IMPORT_ARCHIV_BYTES + 1))
        self.assertIn("groesser als", str(ctx.exception))


class ImportAbwehr(unittest.TestCase):
    """Die Faelle, wegen derer es diese Pruefungen ueberhaupt gibt."""

    def test_pfadausbruch_wird_uebersprungen(self):
        mgr, schreiben = _manager()
        bericht = mgr.importiere_ordner_zip("c1", "/workspace", _zip({
            "app/docker-compose.yml": COMPOSE,
            "../../etc/passwd": b"root:x:0:0",
        }))
        geschriebene = [n for n, _ in schreiben.call_args[0][2]]
        self.assertNotIn("../../etc/passwd", geschriebene)
        self.assertTrue(any("heraus" in e for e in bericht.uebersprungen))

    def test_symlink_wird_uebersprungen(self):
        mgr, schreiben = _manager()
        bericht = mgr.importiere_ordner_zip("c1", "/workspace", _zip(
            {"app/docker-compose.yml": COMPOSE, "app/link": b"/etc/shadow"},
            symlinks={"app/link"},
        ))
        geschriebene = [n for n, _ in schreiben.call_args[0][2]]
        self.assertNotIn("app/link", geschriebene)
        self.assertTrue(any("regulaerer" in e for e in bericht.uebersprungen))

    def test_gesperrte_endung_wird_uebersprungen(self):
        mgr, schreiben = _manager()
        bericht = mgr.importiere_ordner_zip("c1", "/workspace", _zip({
            "app/docker-compose.yml": COMPOSE,
            "app/setup.exe": b"MZ",
        }))
        geschriebene = [n for n, _ in schreiben.call_args[0][2]]
        self.assertNotIn("app/setup.exe", geschriebene)
        self.assertTrue(any("Dateiendung" in e for e in bericht.uebersprungen))

    def test_node_modules_bleiben_draussen(self):
        mgr, schreiben = _manager()
        mgr.importiere_ordner_zip("c1", "/workspace", _zip({
            "app/docker-compose.yml": COMPOSE,
            "app/node_modules/links/index.js": b"x" * 100,
        }))
        geschriebene = [n for n, _ in schreiben.call_args[0][2]]
        self.assertNotIn("app/node_modules/links/index.js", geschriebene)

    def test_uebersprungene_werden_gedeckelt_aber_gezaehlt(self):
        """50 Namen in der Liste, die Gesamtzahl bleibt ehrlich."""
        mgr, _ = _manager()
        eintraege = {"app/docker-compose.yml": COMPOSE}
        eintraege.update({f"app/f{i}.exe": b"MZ" for i in range(60)})
        bericht = mgr.importiere_ordner_zip("c1", "/workspace", _zip(eintraege))
        self.assertEqual(len(bericht.uebersprungen), 50)
        self.assertEqual(bericht.uebersprungen_gesamt, 60)


class Checkup(unittest.TestCase):
    """Taugt das Paket als App auf der Plattform?"""

    def test_ohne_compose_nicht_startklar(self):
        befunde = pruefe_app_paket(["app/index.js"])
        self.assertTrue(any(b.art == "fehler" for b in befunde))

    def test_mit_compose_startklar(self):
        befunde = pruefe_app_paket(["app/docker-compose.yml"])
        self.assertFalse(any(b.art == "fehler" for b in befunde))

    def test_alle_vier_compose_namen_zaehlen(self):
        for name in ("docker-compose.yml", "docker-compose.yaml",
                     "compose.yml", "compose.yaml"):
            with self.subTest(name=name):
                befunde = pruefe_app_paket([f"app/{name}"])
                self.assertFalse(any(b.art == "fehler" for b in befunde))

    def test_zu_tief_ist_ein_fehler(self):
        """Der Verzeichnis-Scan geht nur drei Ebenen tief."""
        befunde = pruefe_app_paket(["a/b/c/docker-compose.yml"])
        self.assertTrue(any(b.art == "fehler" and "tief" in b.text for b in befunde))

    def test_build_ohne_dockerfile_warnt(self):
        inhalt = b"services:\n  web:\n    build: .\n"
        archiv = zipfile.ZipFile(io.BytesIO(_zip({"app/docker-compose.yml": inhalt})))
        befunde = pruefe_app_paket(["app/docker-compose.yml"], archiv)
        self.assertTrue(any(b.art == "warnung" and "Dockerfile" in b.text for b in befunde))

    def test_build_mit_dockerfile_warnt_nicht(self):
        inhalt = b"services:\n  web:\n    build: .\n"
        namen = ["app/docker-compose.yml", "app/Dockerfile"]
        archiv = zipfile.ZipFile(io.BytesIO(_zip({"app/docker-compose.yml": inhalt})))
        befunde = pruefe_app_paket(namen, archiv)
        self.assertFalse(any("Dockerfile" in b.text and b.art == "warnung" for b in befunde))

    def test_env_datei_wird_gemeldet(self):
        """Der Export nimmt .env mit — dort stehen ueblicherweise Zugangsdaten."""
        befunde = pruefe_app_paket(["app/docker-compose.yml", "app/.env"])
        self.assertTrue(any(b.art == "warnung" and ".env" in b.text for b in befunde))

    def test_defekte_compose_sprengt_den_import_nicht(self):
        archiv = zipfile.ZipFile(io.BytesIO(_zip({"app/docker-compose.yml": b"{{{ kaputt"})))
        befunde = pruefe_app_paket(["app/docker-compose.yml"], archiv)
        self.assertTrue(any(b.art == "warnung" for b in befunde))


if __name__ == "__main__":
    unittest.main()

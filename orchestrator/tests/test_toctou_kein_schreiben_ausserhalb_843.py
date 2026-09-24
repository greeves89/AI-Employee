"""Issue #843, schaerfere Frage als die Nachpruefung beantwortet: landen im
TOCTOU-Fenster ueberhaupt noch Bytes an einem Pfad, den der Agent sich per
Symlink ausgesucht hat?

Die erste Stufe des Fixes (Nachpruefung nach ``put_archive``) erkennt den
Tausch zuverlaessig — aber erst NACHDEM der Docker-Daemon den Symlink schon
aufgeloest und geschrieben hat. Der Gegenleser-Review zu PR #848 hat genau
das am echten Dateisystem belegt: die Ausnahme kommt korrekt, die Datei liegt
trotzdem im Fluchtziel.

Dieser Test misst deshalb nicht die Erkennung, sondern die Wirkung: nach
einem gewonnenen Fenster darf ausserhalb der Zielkette NICHTS liegen.
``put_archive`` schreibt dafuer nicht mehr in den vom Agenten erreichbaren
Zielpfad, sondern in ein root-eigenes Staging-Verzeichnis; die Uebernahme in
die Zielkette passiert in EINEM Exec, das jedes Kettenglied mit O_NOFOLLOW
oeffnet und ausschliesslich relativ zu den so festgenagelten Deskriptoren
schreibt.
"""

import io
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest

from app.services import docker_service as ds_modul
from app.services.docker_service import DockerService, ZielordnerKompromittiert


class _FakeContainer:
    """``put_archive`` wie der echte Daemon: der Zielpfad wird per realpath
    aufgeloest, Symlinks eingeschlossen (moby ``daemon/archive_unix.go``,
    ``filepath.EvalSymlinks`` — ausdruecklich auch fuer das letzte Glied).
    Genau diese Aufloesung ist der Fluchtweg aus #843."""

    def __init__(self):
        self.ziele = []

    def put_archive(self, dir_path, tar_stream):
        self.ziele.append(dir_path)
        ziel = os.path.realpath(dir_path)
        with tarfile.open(fileobj=io.BytesIO(tar_stream.read())) as tar:
            tar.extractall(ziel)
        return True


class _FakeClient:
    def __init__(self, container):
        self._container = container

    class _Containers:
        def __init__(self, container):
            self._container = container

        def get(self, _cid):
            return self._container

    @property
    def containers(self):
        return self._Containers(self._container)


class KeinSchreibenAusserhalbDerKetteTests(unittest.TestCase):
    """Echtes Dateisystem, echte Skripte aus dem Diff, gefaelscht ist nur der
    Docker-Roundtrip. Kein gemocktes ``exec_in_container`` mit vorgegebenen
    Rueckgabewerten — sonst prueft der Test die eigene Erwartung statt den
    Code."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self._tmp.name, "workspace")
        self.outside = os.path.join(self._tmp.name, "outside")
        self.staging_root = os.path.join(self._tmp.name, "staging")
        os.mkdir(self.root)
        os.mkdir(self.outside)
        os.makedirs(os.path.join(self.root, "projects", "app"))

        self._staging_patch = getattr(ds_modul, "_IMPORT_STAGING_PARENT", None)
        if self._staging_patch is not None:
            ds_modul._IMPORT_STAGING_PARENT = self.staging_root

        self.container = _FakeContainer()
        self.svc = DockerService.__new__(DockerService)  # __init__ braucht einen Daemon
        self.svc.client = _FakeClient(self.container)
        self.svc.exec_in_container = self._exec
        self.tausch_beim_naechsten_exec_ende = False
        self.execs = []

    def tearDown(self):
        if self._staging_patch is not None:
            ds_modul._IMPORT_STAGING_PARENT = self._staging_patch
        self._tmp.cleanup()

    def _exec(self, _cid, cmd, user=None):
        """Fuehrt das Skript wirklich aus — als derselbe Nutzer, der den Test
        faehrt (chown auf die eigene uid/gid ist ohne root erlaubt)."""
        self.execs.append(cmd)
        r = subprocess.run(cmd, capture_output=True, text=True)
        if self.tausch_beim_naechsten_exec_ende:
            # Der Agent gewinnt das Fenster: das Kettenglied, das die
            # Vorbereitung ihm gerade uebergeben hat, wird gegen einen
            # Symlink nach draussen getauscht.
            self.tausch_beim_naechsten_exec_ende = False
            ziel = os.path.join(self.root, "projects", "app")
            os.rmdir(ziel)
            os.symlink(self.outside, ziel)
        return r.returncode, r.stdout + r.stderr

    def _schreibe_datei(self):
        self.svc.write_file_in_container(
            "c1", os.path.join(self.root, "projects", "app", "geheim.txt"),
            "GEHEIMNIS: vom Agenten geschrieben",
            uid=os.getuid(), gid=os.getgid(), root=self.root,
        )

    def _schreibe_ordner(self):
        self.svc.write_files_in_container(
            "c1", os.path.join(self.root, "projects", "app"),
            [("src/index.js", b"GEHEIMNIS: vom Agenten geschrieben")],
            uid=os.getuid(), gid=os.getgid(), root=self.root,
        )

    # --- der eigentliche Befund aus dem #848-Review -----------------------

    def test_einzeldatei_landet_nach_gewonnenem_fenster_nicht_ausserhalb(self):
        self.tausch_beim_naechsten_exec_ende = True  # direkt nach der Vorbereitung
        with self.assertRaises(ZielordnerKompromittiert):
            self._schreibe_datei()
        self.assertEqual(
            sorted(os.listdir(self.outside)), [],
            "Der Schreibvorgang ist dem eingetauschten Symlink gefolgt — die "
            "Bytes liegen am vom Angreifer gewaehlten Pfad, die Ausnahme "
            "kommt zu spaet.",
        )

    def test_ordnerimport_landet_nach_gewonnenem_fenster_nicht_ausserhalb(self):
        self.tausch_beim_naechsten_exec_ende = True
        with self.assertRaises(ZielordnerKompromittiert):
            self._schreibe_ordner()
        self.assertEqual(sorted(os.listdir(self.outside)), [])

    # --- Gegenrichtung: ohne Angriff muss der Schreibweg heil bleiben -----

    def test_ohne_angriff_liegt_die_einzeldatei_im_ziel(self):
        self._schreibe_datei()
        ziel = os.path.join(self.root, "projects", "app", "geheim.txt")
        self.assertTrue(os.path.isfile(ziel))
        with open(ziel) as fh:
            self.assertEqual(fh.read(), "GEHEIMNIS: vom Agenten geschrieben")
        self.assertEqual(sorted(os.listdir(self.outside)), [])

    def test_ohne_angriff_liegt_der_ordnerimport_mit_unterordnern_im_ziel(self):
        self._schreibe_ordner()
        ziel = os.path.join(self.root, "projects", "app", "src", "index.js")
        self.assertTrue(os.path.isfile(ziel), sorted(os.walk(self.root)))
        with open(ziel, "rb") as fh:
            self.assertEqual(fh.read(), b"GEHEIMNIS: vom Agenten geschrieben")

    def test_bestehende_datei_wird_weiterhin_ueberschrieben(self):
        os.makedirs(os.path.join(self.root, "projects", "app"), exist_ok=True)
        with open(os.path.join(self.root, "projects", "app", "geheim.txt"), "w") as fh:
            fh.write("alt")
        self._schreibe_datei()
        with open(os.path.join(self.root, "projects", "app", "geheim.txt")) as fh:
            self.assertEqual(fh.read(), "GEHEIMNIS: vom Agenten geschrieben")


if __name__ == "__main__":
    unittest.main()

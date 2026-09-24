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

from docker.errors import APIError

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
        # Bewusst OHNE tarfile.extractall: der Daemon entpackt nicht
        # abgesichert, und genau das soll hier nachgebildet werden — mit
        # extractall wuerde ein Sicherheitsfilter (ab Python 3.14 der
        # Standard) die Fluchtwege wegnehmen, die dieser Test messen will.
        # Entpackt wird deshalb Eintrag fuer Eintrag unter dem AUFGELOESTEN
        # Zielpfad, so wie moby es tut.
        ziel = os.path.realpath(dir_path)
        with tarfile.open(fileobj=io.BytesIO(tar_stream.read())) as tar:
            for eintrag in tar.getmembers():
                pfad = os.path.join(ziel, eintrag.name)
                if eintrag.isdir():
                    os.makedirs(pfad, exist_ok=True)
                    os.chmod(pfad, eintrag.mode)
                    continue
                os.makedirs(os.path.dirname(pfad), exist_ok=True)
                with open(pfad, "wb") as fh:
                    quelle = tar.extractfile(eintrag)
                    if quelle is not None:
                        fh.write(quelle.read())
                os.chmod(pfad, eintrag.mode)
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


    # --- Luecken, die eine Mutationsbatterie sichtbar gemacht hat ---------

    def test_hardlink_im_ziel_zerstoert_die_opferdatei_nicht(self):
        """O_NOFOLLOW sieht nur Symlinks. Legt der Agent vorher einen HARDLINK
        auf eine Datei ausserhalb der Kette, wuerde ein O_TRUNC-Schreibvorgang
        als root deren Inhalt zerstoeren und sie per fchown dem Agenten
        ueberschreiben — ohne dass je ein Symlink im Spiel war."""
        opfer = os.path.join(self.outside, "opfer.txt")
        with open(opfer, "w") as fh:
            fh.write("fremder Inhalt")
        os.chmod(opfer, 0o600)
        os.link(opfer, os.path.join(self.root, "projects", "app", "geheim.txt"))

        self._schreibe_datei()

        with open(opfer) as fh:
            self.assertEqual(
                fh.read(), "fremder Inhalt",
                "Der Schreibvorgang ist einem Hardlink gefolgt und hat eine "
                "Datei ausserhalb der Zielkette ueberschrieben.",
            )
        self.assertEqual(os.stat(opfer).st_mode & 0o777, 0o600)
        ziel = os.path.join(self.root, "projects", "app", "geheim.txt")
        with open(ziel) as fh:
            self.assertEqual(fh.read(), "GEHEIMNIS: vom Agenten geschrieben")

    def test_unterordner_aus_dem_archiv_folgt_keinem_symlink_im_ziel(self):
        """Zweite O_NOFOLLOW-Stelle: nicht die Kettenglieder, sondern die
        Ordner, die die Uebernahme selbst im Ziel anlegt."""
        os.symlink(self.outside, os.path.join(self.root, "projects", "app", "src"))
        with self.assertRaises(ZielordnerKompromittiert):
            self._schreibe_ordner()
        self.assertEqual(sorted(os.listdir(self.outside)), [])

    def test_behaelter_weg_meldet_fehlschlag_statt_erfolg(self):
        echt = self.svc.exec_in_container

        def nur_die_uebernahme_faellt_aus(cid, cmd, user=None):
            if self.execs:  # der erste Aufruf ist die Vorbereitung
                raise APIError("Behaelter antwortet nicht")
            return echt(cid, cmd, user=user)

        self.svc.exec_in_container = nur_die_uebernahme_faellt_aus
        with self.assertRaises(ZielordnerKompromittiert):
            self._schreibe_datei()

    def test_geschriebene_datei_gehoert_dem_agenten(self):
        """Ohne fchown/fchmod bliebe die Datei root:root und mit den Rechten,
        die die umask des Orchestrators gerade vorgibt — der Agent koennte
        seine eigene Datei nicht mehr schreiben (Regressionsklasse #840/#841).

        Die umask wird bewusst restriktiv gesetzt: sonst liefert schon
        ``os.open(..., 0o644)`` zufaellig das erwartete Ergebnis, und der Test
        koennte den Wegfall von fchmod gar nicht bemerken. Den Eigentuemer
        kann ein Testlauf ohne root-Rechte nicht veraendern — geprueft wird
        daher, dass er die uebergebene uid/gid traegt."""
        vorher = os.umask(0o077)
        try:
            self._schreibe_datei()
        finally:
            os.umask(vorher)
        st = os.stat(os.path.join(self.root, "projects", "app", "geheim.txt"))
        self.assertEqual((st.st_uid, st.st_gid), (os.getuid(), os.getgid()))
        self.assertEqual(
            st.st_mode & 0o777, 0o644,
            "Die Datei traegt die umask des Orchestrators statt der "
            "ausdruecklich gesetzten Rechte — fchmod fehlt.",
        )

    def test_datei_groesser_als_ein_brocken_kommt_vollstaendig_an(self):
        """Die Uebernahme kopiert in 1-MiB-Brocken."""
        nutzlast = (b"0123456789abcdef" * 65536) + b"REST"  # 1 MiB + 4 Byte
        self.svc.write_files_in_container(
            "c1", os.path.join(self.root, "projects", "app"),
            [("gross.bin", nutzlast)],
            uid=os.getuid(), gid=os.getgid(), root=self.root,
        )
        with open(os.path.join(self.root, "projects", "app", "gross.bin"), "rb") as fh:
            self.assertEqual(fh.read(), nutzlast)

    def test_aufraeumen_trifft_nur_alte_zwischenlager(self):
        """Die Ordner im Zwischenlager entstehen aus Archiv-Eintraegen und
        tragen deshalb mtime=0 — eine mtime-Schwelle waere IMMER erfuellt und
        haette die Zwischenlager gleichzeitig laufender Importe geloescht.
        Das Alter kommt aus dem Namen."""
        import time as _t

        lager = os.path.join(self.staging_root, ds_modul._IMPORT_STAGING_DIR)
        frisch = os.path.join(lager, f"{int(_t.time())}-parallel")
        alt = os.path.join(lager, f"{int(_t.time()) - 90000}-verwaist")
        for pfad in (frisch, alt):
            os.makedirs(pfad)
            os.utime(pfad, (0, 0))  # wie vom Daemon aus dem Archiv gesetzt

        self._schreibe_datei()

        self.assertTrue(
            os.path.isdir(frisch),
            "Das Zwischenlager eines parallel laufenden Imports wurde geloescht.",
        )
        self.assertFalse(os.path.isdir(alt), "Verwaister Rest blieb liegen.")


if __name__ == "__main__":
    unittest.main()

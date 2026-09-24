"""Issue #840: der ZIP-Ordner-Import schrieb, ohne den Zielpfad gegen Symlinks
abzusichern — die Restklasse zu #821.

#821 hat den Datei-Upload gehaertet: erst die Ordnerkette im Behaelter ueber
Verzeichnis-Deskriptoren (O_NOFOLLOW) anlegen und uebergeben, bei Fehlschlag
VOR dem Schreiben abbrechen. Der Import ging denselben Schreibweg, ohne diesen
Schritt. Und er war nicht der einzige: die Skill-Zuweisung schreibt ebenfalls
direkt (``/workspace/skills/<name>``). Zwei vergessene Aufrufer bei zwei
Gelegenheiten sind kein Zufall — deshalb sitzt die Vorbereitung jetzt dort, wo
geschrieben wird (``write_files_in_container``), und nicht bei den Aufrufern.

WELCHE VARIANTE DIESER FIX SCHLIESST — und welche nicht seine Aufgabe ist.
Beides am Quelltext von moby v25.0.0 nachgelesen, nicht aus der Doku
geschlossen (ein Docker-Daemon war fuer die Messung nicht erreichbar):

(a) Symlink im ZIELPFAD (``path=/workspace/projects/meine-app``, wobei
    ``meine-app`` ein Symlink ist). ``daemon/archive_unix.go`` loest das Ziel
    mit ``filepath.EvalSymlinks`` auf — ausdruecklich einschliesslich des
    letzten Pfadglieds ("so that you can extract an archive to a symlink that
    points to a directory"). Der Schreibvorgang wird also wirklich umgelenkt.
    DAS ist die Luecke, und DAS schliesst dieser Fix.

(b) Symlink, den ein ARCHIV-EINTRAG trifft (``path=/workspace/projects``, im
    ZIP liegt ``meine-app/app.py``, und ``meine-app`` ist ein Symlink) — so
    ist die Reproduktion im Issue skizziert. Diese Variante faengt Docker
    selbst ab: ``pkg/archive/archive.go`` Z. 1139-1164 ruft ``os.Lstat`` auf
    den Zielpfad und entfernt ihn bei abweichendem Typ per ``os.RemoveAll``
    — entfernt wird der SYMLINK, nicht sein Ziel. Hier ist also nichts zu
    reparieren; es steht hier, damit der naechste Leser es nicht fuer eine
    vergessene Luecke haelt.

Ein Test, der nur den Abbruch prueft, deckt die Klasse NICHT ab: entscheidend
ist, dass ausserhalb des Ziels nichts geschrieben wurde.
"""

import ast
import inspect
import io
import os
import tempfile
import unittest
import zipfile
from unittest.mock import MagicMock

from app.core.file_manager import FileManager
from app.services import docker_service as ds_modul
from app.services.docker_service import DockerService, ZielordnerNichtVorbereitbar


class _FakeContainer:
    def __init__(self):
        self.archives = []

    def put_archive(self, dir_path, tar_stream):
        self.archives.append((dir_path, tar_stream.read()))
        return True


class _FakeClient:
    def __init__(self, container):
        self._container = container

    class _Containers:
        def __init__(self, container):
            self._container = container

        def get(self, _container_id):
            return self._container

    @property
    def containers(self):
        return self._Containers(self._container)


def _service(rc=0, out=""):
    """Echter DockerService (kein Mock) mit gefaelschtem Client und
    gefaelschtem exec — so laeuft der echte Schreibweg durch."""
    container = _FakeContainer()
    svc = DockerService.__new__(DockerService)  # __init__ braucht einen Daemon
    svc.client = _FakeClient(container)
    svc.exec_in_container = MagicMock(return_value=(rc, out))
    return svc, container


SYMLINK_ABLEHNUNG = (4, "'meine-app' ist ein Symlink — Upload-Ziel abgelehnt\n")


def _zip_mit_einem_ordner(ordner="meine-app", datei="app.py", inhalt=b"x = 1\n"):
    puffer = io.BytesIO()
    with zipfile.ZipFile(puffer, "w") as z:
        z.writestr(f"{ordner}/{datei}", inhalt)
    return puffer.getvalue()


class ImportSchreibtNichtAnEinemSymlinkVorbeiTests(unittest.TestCase):
    """Der gemeldete Fall aus #840, durch den ECHTEN Schreibweg gefahren."""

    def test_symlink_als_ziel_bricht_ab_und_schreibt_nichts(self):
        svc, container = _service(*SYMLINK_ABLEHNUNG)
        mgr = FileManager(svc)

        with self.assertRaises(ValueError) as ctx:  # -> HTTP 400 im Endpunkt
            mgr.importiere_ordner_zip("c1", "/workspace/projects/meine-app",
                                      _zip_mit_einem_ordner())

        self.assertIn("Symlink", str(ctx.exception))
        # DAS ist der Kern: nicht nur abgebrochen, sondern nichts geschrieben.
        self.assertEqual(container.archives, [])

    def test_import_bereitet_die_kette_unterhalb_von_workspace_vor(self):
        svc, container = _service()
        mgr = FileManager(svc)

        mgr.importiere_ordner_zip("c1", "/workspace/projects", _zip_mit_einem_ordner())

        # #843: nach put_archive uebernimmt _install_from_staging aus dem
        # Zwischenlager in die Kette — die Vorbereitung bleibt der ERSTE Aufruf.
        cmd = svc.exec_in_container.call_args_list[0].args[1]
        self.assertEqual(cmd[:2], ["python3", "-c"])
        self.assertEqual(cmd[3:], ["/workspace", "1000", "1000", "projects"])
        self.assertEqual(svc.exec_in_container.call_args_list[0].kwargs.get("user"), "root")
        self.assertEqual(len(container.archives), 1)

    def test_fehlermeldung_wird_nicht_als_413_fehlgedeutet(self):
        """Der Endpunkt entscheidet 413 vs. 400 am Text ('zu gross' /
        'groesser als'). Eine abgelehnte Zielkette ist ein 400."""
        svc, _ = _service(*SYMLINK_ABLEHNUNG)
        mgr = FileManager(svc)
        with self.assertRaises(ValueError) as ctx:
            mgr.importiere_ordner_zip("c1", "/workspace/x", _zip_mit_einem_ordner())
        text = str(ctx.exception)
        self.assertNotIn("zu gross", text)
        self.assertNotIn("groesser als", text)


class JederSchreibwegGehtDurchDieVorbereitungTests(unittest.TestCase):
    """Die eigentliche Lehre aus #840: nicht den gemeldeten Aufrufer flicken,
    sondern die Stelle, an der geschrieben wird."""

    def test_upload_bleibt_geschuetzt(self):
        svc, container = _service(*SYMLINK_ABLEHNUNG)
        with self.assertRaises(ZielordnerNichtVorbereitbar):
            svc.write_files_in_container("c1", "/workspace/link", [("a.txt", b"a")])
        self.assertEqual(container.archives, [])

    def test_einzahl_helfer_ist_seit_841_ebenfalls_geschuetzt(self):
        """Issue #841: write_file_in_container (Einzahl) ging denselben
        Weg an put_archive vorbei an der Vorbereitung vorbei — hier der
        gemeldete Fall, durch den echten Schreibweg gefahren."""
        svc, container = _service(*SYMLINK_ABLEHNUNG)
        with self.assertRaises(ZielordnerNichtVorbereitbar):
            svc.write_file_in_container("c1", "/workspace/link/knowledge.md", "x")
        self.assertEqual(container.archives, [])

    def test_einzahl_helfer_lehnt_ziel_ausserhalb_der_wurzel_ab(self):
        svc, container = _service()
        with self.assertRaises(ValueError):
            svc.write_file_in_container("c1", "/etc/passwd", "x")
        self.assertEqual(container.archives, [])
        self.assertFalse(svc.exec_in_container.called)

    def test_einzahl_helfer_akzeptiert_bewusst_erklaerte_fremde_wurzel(self):
        svc, container = _service()
        svc.write_file_in_container("c1", "/etc/sudoers.d/x", "x", uid=0, gid=0, root="/etc")

        cmd = svc.exec_in_container.call_args_list[0].args[1]
        self.assertEqual(cmd[3:], ["/etc", "0", "0", "sudoers.d"])
        self.assertEqual(len(container.archives), 1)

    def test_skill_zuweisung_ist_jetzt_ebenfalls_geschuetzt(self):
        """Dritter Aufrufer (`_push_skill_files_to_agent`, Ziel
        /workspace/skills/<name>) — im Issue nicht genannt, gleiche Klasse."""
        svc, container = _service(*SYMLINK_ABLEHNUNG)
        with self.assertRaises(ZielordnerNichtVorbereitbar):
            svc.write_files_in_container("c1", "/workspace/skills/meine-app",
                                         [("SKILL.md", b"# x")])
        self.assertEqual(container.archives, [])

    def test_ziel_ausserhalb_von_workspace_wird_abgelehnt(self):
        svc, container = _service()
        with self.assertRaises(ValueError):
            svc.write_files_in_container("c1", "/etc", [("passwd", b"x")])
        self.assertEqual(container.archives, [])
        self.assertFalse(svc.exec_in_container.called)

    def test_gestoppter_behaelter_wird_zur_verstaendlichen_ablehnung(self):
        """Die Vorbereitung braucht ein exec, put_archive allein kaeme auch an
        einen gestoppten Behaelter heran. Der Unterschied darf den Aufrufer
        nicht als nacktes 500 treffen — und schreiben darf er trotzdem nicht."""
        from docker.errors import APIError

        svc, container = _service()
        svc.exec_in_container = MagicMock(side_effect=APIError("409 Conflict: not running"))
        with self.assertRaises(ZielordnerNichtVorbereitbar) as ctx:
            svc.write_files_in_container("c1", "/workspace/x", [("a.txt", b"a")])
        self.assertIn("laeuft er?", str(ctx.exception))
        self.assertEqual(container.archives, [])

    def _reihenfolge_prepare_vor_put_archive(self, funktion_oder_knoten):
        """Reihenfolge ist die ganze Schutzwirkung: erst pruefen, dann
        schreiben. Ein Formtest, weil ein Verhaltenstest bei rc=0 beide
        Reihenfolgen gleich gruen sieht. Nimmt entweder eine Funktion (per
        inspect.getsource) oder einen bereits geparsten ast.FunctionDef-Knoten
        (fuer den Sammel-Scan ueber alle Traeger)."""
        if isinstance(funktion_oder_knoten, ast.AST):
            baum = funktion_oder_knoten
        else:
            quelle = inspect.getsource(funktion_oder_knoten)
            baum = ast.parse(ast.unparse(ast.parse(quelle.strip())))
        namen = []
        for knoten in ast.walk(baum):
            if isinstance(knoten, ast.Call) and isinstance(knoten.func, ast.Attribute):
                if knoten.func.attr in ("prepare_target_dir", "put_archive"):
                    namen.append((knoten.lineno, knoten.func.attr))
        namen.sort()
        return [n for _, n in namen]

    def test_vorbereitung_laeuft_VOR_dem_schreiben(self):
        self.assertEqual(
            self._reihenfolge_prepare_vor_put_archive(DockerService.write_files_in_container),
            ["prepare_target_dir", "put_archive"],
        )

    def test_vorbereitung_laeuft_VOR_dem_schreiben_auch_im_einzahl_helfer(self):
        """Issue #841: derselbe Reihenfolge-Beweis fuer write_file_in_container
        (Einzahl) — sonst waere die Vorbereitung nur Zierde, wenn sie NACH
        dem Schreiben liefe."""
        self.assertEqual(
            self._reihenfolge_prepare_vor_put_archive(DockerService.write_file_in_container),
            ["prepare_target_dir", "put_archive"],
        )

    def test_kein_aufrufer_umgeht_den_schreib_helfer(self):
        """Wer put_archive direkt ruft, MUSS im selben Funktionskoerper zuerst
        prepare_target_dir rufen — sonst umgeht er die Vorbereitung.

        Seit #841 gilt das fuer BEIDE bekannten Traeger (Einzahl und Mehrzahl),
        und die Reihenfolge-Pruefung (erst pruefen, dann schreiben) laeuft
        automatisch ueber JEDEN gefundenen Traeger — nicht nur ueber die zwei
        bekannten Namen. Ein neuer, dritter Aufrufer von put_archive faellt
        hier durch, wenn er die Vorbereitung vergisst ODER sie nach dem
        Schreiben statt davor ruft — das ist die eigentliche Lehre aus
        #840/#841: nicht den gemeldeten Aufrufer flicken, sondern die Stelle
        vermessen, an der geschrieben wird."""
        quelle = inspect.getsource(ds_modul)
        baum = ast.parse(quelle)
        traeger_ohne_vorbereitung = []
        falsche_reihenfolge = []
        alle_traeger = set()
        for knoten in ast.walk(baum):
            if not isinstance(knoten, ast.FunctionDef):
                continue
            ruft_put_archive = False
            ruft_prepare = False
            for unter in ast.walk(knoten):
                if isinstance(unter, ast.Call) and isinstance(unter.func, ast.Attribute):
                    if unter.func.attr == "put_archive":
                        ruft_put_archive = True
                    elif unter.func.attr == "prepare_target_dir":
                        ruft_prepare = True
            if not ruft_put_archive:
                continue
            alle_traeger.add(knoten.name)
            if not ruft_prepare:
                traeger_ohne_vorbereitung.append(knoten.name)
                continue
            reihenfolge = self._reihenfolge_prepare_vor_put_archive(knoten)
            if reihenfolge != ["prepare_target_dir", "put_archive"]:
                falsche_reihenfolge.append(knoten.name)
        self.assertEqual(alle_traeger, {"write_file_in_container", "write_files_in_container"})
        self.assertEqual(traeger_ohne_vorbereitung, [])
        self.assertEqual(falsche_reihenfolge, [])


class SkriptLehntSymlinkWirklichAbTests(unittest.TestCase):
    """Gegenprobe am echten Dateisystem: das Skript, das im Behaelter laeuft,
    laesst den Symlink nicht durch (der Rest der Batterie steht in
    test_upload_target_dir_owned_by_agent.py)."""

    def test_symlink_wird_abgelehnt_und_dahinter_nichts_angelegt(self):
        import subprocess
        import sys

        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "workspace")
            outside = os.path.join(tmp, "outside")
            os.mkdir(root)
            os.mkdir(outside)
            os.symlink(outside, os.path.join(root, "meine-app"))

            r = subprocess.run(
                [sys.executable, "-c", ds_modul._PREPARE_TARGET_DIR_SCRIPT,
                 root, str(os.getuid()), str(os.getgid()), "meine-app"],
                capture_output=True, text=True,
            )
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("Symlink", r.stdout)
            self.assertEqual(os.listdir(outside), [])


if __name__ == "__main__":
    unittest.main()

"""Der Zielordner eines Datei-Uploads gehoert danach dem Agenten — und die
Uebergabe bleibt in /workspace.

``upload_files`` legt den Zielordner als root an. Ein neuer Ordner (und jeder
Elternordner, den mkdir mit anlegen musste) gehoerte damit root — der Agent
konnte die hochgeladenen Dateien zwar aendern, aber keine neue daneben anlegen.
Sichtbar geworden an einer importierten App, bei der ein Rebuild mit einem
neuen Modul im Import-Loop crashte.

Die Uebergabe (chown als root) darf dabei keinem Symlink folgen: ein bereits
vorhandener Link ``/workspace/link -> /etc`` besteht ``_validate_path`` (reine
Stringpruefung), ein ``chown`` auf den Pfad haette das Systemverzeichnis dem
Agenten uebertragen. Deshalb laeuft die Kette im Container ueber
Verzeichnis-Deskriptoren mit O_NOFOLLOW und fchown — hier gegen echte Symlinks
im Tempdir ausgefuehrt.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock

from app.core.file_manager import (
    _PREPARE_TARGET_DIR_SCRIPT,
    FileManager,
    UploadZielNichtVorbereitbar,
)
from app.services import docker_service as ds_modul
from app.services.docker_service import DockerService


def _run_script(root, *parts):
    """Das In-Container-Skript so ausfuehren, wie exec_in_container es taete —
    nur mit der eigenen uid/gid, damit fchown ohne root erlaubt ist."""
    return subprocess.run(
        [sys.executable, "-c", _PREPARE_TARGET_DIR_SCRIPT, root,
         str(os.getuid()), str(os.getgid()), *parts],
        capture_output=True, text=True,
    )


class PrepareScriptStaysInsideTheRootTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self._tmp.name, "workspace")
        self.outside = os.path.join(self._tmp.name, "outside")
        os.mkdir(self.root)
        os.mkdir(self.outside)
        self.outside_before = os.stat(self.outside)

    def tearDown(self):
        self._tmp.cleanup()

    def test_creates_the_missing_chain(self):
        r = _run_script(self.root, "projects", "app", "src")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(os.path.isdir(os.path.join(self.root, "projects", "app", "src")))

    def test_existing_chain_is_accepted(self):
        os.makedirs(os.path.join(self.root, "projects", "app"))
        r = _run_script(self.root, "projects", "app")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_final_symlink_is_refused(self):
        os.symlink(self.outside, os.path.join(self.root, "link"))
        r = _run_script(self.root, "link")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Symlink", r.stdout)
        # nichts hinter dem Link angefasst
        self.assertEqual(os.stat(self.outside).st_mode, self.outside_before.st_mode)
        self.assertEqual(os.listdir(self.outside), [])

    def test_intermediate_symlink_is_refused_before_anything_is_created(self):
        os.symlink(self.outside, os.path.join(self.root, "link"))
        r = _run_script(self.root, "link", "app", "src")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Symlink", r.stdout)
        # mkdir darf hinter dem Link nichts angelegt haben
        self.assertEqual(os.listdir(self.outside), [])

    def test_regular_file_in_the_chain_is_refused(self):
        open(os.path.join(self.root, "datei"), "w").close()
        r = _run_script(self.root, "datei", "app")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("kein Verzeichnis", r.stdout)

    def test_symlinked_root_is_refused(self):
        os.symlink(self.outside, os.path.join(self._tmp.name, "wurzel-link"))
        r = _run_script(os.path.join(self._tmp.name, "wurzel-link"), "app")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(os.listdir(self.outside), [])

    def test_dotdot_and_absolute_links_are_refused_by_the_script_itself(self):
        """Die Kette kommt aus _validate_path + split — das Skript verlaesst
        sich trotzdem nicht darauf: '..' oeffnete den Elternordner der Wurzel,
        ein absolutes Glied ignoriert dir_fd komplett."""
        parent_before = os.stat(self._tmp.name)
        for glieder in (["..", "x"], ["/tmp/zzz_abs_" + str(os.getpid())], ["a/b"], [""], ["."]):
            with self.subTest(glieder=glieder):
                r = _run_script(self.root, *glieder)
                self.assertNotEqual(r.returncode, 0, glieder)
                self.assertIn("unzulaessiges Kettenglied", r.stdout)
        self.assertEqual(sorted(os.listdir(self._tmp.name)), ["outside", "workspace"])
        self.assertEqual(os.stat(self._tmp.name).st_uid, parent_before.st_uid)

    def test_script_has_no_syntax_error_and_needs_no_third_party_import(self):
        compile(_PREPARE_TARGET_DIR_SCRIPT, "<prepare>", "exec")
        self.assertNotIn("import app", _PREPARE_TARGET_DIR_SCRIPT)

    def test_ownership_is_changed_on_the_descriptor_never_on_a_path(self):
        """Formtest fuer das Austauschrennen, das kein Verhaltenstest sehen
        kann: zwischen dem Oeffnen (O_NOFOLLOW) und einem ``os.chown(pfad)``
        koennte das Glied gegen einen Symlink getauscht werden. Deshalb darf
        das Skript ausschliesslich ``fchown`` auf den geoeffneten Deskriptor
        rufen — nie chown/lchown auf einen Pfad."""
        import ast

        tree = ast.parse(_PREPARE_TARGET_DIR_SCRIPT)
        aufrufe = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "os"
        }
        self.assertIn("fchown", aufrufe)
        self.assertFalse(aufrufe & {"chown", "lchown", "makedirs", "chmod"}, aufrufe)


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
    """Echter DockerService mit gefaelschtem Client — der Schutz sitzt seit
    #840 im Schreib-Helfer, nicht mehr im FileManager. Ein Mock des
    DockerService wuerde ihn also gar nicht mehr ausfuehren."""
    container = _FakeContainer()
    svc = DockerService.__new__(DockerService)  # __init__ braucht einen Daemon
    svc.client = _FakeClient(container)
    svc.exec_in_container = MagicMock(return_value=(rc, out))
    return svc, container


class WriteHandsTheTargetToTheAgentTests(unittest.TestCase):
    """Dieselben Zusagen wie vor #840, nur an der Stelle gemessen, an der die
    Vorbereitung jetzt sitzt — damit sie fuer JEDEN Aufrufer gelten."""

    def test_one_root_exec_prepares_the_chain_then_files_are_written(self):
        svc, container = _service()
        svc.write_files_in_container("c1", "/workspace/projects/app", [("a.txt", b"a")])

        # #843: ein zweiter exec-Aufruf uebernimmt den Inhalt NACH
        # put_archive aus dem Zwischenlager in die Zielkette
        # (_install_from_staging) — die Vorbereitung bleibt der ERSTE Aufruf.
        self.assertEqual(svc.exec_in_container.call_count, 2)
        call = svc.exec_in_container.call_args_list[0]
        cmd = call.args[1]
        self.assertEqual(cmd[:2], ["python3", "-c"])
        self.assertEqual(cmd[2], _PREPARE_TARGET_DIR_SCRIPT)
        self.assertEqual(cmd[3:], ["/workspace", "1000", "1000", "projects", "app"])
        self.assertEqual(call.kwargs.get("user"), "root")
        self.assertEqual(len(container.archives), 1)
        # put_archive zielt seit #843 auf die root-eigene Staging-Wurzel; der
        # Zielpfad steht im zweiten (Uebernahme-)Aufruf.
        self.assertEqual(container.archives[0][0], ds_modul._IMPORT_STAGING_PARENT)
        uebernahme = svc.exec_in_container.call_args_list[1].args[1]
        self.assertEqual(uebernahme[3:][:1], ["/workspace"])
        self.assertEqual(uebernahme[3:][-2:], ["projects", "app"])

    def test_chain_links_come_from_the_normalised_path(self):
        """'/workspace/../workspace/x' darf nur ['x'] ergeben — und auch
        geschrieben wird in den normalisierten Pfad, nicht in den Rohpfad."""
        svc, container = _service()
        svc.write_files_in_container("c1", "/workspace/../workspace//x/./y/", [("a.txt", b"a")])

        cmd = svc.exec_in_container.call_args_list[0].args[1]
        self.assertEqual(cmd[3:], ["/workspace", "1000", "1000", "x", "y"])
        self.assertEqual(container.archives[0][0], ds_modul._IMPORT_STAGING_PARENT)
        uebernahme = svc.exec_in_container.call_args_list[1].args[1]
        self.assertEqual(uebernahme[3:][-2:], ["x", "y"])

    def test_writing_into_workspace_root_passes_no_chain_link(self):
        """/workspace gehoert dem Agenten schon; die Kette ist leer, das Skript
        oeffnet nur die Wurzel und uebergibt nichts."""
        svc, _ = _service()
        svc.write_files_in_container("c1", "/workspace", [("a.txt", b"a")])

        cmd = svc.exec_in_container.call_args_list[0].args[1]
        self.assertEqual(cmd[3:], ["/workspace", "1000", "1000"])

    def test_failed_preparation_aborts_before_anything_is_written(self):
        """Ein fehlgeschlagenes mkdir/chown (rc != 0) darf nicht als
        erfolgreicher Schreibvorgang durchgehen."""
        svc, container = _service(rc=5, out="chown 'app' fehlgeschlagen: Operation not permitted\n")
        with self.assertRaises(UploadZielNichtVorbereitbar) as ctx:
            svc.write_files_in_container("c1", "/workspace/projects/app", [("a.txt", b"a")])

        self.assertIn("Operation not permitted", str(ctx.exception))
        self.assertEqual(container.archives, [])

    def test_symlink_refusal_is_a_400_class_error_with_the_reason(self):
        svc, container = _service(rc=4, out="'link' ist ein Symlink — Upload-Ziel abgelehnt\n")
        with self.assertRaises(ValueError) as ctx:  # -> HTTP 400 im Endpunkt
            svc.write_files_in_container("c1", "/workspace/link", [("a.txt", b"a")])

        self.assertIn("Symlink", str(ctx.exception))
        self.assertEqual(container.archives, [])


class UploadReachesTheProtectedWriterTests(unittest.IsolatedAsyncioTestCase):
    """upload_files selbst bereitet nicht mehr vor — es muss den geschuetzten
    Schreib-Helfer mit dem normalisierten Pfad erreichen."""

    async def test_upload_writes_through_the_protected_helper(self):
        svc, container = _service()
        mgr = FileManager(svc)
        await mgr.upload_files("c1", "/workspace/../workspace//projects/./app/", [("a.txt", b"a")])

        self.assertEqual(container.archives[0][0], ds_modul._IMPORT_STAGING_PARENT)
        cmd = svc.exec_in_container.call_args_list[0].args[1]
        self.assertEqual(cmd[3:], ["/workspace", "1000", "1000", "projects", "app"])
        uebernahme = svc.exec_in_container.call_args_list[1].args[1]
        self.assertEqual(uebernahme[3:][-2:], ["projects", "app"])

    async def test_refused_target_stops_the_upload(self):
        svc, container = _service(rc=4, out="'link' ist ein Symlink — Upload-Ziel abgelehnt\n")
        mgr = FileManager(svc)
        with self.assertRaises(UploadZielNichtVorbereitbar):
            await mgr.upload_files("c1", "/workspace/link", [("a.txt", b"a")])
        self.assertEqual(container.archives, [])


if __name__ == "__main__":
    unittest.main()

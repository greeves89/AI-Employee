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
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.core.file_manager import (
    _PREPARE_TARGET_DIR_SCRIPT,
    FileManager,
    UploadZielNichtVorbereitbar,
)


def _manager(rc=0, out=""):
    docker = SimpleNamespace(
        exec_in_container=MagicMock(return_value=(rc, out)),
        write_files_in_container=MagicMock(),
    )
    return FileManager(docker), docker


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


class UploadHandsTheTargetToTheAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_root_exec_prepares_the_chain_then_files_are_written(self):
        mgr, docker = _manager()
        await mgr.upload_files("c1", "/workspace/projects/app", [("a.txt", b"a")])

        self.assertEqual(docker.exec_in_container.call_count, 1)
        call = docker.exec_in_container.call_args
        cmd = call.args[1]
        self.assertEqual(cmd[:2], ["python3", "-c"])
        self.assertEqual(cmd[2], _PREPARE_TARGET_DIR_SCRIPT)
        self.assertEqual(cmd[3:], ["/workspace", "1000", "1000", "projects", "app"])
        self.assertEqual(call.kwargs.get("user"), "root")
        docker.write_files_in_container.assert_called_once_with(
            "c1", "/workspace/projects/app", [("a.txt", b"a")]
        )

    async def test_chain_links_come_from_the_normalised_path(self):
        """Das Skript bekommt die Glieder aus safe_path (normpath), nicht aus
        dem Rohpfad: '/workspace/../workspace/x' darf nur ['x'] ergeben."""
        mgr, docker = _manager()
        await mgr.upload_files("c1", "/workspace/../workspace//x/./y/", [("a.txt", b"a")])

        cmd = docker.exec_in_container.call_args.args[1]
        self.assertEqual(cmd[3:], ["/workspace", "1000", "1000", "x", "y"])
        docker.write_files_in_container.assert_called_once_with(
            "c1", "/workspace/x/y", [("a.txt", b"a")]
        )

    async def test_uploading_into_workspace_root_passes_no_chain_link(self):
        """/workspace gehoert dem Agenten schon; die Kette ist leer, das Skript
        oeffnet nur die Wurzel und uebergibt nichts."""
        mgr, docker = _manager()
        await mgr.upload_files("c1", "/workspace", [("a.txt", b"a")])

        cmd = docker.exec_in_container.call_args.args[1]
        self.assertEqual(cmd[3:], ["/workspace", "1000", "1000"])

    async def test_failed_preparation_aborts_before_anything_is_written(self):
        """Ein fehlgeschlagenes mkdir/chown (rc != 0) darf nicht als
        erfolgreicher Upload durchgehen: vorher war genau das der Zustand, den
        der Fix beheben soll — Dateien in einem root-eigenen Ordner."""
        mgr, docker = _manager(rc=5, out="chown 'app' fehlgeschlagen: Operation not permitted\n")
        with self.assertRaises(UploadZielNichtVorbereitbar) as ctx:
            await mgr.upload_files("c1", "/workspace/projects/app", [("a.txt", b"a")])

        self.assertIn("Operation not permitted", str(ctx.exception))
        self.assertFalse(docker.write_files_in_container.called)

    async def test_symlink_refusal_is_a_400_class_error_with_the_reason(self):
        mgr, docker = _manager(rc=4, out="'link' ist ein Symlink — Upload-Ziel abgelehnt\n")
        with self.assertRaises(ValueError) as ctx:  # -> HTTP 400 im Endpunkt
            await mgr.upload_files("c1", "/workspace/link", [("a.txt", b"a")])

        self.assertIn("Symlink", str(ctx.exception))
        self.assertFalse(docker.write_files_in_container.called)

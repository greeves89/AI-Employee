"""Issue #843 (Nachfolge zu #840/#842): die TOCTOU-Restluecke zwischen
`prepare_target_dir` (Exec-Aufruf, setzt Ownership) und `put_archive`
(getrennter Docker-API-Roundtrip, schreibt) war bis hierher unbehandelt.

#840/#842 haben den Fall geschlossen, dass ein Symlink bereits VOR dem
Schreibversuch an der Zielkette liegt (`prepare_target_dir` lehnt das per
O_NOFOLLOW ab, BEVOR irgendetwas geschrieben wird). Diese Restluecke ist
enger: ein Agent mit uid 1000 — der genau die Kette besitzt, die
`prepare_target_dir` ihm gerade uebergeben hat — kann versuchen, ein Glied
GENAU in dem Fenster zwischen dem Exec-Aufruf und dem nachfolgenden
`put_archive`-Aufruf gegen einen Symlink zu tauschen.

Der Fix verhindert das Fenster nicht (das wuerde eine einzige atomare
Docker-API-Operation brauchen, die `put_archive` nicht anbietet), sondern
erkennt einen im Fenster erfolgten Tausch NACHTRAEGLICH:
`_assert_target_dir_still_safe` geht die Kette nach `put_archive` nochmal mit
O_NOFOLLOW ab (read-only). Eine Flucht aus der Kette ist nur ueber einen
Symlink moeglich (ein `mkdir` legt ein Ersatz-Verzeichnis immer INNERHALB des
gleichen Elternordners an, es kann die Kette nicht verlassen) — genau das
faengt O_NOFOLLOW ab, unabhaengig vom Owner des eingetauschten Symlinks.
"""

import ast
import inspect
import io
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import MagicMock

from app.services import docker_service as ds_modul
from app.services.docker_service import (
    DockerService,
    ZielordnerKompromittiert,
    ZielordnerNichtVorbereitbar,
    _VERIFY_TARGET_DIR_SCRIPT,
)


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


def _service():
    container = _FakeContainer()
    svc = DockerService.__new__(DockerService)  # __init__ braucht einen Daemon
    svc.client = _FakeClient(container)
    return svc, container


PREPARE_OK = (0, "")
VERIFY_SYMLINK_GETAUSCHT = (
    3, "'app' wurde waehrend des Schreibens gegen einen Symlink getauscht\n",
)


class SkriptErkenntSymlinkTauschAmEchtenDateisystemTests(unittest.TestCase):
    """Gegenprobe am echten Dateisystem, wie schon bei _PREPARE_TARGET_DIR_SCRIPT
    (siehe test_import_zielordner_symlink.py) — hier fuer den read-only
    Nachpruef-Zweig."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self._tmp.name, "workspace")
        self.outside = os.path.join(self._tmp.name, "outside")
        os.mkdir(self.root)
        os.mkdir(self.outside)

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, *parts):
        return subprocess.run(
            [sys.executable, "-c", _VERIFY_TARGET_DIR_SCRIPT, self.root, *parts],
            capture_output=True, text=True,
        )

    def test_unveraenderte_kette_besteht_die_nachpruefung(self):
        os.makedirs(os.path.join(self.root, "projects", "app"))
        r = self._run("projects", "app")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_im_fenster_eingetauschter_symlink_wird_erkannt(self):
        """Das ist genau der Angriff aus #843: die Kette existierte beim
        prepare_target_dir-Exec noch real — dazwischen (hier simuliert durch
        manuelles Umbauen VOR der Nachpruefung) wird ein Glied gegen einen
        Symlink getauscht."""
        os.makedirs(os.path.join(self.root, "projects", "app"))
        # Tausch: "app" verschwindet, an seiner Stelle steht jetzt ein Symlink
        # nach draussen — das simuliert einen Gewinn des Fensters.
        os.rmdir(os.path.join(self.root, "projects", "app"))
        os.symlink(self.outside, os.path.join(self.root, "projects", "app"))

        r = self._run("projects", "app")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Symlink", r.stdout)

    def test_symlink_besteht_unabhaengig_vom_owner_nicht(self):
        """Ein eingetauschter Symlink wird auch dann abgelehnt, wenn er
        (wie beim echten Angriff) demselben Nutzer gehoert, der die Kette
        laut Vorbereitung besitzen soll — der O_NOFOLLOW-Test fragt nicht
        nach dem Owner, sondern nach dem Dateityp."""
        os.makedirs(os.path.join(self.root, "projects"))
        os.symlink(self.outside, os.path.join(self.root, "projects", "app"))
        self.assertEqual(os.lstat(os.path.join(self.root, "projects", "app")).st_uid, os.getuid())

        r = self._run("projects", "app")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Symlink", r.stdout)

    def test_ersatz_durch_ein_echtes_verzeichnis_kann_die_kette_nicht_verlassen(self):
        """Kein Angriffsvektor, sondern die Begruendung dafuer, dass die
        Nachpruefung sich auf Symlinks beschraenken darf: ein 'rmdir + mkdir'
        am selben Kettenglied legt zwangslaeufig wieder INNERHALB desselben
        Elternordners an und besteht die Pruefung — es gibt nichts zu
        erkennen, weil nichts entkommen ist."""
        os.makedirs(os.path.join(self.root, "projects", "app"))
        os.rmdir(os.path.join(self.root, "projects", "app"))
        os.mkdir(os.path.join(self.root, "projects", "app"))

        r = self._run("projects", "app")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_leere_kette_an_der_wurzel_besteht_immer(self):
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_script_has_no_syntax_error_and_needs_no_third_party_import(self):
        compile(_VERIFY_TARGET_DIR_SCRIPT, "<verify>", "exec")
        self.assertNotIn("import app", _VERIFY_TARGET_DIR_SCRIPT)


class NachpruefungWirdNachDemSchreibenAufgerufenTests(unittest.TestCase):
    """Verhaltenstest ueber den gefaelschten Client: _assert_target_dir_still_safe
    laeuft NACH put_archive, und ein Fehlschlag dort markiert den bereits
    erfolgten Schreibvorgang als kompromittiert (ZielordnerKompromittiert)."""

    def test_write_file_in_container_erkennt_kompromittierte_kette(self):
        svc, container = _service()
        svc.exec_in_container = MagicMock(side_effect=[PREPARE_OK, VERIFY_SYMLINK_GETAUSCHT])

        with self.assertRaises(ZielordnerKompromittiert) as ctx:
            svc.write_file_in_container("c1", "/workspace/app/knowledge.md", "x")

        self.assertIn("Symlink", str(ctx.exception))
        # Der Kern der Meldung: put_archive lief bereits (Daten liegen im
        # Container), der Aufrufer soll das trotzdem nicht als Erfolg werten.
        self.assertEqual(len(container.archives), 1)
        self.assertEqual(svc.exec_in_container.call_count, 2)

    def test_write_files_in_container_erkennt_kompromittierte_kette(self):
        svc, container = _service()
        svc.exec_in_container = MagicMock(side_effect=[PREPARE_OK, VERIFY_SYMLINK_GETAUSCHT])

        with self.assertRaises(ZielordnerKompromittiert):
            svc.write_files_in_container("c1", "/workspace/app", [("a.txt", b"a")])

        self.assertEqual(len(container.archives), 1)
        self.assertEqual(svc.exec_in_container.call_count, 2)

    def test_kompromittierung_ist_eine_zielordner_nicht_vorbereitbar_unterklasse(self):
        """Bestehende Aufrufer, die ZielordnerNichtVorbereitbar in einen 4xx
        uebersetzen, sollen das neue Signal automatisch mitbekommen, ohne
        jede Stelle einzeln anzufassen."""
        self.assertTrue(issubclass(ZielordnerKompromittiert, ZielordnerNichtVorbereitbar))

    def test_unveraenderte_kette_meldet_keinen_fehler(self):
        svc, container = _service()
        svc.exec_in_container = MagicMock(return_value=PREPARE_OK)

        svc.write_file_in_container("c1", "/workspace/app/knowledge.md", "x")

        self.assertEqual(len(container.archives), 1)
        self.assertEqual(svc.exec_in_container.call_count, 2)

    def _reihenfolge_put_archive_vor_verify(self, funktion):
        """Formtest wie schon fuer prepare_target_dir/put_archive in
        test_import_zielordner_symlink.py: bei rc=0 sehen beide Reihenfolgen
        gleich gruen aus, deshalb am Quelltext pruefen statt am Verhalten."""
        quelle = inspect.getsource(funktion)
        baum = ast.parse(ast.unparse(ast.parse(quelle.strip())))
        namen = []
        for knoten in ast.walk(baum):
            if isinstance(knoten, ast.Call) and isinstance(knoten.func, ast.Attribute):
                if knoten.func.attr in ("put_archive", "_assert_target_dir_still_safe"):
                    namen.append((knoten.lineno, knoten.func.attr))
        namen.sort()
        return [n for _, n in namen]

    def test_verify_laeuft_nach_put_archive_im_einzahl_helfer(self):
        self.assertEqual(
            self._reihenfolge_put_archive_vor_verify(DockerService.write_file_in_container),
            ["put_archive", "_assert_target_dir_still_safe"],
        )

    def test_verify_laeuft_nach_put_archive_im_mehrzahl_helfer(self):
        self.assertEqual(
            self._reihenfolge_put_archive_vor_verify(DockerService.write_files_in_container),
            ["put_archive", "_assert_target_dir_still_safe"],
        )

    def test_beide_bekannten_put_archive_traeger_rufen_die_nachpruefung(self):
        """Dieselbe Lehre wie in #840/#841: nicht den gemeldeten Aufrufer
        flicken, sondern JEDEN Traeger von put_archive erfassen. Ein neuer,
        dritter Aufrufer faellt hier durch, wenn er die Nachpruefung
        vergisst — analog zum bestehenden Sammel-Scan fuer
        prepare_target_dir in test_import_zielordner_symlink.py."""
        quelle = inspect.getsource(ds_modul)
        baum = ast.parse(quelle)
        traeger_ohne_verify = []
        alle_traeger = set()
        for knoten in ast.walk(baum):
            if not isinstance(knoten, ast.FunctionDef):
                continue
            ruft_put_archive = False
            ruft_verify = False
            for unter in ast.walk(knoten):
                if isinstance(unter, ast.Call) and isinstance(unter.func, ast.Attribute):
                    if unter.func.attr == "put_archive":
                        ruft_put_archive = True
                    elif unter.func.attr == "_assert_target_dir_still_safe":
                        ruft_verify = True
            if not ruft_put_archive:
                continue
            alle_traeger.add(knoten.name)
            if not ruft_verify:
                traeger_ohne_verify.append(knoten.name)
        self.assertEqual(alle_traeger, {"write_file_in_container", "write_files_in_container"})
        self.assertEqual(traeger_ohne_verify, [])


if __name__ == "__main__":
    unittest.main()

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

Der Fix nimmt dem Fenster die Wirkung, statt es nur zu erkennen:
`put_archive` schreibt nur noch in ein root-eigenes Zwischenlager, an dem der
Agent nichts umbiegen kann. Die Zielkette fasst erst
`_install_from_staging` an — in EINEM Exec, das jedes Glied mit O_NOFOLLOW
oeffnet und danach nur noch relativ zu den offenen Deskriptoren schreibt.
Eine Flucht aus der Kette ist nur ueber einen Symlink moeglich (ein `mkdir`
legt ein Ersatz-Verzeichnis immer INNERHALB des gleichen Elternordners an, es
kann die Kette nicht verlassen) — genau das faengt O_NOFOLLOW ab, unabhaengig
vom Owner des eingetauschten Symlinks.

Dass dabei wirklich nichts mehr ausserhalb der Kette landet, misst
`test_toctou_kein_schreiben_ausserhalb_843.py` am echten Dateisystem; hier
stehen die Einzelteile (Skript, Reihenfolge, Vollstaendigkeit).
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
import uuid
from unittest.mock import MagicMock

from app.services import docker_service as ds_modul
from app.services.docker_service import (
    DockerService,
    ZielordnerKompromittiert,
    ZielordnerNichtVorbereitbar,
    _INSTALL_FROM_STAGING_SCRIPT,
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
INSTALL_SYMLINK_GETAUSCHT = (
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

    def _run(self, *parts, inhalt=b"nutzlast"):
        """Faehrt das ECHTE Uebernahme-Skript mit einem gefuellten
        Zwischenlager. Rueckgabe zusaetzlich: wo der Inhalt gelandet ist."""
        staging = os.path.join(self._tmp.name, "staging", uuid.uuid4().hex)
        os.makedirs(staging)
        with open(os.path.join(staging, "nutzlast.txt"), "wb") as fh:
            fh.write(inhalt)
        r = subprocess.run(
            [sys.executable, "-c", _INSTALL_FROM_STAGING_SCRIPT, self.root,
             str(os.getuid()), str(os.getgid()), staging, *parts],
            capture_output=True, text=True,
        )
        self.staging = staging
        return r

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
        compile(_INSTALL_FROM_STAGING_SCRIPT, "<install>", "exec")
        self.assertNotIn("import app", _INSTALL_FROM_STAGING_SCRIPT)

    def test_zwischenlager_wird_auch_nach_einer_ablehnung_entfernt(self):
        """Ein abgelehnter Schreibvorgang darf keine Nutzlast im
        Zwischenlager liegen lassen — sonst sammelt sich dort ueber die Zeit
        genau der Inhalt an, den der Aufrufer fuer nicht geschrieben haelt."""
        os.makedirs(os.path.join(self.root, "projects"))
        os.symlink(self.outside, os.path.join(self.root, "projects", "app"))
        r = self._run("projects", "app")
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(os.path.exists(self.staging), r.stdout)

    # --- Luecken aus der Mutationsbatterie zu #843 ------------------------
    # Die drei folgenden Faelle blieben GRUEN, als die jeweilige Sicherung im
    # Uebernahme-Skript abgeschaltet wurde — sie waren also ungeprueft.

    def test_unzulaessiges_kettenglied_wird_abgelehnt(self):
        """Die Kettenglieder kommen heute aus ``os.path.normpath`` und koennen
        deshalb kein ".." mehr enthalten. Die Pruefung im Skript ist die
        Absicherung gegen einen KUENFTIGEN Aufrufer, der die Glieder anders
        herleitet — ohne sie wuerde ein solcher Aufrufer die Kette per ".."
        nach oben verlassen, und zwar an den Deskriptoren vorbei."""
        os.makedirs(os.path.join(self.root, "projects"))
        for glied in ("..", ".", "", "projects/app"):
            with self.subTest(glied=glied):
                r = self._run("projects", glied)
                self.assertNotEqual(
                    r.returncode, 0,
                    f"Das Kettenglied {glied!r} wurde angenommen: {r.stdout}",
                )
                self.assertIn("unzulaessig", r.stdout)

    def test_zwischenlager_wird_auch_vor_dem_ketteneinstieg_entfernt(self):
        """Eine Ablehnung VOR dem Betreten der Kette laeuft nicht durch den
        finally-Zweig — das Aufraeumen muss deshalb schon in der
        Fehlerbehandlung selbst stehen. Sonst bleibt bei jedem solchen
        Abbruch die vollstaendige Nutzlast im Zwischenlager liegen."""
        os.makedirs(os.path.join(self.root, "projects"))
        r = self._run("projects", "..", inhalt=b"GEHEIMNIS")
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(
            os.path.exists(self.staging),
            f"Nutzlast blieb im Zwischenlager liegen: {r.stdout}",
        )

    def test_eintrag_im_zwischenlager_der_keine_datei_ist_wird_abgelehnt(self):
        """Der Docker-Daemon entpackt das Archiv ungefiltert — ein Eintrag,
        der weder Ordner noch normale Datei ist (Symlink, FIFO, Geraet),
        landet also so im Zwischenlager. Wuerde die Uebernahme ihn einfach
        weiterreichen, oeffnete ``open(pfad, "rb")`` beim Symlink die Datei
        AM ZIEL des Verweises und kopierte deren Inhalt in die Kette — ein
        Leseweg nach draussen, vorbei an allen Deskriptor-Sicherungen."""
        staging = os.path.join(self._tmp.name, "staging", uuid.uuid4().hex)
        os.makedirs(staging)
        geheim = os.path.join(self.outside, "schluessel.txt")
        with open(geheim, "wb") as fh:
            fh.write(b"GEHEIMNIS: liegt ausserhalb der Kette")
        os.symlink(geheim, os.path.join(staging, "beute.txt"))
        os.makedirs(os.path.join(self.root, "projects", "app"))

        r = subprocess.run(
            [sys.executable, "-c", _INSTALL_FROM_STAGING_SCRIPT, self.root,
             str(os.getuid()), str(os.getgid()), staging, "projects", "app"],
            capture_output=True, text=True,
        )

        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("weder Datei noch Ordner", r.stdout)
        self.assertFalse(
            os.path.exists(os.path.join(self.root, "projects", "app", "beute.txt")),
            "Der Inhalt einer Datei ausserhalb der Kette wurde hineinkopiert.",
        )

    def test_kurzschreibung_meldet_fehlschlag_statt_erfolg(self):
        """``os.write`` ist kein write_all: bei voller Platte schreibt es
        kuerzer, ohne zu melden. Ohne Auswertung endete das Skript regulaer
        mit 0 — der Aufrufer bekaeme Erfolg fuer eine ABGESCHNITTENE Datei.
        Die Schreibgrenze wird hier per RLIMIT_FSIZE gesetzt (SIGXFSZ
        ignoriert); das Kernel-Verhalten ist dasselbe wie bei ENOSPC."""
        import resource
        import signal

        grenze = 4096
        os.makedirs(os.path.join(self.root, "projects", "app"))
        staging = os.path.join(self._tmp.name, "staging", uuid.uuid4().hex)
        os.makedirs(staging)
        nutzlast = b"X" * (grenze * 4)
        with open(os.path.join(staging, "nutzlast.txt"), "wb") as fh:
            fh.write(nutzlast)

        def begrenzen():
            signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
            resource.setrlimit(resource.RLIMIT_FSIZE, (grenze, grenze))

        r = subprocess.run(
            [sys.executable, "-c", _INSTALL_FROM_STAGING_SCRIPT, self.root,
             str(os.getuid()), str(os.getgid()), staging, "projects", "app"],
            capture_output=True, text=True, preexec_fn=begrenzen,
        )

        self.assertNotEqual(
            r.returncode, 0,
            "Erfolg gemeldet, obwohl die Datei nicht vollstaendig ankam: "
            f"{r.stdout}{r.stderr}",
        )
        ziel = os.path.join(self.root, "projects", "app", "nutzlast.txt")
        if os.path.exists(ziel):
            self.assertNotEqual(
                os.path.getsize(ziel), len(nutzlast),
                "Testaufbau greift nicht: die Schreibgrenze hat nicht gewirkt.",
            )

    def test_nutzlast_landet_bei_heiler_kette_wirklich_im_ziel(self):
        os.makedirs(os.path.join(self.root, "projects", "app"))
        r = self._run("projects", "app", inhalt=b"hallo")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        ziel = os.path.join(self.root, "projects", "app", "nutzlast.txt")
        with open(ziel, "rb") as fh:
            self.assertEqual(fh.read(), b"hallo")
        self.assertFalse(os.path.exists(self.staging))

    def test_symlink_als_zieldatei_wird_nicht_durchgeschrieben(self):
        """Haertung, die put_archive nicht hatte: eine bereits als Symlink
        angelegte Zieldatei wird abgelehnt statt durchgeschrieben."""
        os.makedirs(os.path.join(self.root, "projects", "app"))
        beute = os.path.join(self.outside, "beute.txt")
        with open(beute, "w") as fh:
            fh.write("unberuehrt")
        os.symlink(beute, os.path.join(self.root, "projects", "app", "nutzlast.txt"))
        r = self._run("projects", "app", inhalt=b"uebernommen")
        self.assertNotEqual(r.returncode, 0, r.stdout)
        with open(beute) as fh:
            self.assertEqual(fh.read(), "unberuehrt")


class NachpruefungWirdNachDemSchreibenAufgerufenTests(unittest.TestCase):
    """Verhaltenstest ueber den gefaelschten Client: _install_from_staging
    laeuft NACH put_archive, und ein Fehlschlag dort markiert den bereits
    erfolgten Schreibvorgang als kompromittiert (ZielordnerKompromittiert)."""

    def test_write_file_in_container_erkennt_kompromittierte_kette(self):
        svc, container = _service()
        svc.exec_in_container = MagicMock(side_effect=[PREPARE_OK, INSTALL_SYMLINK_GETAUSCHT])

        with self.assertRaises(ZielordnerKompromittiert) as ctx:
            svc.write_file_in_container("c1", "/workspace/app/knowledge.md", "x")

        self.assertIn("Symlink", str(ctx.exception))
        # Der Kern der Meldung: put_archive lief bereits (Daten liegen im
        # Container), der Aufrufer soll das trotzdem nicht als Erfolg werten.
        self.assertEqual(len(container.archives), 1)
        self.assertEqual(svc.exec_in_container.call_count, 2)

    def test_write_files_in_container_erkennt_kompromittierte_kette(self):
        svc, container = _service()
        svc.exec_in_container = MagicMock(side_effect=[PREPARE_OK, INSTALL_SYMLINK_GETAUSCHT])

        with self.assertRaises(ZielordnerKompromittiert):
            svc.write_files_in_container("c1", "/workspace/app", [("a.txt", b"a")])

        self.assertEqual(len(container.archives), 1)
        self.assertEqual(svc.exec_in_container.call_count, 2)

    def test_put_archive_zielt_nie_auf_den_vom_agenten_erreichbaren_pfad(self):
        """Der Kern des Fixes: der Docker-Daemon bekommt den Zielpfad gar
        nicht mehr zu sehen — er schreibt in die root-eigene Staging-Wurzel,
        und jeder Archiv-Eintrag liegt unterhalb des Staging-Praefixes. Ein
        Rueckfall auf den Zielpfad waere hier sofort sichtbar, auch wenn alle
        Ablaeufe gruen bleiben."""
        praefix = ds_modul._IMPORT_STAGING_DIR + "/"
        for aufruf in (
            lambda svc: svc.write_file_in_container("c1", "/workspace/app/knowledge.md", "x"),
            lambda svc: svc.write_files_in_container("c1", "/workspace/app", [("src/a.txt", b"a")]),
        ):
            svc, container = _service()
            svc.exec_in_container = MagicMock(return_value=PREPARE_OK)
            aufruf(svc)
            ziel, rohdaten = container.archives[0]
            self.assertEqual(ziel, ds_modul._IMPORT_STAGING_PARENT)
            with tarfile.open(fileobj=io.BytesIO(rohdaten)) as tar:
                namen = tar.getnames()
            self.assertTrue(namen)
            for name in namen:
                self.assertTrue(
                    name == ds_modul._IMPORT_STAGING_DIR or name.startswith(praefix),
                    f"Archiv-Eintrag {name!r} liegt ausserhalb des Zwischenlagers",
                )
                self.assertNotIn("..", name.split("/"))

    def test_zwischenlager_ordner_gehoeren_root_und_sind_nur_fuer_root_begehbar(self):
        """Ohne das ist das Zwischenlager keines: koennte der Agent hinein,
        haette er den Symlink-Tausch nur an eine andere Stelle verlegt."""
        svc, container = _service()
        svc.exec_in_container = MagicMock(return_value=PREPARE_OK)
        svc.write_file_in_container("c1", "/workspace/app/knowledge.md", "x")
        with tarfile.open(fileobj=io.BytesIO(container.archives[0][1])) as tar:
            ordner = [m for m in tar.getmembers() if m.isdir()]
        self.assertEqual(len(ordner), 2, [m.name for m in ordner])
        for m in ordner:
            self.assertEqual((m.uid, m.gid), (0, 0), m.name)
            self.assertEqual(m.mode, 0o700, m.name)

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

    def _reihenfolge_put_archive_vor_uebernahme(self, funktion):
        """Formtest wie schon fuer prepare_target_dir/put_archive in
        test_import_zielordner_symlink.py: bei rc=0 sehen beide Reihenfolgen
        gleich gruen aus, deshalb am Quelltext pruefen statt am Verhalten."""
        quelle = inspect.getsource(funktion)
        baum = ast.parse(ast.unparse(ast.parse(quelle.strip())))
        namen = []
        for knoten in ast.walk(baum):
            if isinstance(knoten, ast.Call) and isinstance(knoten.func, ast.Attribute):
                if knoten.func.attr in ("put_archive", "_install_from_staging"):
                    namen.append((knoten.lineno, knoten.func.attr))
        namen.sort()
        return [n for _, n in namen]

    def test_uebernahme_laeuft_nach_put_archive_im_einzahl_helfer(self):
        self.assertEqual(
            self._reihenfolge_put_archive_vor_uebernahme(DockerService.write_file_in_container),
            ["put_archive", "_install_from_staging"],
        )

    def test_uebernahme_laeuft_nach_put_archive_im_mehrzahl_helfer(self):
        self.assertEqual(
            self._reihenfolge_put_archive_vor_uebernahme(DockerService.write_files_in_container),
            ["put_archive", "_install_from_staging"],
        )

    def test_beide_bekannten_put_archive_traeger_rufen_die_uebernahme(self):
        """Dieselbe Lehre wie in #840/#841: nicht den gemeldeten Aufrufer
        flicken, sondern JEDEN Traeger von put_archive erfassen. Ein neuer,
        dritter Aufrufer faellt hier durch, wenn er die Nachpruefung
        vergisst — analog zum bestehenden Sammel-Scan fuer
        prepare_target_dir in test_import_zielordner_symlink.py."""
        quelle = inspect.getsource(ds_modul)
        baum = ast.parse(quelle)
        traeger_ohne_uebernahme = []
        alle_traeger = set()
        for knoten in ast.walk(baum):
            if not isinstance(knoten, ast.FunctionDef):
                continue
            ruft_put_archive = False
            ruft_uebernahme = False
            for unter in ast.walk(knoten):
                if isinstance(unter, ast.Call) and isinstance(unter.func, ast.Attribute):
                    if unter.func.attr == "put_archive":
                        ruft_put_archive = True
                    elif unter.func.attr == "_install_from_staging":
                        ruft_uebernahme = True
            if not ruft_put_archive:
                continue
            alle_traeger.add(knoten.name)
            if not ruft_uebernahme:
                traeger_ohne_uebernahme.append(knoten.name)
        self.assertEqual(alle_traeger, {"write_file_in_container", "write_files_in_container"})
        self.assertEqual(traeger_ohne_uebernahme, [])


if __name__ == "__main__":
    unittest.main()

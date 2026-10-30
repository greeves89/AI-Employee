"""``/shared`` je Nutzer getrennt.

Vorher hing das Volume ``ai-employee-shared`` in JEDEM Agenten aller Nutzer. Live
auf einer Anlage: darin Dateien aus einem Kundenprojekt, das Token der
Teamlizenz (für alle lesbar), der gemeinsame Codex-Zugang und das
Fehlerprotokoll der Plattform mit Agenten und Fehlern aller Nutzer. Ein Agent
eines anderen Nutzers konnte das alles lesen.

Jetzt sieht ein Agent als ``/shared`` nur den Ordner seines Besitzers;
Zugangsdaten und Protokoll hängen nur lesend und nur dort, wo sie gebraucht werden.
Die Tests prüfen, was wirklich an Docker geht.
"""
import os
import stat
import tempfile
import unittest
from unittest import mock

from app.core import shared_volume
from app.core.shared_volume import geteilt_plan, nutzer_teilpfad
from app.services.docker_service import DockerService


class _FakeContainersApi:
    def __init__(self):
        self.run_calls = []

    def run(self, **kwargs):
        self.run_calls.append(kwargs)
        return object()


class _FakeVolumesApi:
    def get(self, name):
        return object()

    def create(self, name):
        return object()


class _FakeClient:
    def __init__(self, api: str):
        self.containers = _FakeContainersApi()
        self.volumes = _FakeVolumesApi()
        self._api = api

    def version(self):
        return {"ApiVersion": self._api}


def _run(api: str, geteilt: dict | None):
    svc = DockerService.__new__(DockerService)  # __init__ braucht einen echten Daemon
    svc.client = _FakeClient(api)
    svc.create_container(
        image="ai-employee-agent:latest", name="agent-a1", environment={"AGENT_ID": "a1"},
        volume_name="workspace-a1", network="net", shared_volume_name="ai-employee-shared",
        geteilt=geteilt,
    )
    return svc.client.containers.run_calls[0]


def _ziele(kwargs) -> dict:
    return {m["Target"]: m for m in kwargs.get("mounts") or []}


class Plan(unittest.TestCase):
    def test_teamlizenz_bekommt_nur_ihr_token(self):
        p = geteilt_plan("u1", mode="claude_code", env={}, ist_admin=False)
        self.assertEqual(p["nutzer"], "nutzer/u1")
        self.assertEqual(p["nur_lesen"], [(".auth", "/shared/.auth")])

    def test_eigenes_abo_sieht_das_teamtoken_nicht(self):
        p = geteilt_plan("u1", mode="claude_code", env={"CLAUDE_CREDENTIAL_SOURCE": "personal"}, ist_admin=False)
        self.assertEqual(p["nur_lesen"], [])

    def test_codex_bekommt_den_gemeinsamen_zugang_nur_ohne_eigenen(self):
        gemeinsam = geteilt_plan("u1", mode="codex_cli", env={}, ist_admin=False)
        self.assertEqual(gemeinsam["nur_lesen"], [(".codex", "/shared/.codex")])
        for env in ({"CODEX_AUTH_JSON": "{}"}, {"OPENAI_API_KEY": "x"}):
            with self.subTest(env=env):
                self.assertEqual(geteilt_plan("u1", mode="codex_cli", env=env, ist_admin=False)["nur_lesen"], [])

    def test_plattformprotokoll_nur_fuer_admins_und_besitzerlose(self):
        self.assertNotIn((".plattform", "/shared/.plattform"),
                         geteilt_plan("u1", mode="claude_code", env={}, ist_admin=False)["nur_lesen"])
        self.assertIn((".plattform", "/shared/.plattform"),
                      geteilt_plan("u1", mode="claude_code", env={}, ist_admin=True)["nur_lesen"])
        self.assertIn((".plattform", "/shared/.plattform"),
                      geteilt_plan(None, mode="claude_code", env={}, ist_admin=False)["nur_lesen"])

    def test_teilpfad_bleibt_im_nutzerordner(self):
        self.assertEqual(nutzer_teilpfad(None), "nutzer/ohne-besitzer")
        for boes in ("../../etc", "a/b", "x" * 80, "", ".."):
            with self.subTest(boes=boes):
                pfad = nutzer_teilpfad(boes)
                self.assertTrue(pfad.startswith("nutzer/"))
                self.assertNotIn("..", pfad[len("nutzer/"):].split("/"))
                self.assertEqual(pfad.count("/"), 1)


class AnDocker(unittest.TestCase):
    def tearDown(self):
        shared_volume.JE_NUTZER_AKTIV = True

    def test_neue_engine_haengt_nur_den_nutzerordner_ein(self):
        plan = geteilt_plan("u1", mode="claude_code", env={}, ist_admin=False)
        kwargs = _run("1.55", plan)
        self.assertNotIn("ai-employee-shared", kwargs["volumes"], "das ganze Volume darf nicht mehr rein")
        ziele = _ziele(kwargs)
        self.assertEqual(ziele["/shared"]["VolumeOptions"]["Subpath"], "nutzer/u1")
        self.assertFalse(ziele["/shared"]["ReadOnly"])
        self.assertEqual(ziele["/shared/.auth"]["VolumeOptions"]["Subpath"], ".auth")
        self.assertTrue(ziele["/shared/.auth"]["ReadOnly"])
        self.assertNotIn("/shared/.codex", ziele)
        self.assertNotIn("/shared/.plattform", ziele)
        self.assertTrue(shared_volume.JE_NUTZER_AKTIV)

    def test_alte_engine_faellt_auf_das_ganze_volume_zurueck(self):
        plan = geteilt_plan("u1", mode="claude_code", env={}, ist_admin=False)
        kwargs = _run("1.43", plan)
        self.assertEqual(kwargs["volumes"]["ai-employee-shared"], {"bind": "/shared", "mode": "rw"})
        self.assertNotIn("mounts", kwargs)
        self.assertFalse(shared_volume.JE_NUTZER_AKTIV)

    def test_ohne_plan_bleibt_alles_wie_bisher(self):
        kwargs = _run("1.55", None)
        self.assertEqual(kwargs["volumes"]["ai-employee-shared"], {"bind": "/shared", "mode": "rw"})


class OrdnerUndRechte(unittest.TestCase):
    def test_nutzerordner_mit_leeren_einhaengepunkten(self):
        with tempfile.TemporaryDirectory() as root, mock.patch("os.chown"):
            pfad = shared_volume.nutzer_ordner_anlegen("u1", root=root)
            self.assertEqual(pfad, os.path.join(root, "nutzer", "u1"))
            for sub in (".auth", ".codex", ".plattform"):
                self.assertTrue(os.path.isdir(os.path.join(pfad, sub)))
            self.assertEqual(stat.S_IMODE(os.stat(pfad).st_mode), shared_volume.SHARED_MODE)

    def test_vorhandenes_teamtoken_wird_auf_0640_gesetzt(self):
        with tempfile.TemporaryDirectory() as root, mock.patch("os.chown"):
            os.makedirs(os.path.join(root, ".auth"))
            token = os.path.join(root, ".auth", "token.json")
            with open(token, "w") as f:
                f.write("{}")
            os.chmod(token, 0o644)
            self.assertTrue(shared_volume.ensure_shared_volume_perms(root))
            self.assertEqual(stat.S_IMODE(os.stat(token).st_mode), 0o640)

    def test_agentensicht_folgt_der_engine(self):
        try:
            shared_volume.JE_NUTZER_AKTIV = True
            self.assertEqual(shared_volume.agent_sicht("u1", root="/shared"), "/shared/nutzer/u1")
            shared_volume.JE_NUTZER_AKTIV = False
            self.assertEqual(shared_volume.agent_sicht("u1", root="/shared"), "/shared")
        finally:
            shared_volume.JE_NUTZER_AKTIV = True


class TeamtokenDatei(unittest.TestCase):
    def test_token_wird_nicht_mehr_fuer_alle_lesbar_geschrieben(self):
        from app.services import claude_token_service as cts
        with tempfile.TemporaryDirectory() as root, mock.patch("os.chown"):
            pfad = os.path.join(root, ".auth", "token.json")
            with mock.patch.object(cts, "SHARED_TOKEN_PATH", pfad):
                svc = cts.ClaudeTokenService.__new__(cts.ClaudeTokenService)
                svc._write_shared_token("sk-ant-oat01-" + "a" * 80)
            self.assertTrue(os.path.exists(pfad))
            self.assertEqual(stat.S_IMODE(os.stat(pfad).st_mode) & 0o007, 0, "keine Rechte für alle")


if __name__ == "__main__":
    unittest.main()


class TaskforceBleibtImEigenenOrdner(unittest.TestCase):
    """Der Taskforce-Ordner liegt im beschreibbaren Bereich der Agenten. Ein Symlink
    darf den Orchestrator nicht in den Ordner eines anderen Nutzers führen."""

    def _raum(self):
        return mock.Mock(created_by="u1", id="raum1")

    def test_symlink_auf_fremden_nutzer_wird_abgewiesen(self):
        from fastapi import HTTPException
        from app.api import meeting_rooms
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "nutzer", "u1"))
            os.makedirs(os.path.join(root, "nutzer", "u2", "taskforce", "raum1"))
            # Agent von u1 ersetzt „taskforce“ durch einen Symlink auf den Ordner von u2.
            os.symlink(os.path.join(root, "nutzer", "u2", "taskforce"), os.path.join(root, "nutzer", "u1", "taskforce"))
            with mock.patch("app.core.shared_volume.SHARED_DIR", root), \
                    mock.patch("app.core.shared_volume.agent_sicht", lambda uid: os.path.join(root, "nutzer", uid)):
                with self.assertRaises(HTTPException):
                    meeting_rooms._taskforce_dir(self._raum())

    def test_raumordner_als_symlink_wird_abgewiesen(self):
        from fastapi import HTTPException
        from app.api import meeting_rooms
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "nutzer", "u1", "taskforce"))
            os.makedirs(os.path.join(root, "nutzer", "u2"))
            os.symlink(os.path.join(root, "nutzer", "u2"), os.path.join(root, "nutzer", "u1", "taskforce", "raum1"))
            with mock.patch("app.core.shared_volume.agent_sicht", lambda uid: os.path.join(root, "nutzer", uid)):
                with self.assertRaises(HTTPException):
                    meeting_rooms._taskforce_dir(self._raum())

    def test_normaler_ordner_liegt_beim_besitzer(self):
        from app.api import meeting_rooms
        with tempfile.TemporaryDirectory() as root:
            with mock.patch("app.core.shared_volume.agent_sicht", lambda uid: os.path.join(root, "nutzer", uid)):
                pfad = meeting_rooms._taskforce_dir(self._raum())
            self.assertEqual(pfad, os.path.join(os.path.realpath(root), "nutzer", "u1", "taskforce", "raum1"))

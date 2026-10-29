"""Push-Sperre (#1117): ``git push`` erkennen, Ziel-Branch bestimmen, Orchestrator fragen.

Gegen ein echtes, lokales git-Repo (Branches, Remote) — die Befehlsformen, die
Agenten tatsächlich schreiben. Der Orchestrator ist nachgestellt.
"""

import asyncio
import os
import subprocess
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from app.tools import push_sperre
from app.tools.pretooluse_hook import decide_async


def _git(verzeichnis, *argumente):
    subprocess.run(["git", "-C", verzeichnis, *argumente], check=True, capture_output=True)


class _Repo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = os.path.join(self.tmp.name, "repo")
        os.makedirs(self.repo)
        _git(self.repo, "init", "-q", "-b", "main")
        _git(self.repo, "-c", "user.email=a@example.invalid", "-c", "user.name=A", "commit", "-q",
             "--allow-empty", "-m", "start")
        _git(self.repo, "remote", "add", "origin", "https://x-access-token:geheim@github.com/o/r.git")
        _git(self.repo, "remote", "add", "zweit", "git@github.com:andere/repo.git")
        _git(self.repo, "checkout", "-q", "-b", "12-login-fehler")

    def tearDown(self):
        self.tmp.cleanup()

    def ziele(self, befehl, verzeichnis=None):
        return push_sperre.push_ziele(befehl, verzeichnis or self.repo)


class BefehleLesen(_Repo):
    def test_kein_push(self):
        for befehl in ("git status", "ls -la", "echo push", "git commit -m 'push it'", "git stash push"):
            with self.subTest(befehl=befehl):
                self.assertIsNone(self.ziele(befehl))

    def test_einfacher_push_nimmt_den_aktuellen_branch(self):
        self.assertEqual(self.ziele("git push"),
                         [{"branch": "12-login-fehler", "remote": "https://github.com/o/r.git"}])

    def test_zugangsdaten_gehen_nicht_mit(self):
        self.assertNotIn("geheim", str(self.ziele("git push origin 12-login-fehler")))

    def test_formen_von_refspecs(self):
        faelle = {
            "git push origin HEAD:feat/12-x": ["feat/12-x"],
            "git push -u origin feat/34-y": ["feat/34-y"],
            "git push --force origin 12-login-fehler": ["12-login-fehler"],
            "git push -f origin +main:refs/heads/56-z": ["56-z"],
            "git push --force-with-lease origin HEAD": ["12-login-fehler"],
            "git push origin :78-weg": ["78-weg"],
            "git push origin a b": ["a", "b"],
            "git push origin v1.0 refs/tags/v1.0": ["v1.0"],
            "git push --tags": ["12-login-fehler"],
        }
        for befehl, erwartet in faelle.items():
            with self.subTest(befehl=befehl):
                self.assertEqual([z["branch"] for z in self.ziele(befehl)], erwartet)

    def test_remote_wird_aufgeloest(self):
        self.assertEqual(self.ziele("git push zweit HEAD")[0]["remote"], "git@github.com:andere/repo.git")
        self.assertEqual(self.ziele("git push https://github.com/o/r.git HEAD:9-a")[0]["remote"],
                         "https://github.com/o/r.git")

    def test_ketten_huellen_und_verzeichnisse(self):
        oben = self.tmp.name
        faelle = [
            ("git add -A && git commit -m 'a; b' && git push origin HEAD", self.repo, "12-login-fehler", True),
            (f"cd {self.repo} && git push", oben, "12-login-fehler", True),
            (f"git -C {self.repo} push", oben, "12-login-fehler", True),
            ("echo los; git push origin 90-x 2>&1 | tail -5", self.repo, "90-x", True),
            ("FOO=1 timeout 30s git push origin 91-x", self.repo, "91-x", True),
            ("bash -c \"git push origin 92-x\"", self.repo, "92-x", True),
            ("git status\ngit push origin 93-x", self.repo, "93-x", True),
        ]
        for befehl, verzeichnis, branch, _ in faelle:
            with self.subTest(befehl=befehl):
                self.assertIn(branch, [z["branch"] for z in self.ziele(befehl, verzeichnis)])

    def test_push_ohne_erkennbaren_branch_ist_trotzdem_ein_push(self):
        # Ausserhalb eines Repos: der Branch ist unbekannt — die Takt-Regel gilt trotzdem.
        self.assertEqual(self.ziele("git push", self.tmp.name), [])

    def test_unlesbarer_befehl_sperrt_nicht(self):
        self.assertIsNone(self.ziele("git push 'offen"))


class _Antwort:
    def __init__(self, status, daten=None):
        self.status_code, self._daten = status, daten

    def json(self):
        return self._daten


class _Client:
    def __init__(self, antwort=None, fehler=None):
        self.antwort, self.fehler, self.gesendet = antwort, fehler, []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None):
        self.gesendet.append((url, headers, json))
        if self.fehler:
            raise self.fehler
        return self.antwort


class OrchestratorFragen(_Repo):
    def _pruefen(self, client, befehl="git push origin HEAD:12-fix", lauf="t-1"):
        with patch("httpx.AsyncClient", return_value=client):
            return asyncio.run(push_sperre.pruefen(befehl, self.repo, lauf))

    def test_sperrgrund_kommt_durch_und_lauf_geht_mit(self):
        client = _Client(_Antwort(200, {"erlaubt": False, "grund": "Push auf `12-fix` gesperrt: …"}))
        self.assertIn("gesperrt", self._pruefen(client))
        url, kopf, daten = client.gesendet[0]
        self.assertTrue(url.endswith("/api/v1/teams/repo/push-pruefen"))
        self.assertIn("Authorization", kopf)
        self.assertEqual(daten["lauf"], "t-1")
        self.assertEqual(daten["ziele"], [{"branch": "12-fix", "remote": "https://github.com/o/r.git"}])

    def test_erlaubt(self):
        self.assertIsNone(self._pruefen(_Client(_Antwort(200, {"erlaubt": True}))))

    def test_fail_open(self):
        self.assertIsNone(self._pruefen(_Client(fehler=OSError("Verbindung abgelehnt"))))
        self.assertIsNone(self._pruefen(_Client(_Antwort(404, {"detail": "Not Found"}))))
        self.assertIsNone(self._pruefen(_Client(_Antwort(500))))

    def test_ohne_push_keine_frage(self):
        client = _Client(_Antwort(200, {"erlaubt": False, "grund": "x"}))
        self.assertIsNone(self._pruefen(client, befehl="git status"))
        self.assertEqual(client.gesendet, [])


class HookUndExecutor(unittest.TestCase):
    """Dieselbe Entscheidung im Hook (Claude Code, Codex) und im Custom-LLM-Executor."""

    def setUp(self):
        self.erlaubt = patch("app.tools.pretooluse_hook._get_allowed_categories", return_value=None)
        self.policy = patch("app.tools.pretooluse_hook._evaluate_command_policy",
                            new=AsyncMock(return_value=(None, None)))
        self.erlaubt.start()
        self.policy.start()

    def tearDown(self):
        self.policy.stop()
        self.erlaubt.stop()

    def test_hook_sperrt_mit_dem_grund_des_orchestrators(self):
        pruefen = AsyncMock(return_value="Push gesperrt: Dies ist ein Lauf des Arbeitstakts.")
        with patch("app.tools.push_sperre.pruefen", new=pruefen):
            aus = asyncio.run(decide_async("Bash", {"command": "git push"}, cwd="/workspace/r", lauf="t-takt"))
        self.assertEqual(aus["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("Arbeitstakt", aus["hookSpecificOutput"]["permissionDecisionReason"])
        pruefen.assert_awaited_once_with("git push", "/workspace/r", "t-takt")

    def test_hook_codex_befehl_als_liste(self):
        pruefen = AsyncMock(return_value=None)
        with patch("app.tools.push_sperre.pruefen", new=pruefen):
            aus = asyncio.run(decide_async("Bash", {"command": ["git", "push", "origin", "HEAD"]}))
        self.assertEqual(aus["hookSpecificOutput"]["permissionDecision"], "allow")
        pruefen.assert_awaited_once_with("git push origin HEAD", None, None)

    def test_executor_sperrt_und_kennt_seinen_lauf(self):
        from app.tools.executor import ToolExecutor

        ex = ToolExecutor(workspace_dir=tempfile.gettempdir())
        ex.lauf = "t-takt"
        pruefen = AsyncMock(return_value="Push gesperrt: Dies ist ein Lauf des Arbeitstakts.")
        with patch("app.tools.executor._evaluate_command_policy", new=AsyncMock(return_value=(None, None))), \
                patch("app.tools.push_sperre.pruefen", new=pruefen):
            aus = asyncio.run(ex._tool_bash({"command": "git push origin HEAD"}))
        self.assertTrue(aus.startswith("[PUSH GESPERRT]"))
        pruefen.assert_awaited_once_with("git push origin HEAD", ex.workspace_dir, "t-takt")


class HealthHandler(unittest.TestCase):
    def test_lauf_aus_kopfzeile_und_cwd_aus_dem_aufruf(self):
        from app import health

        class _Anfrage:
            headers = {push_sperre.LAUF_HEADER: "t-7"}

            async def json(self):
                return {"tool_name": "Bash", "tool_input": {"command": "git push"}, "cwd": "/workspace/r"}

        entscheiden = AsyncMock(return_value={"hookSpecificOutput": {"permissionDecision": "allow"}})
        with patch("app.tools.pretooluse_hook.decide_async", new=entscheiden):
            asyncio.run(health.pretooluse_hook_handler(_Anfrage()))
        entscheiden.assert_awaited_once_with("Bash", {"command": "git push"}, cwd="/workspace/r", lauf="t-7")


if __name__ == "__main__":
    unittest.main()

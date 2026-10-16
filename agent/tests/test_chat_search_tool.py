"""Werkzeug ``chat_search``: das Gespräch setzt die Laufzeit, nie das Modell.

``ChatMessage`` kennt keinen Nutzer, ein Agent kann mehreren Menschen gehören.
Deshalb sucht ``chat_search`` nur im LAUFENDEN Gespräch, und das kommt

* bei Custom-LLM aus ``current_chat_session`` (der Chat-Verbraucher setzt es),
* bei Claude Code und Codex aus ``AI_CHAT_SESSION`` in der Umgebung der CLI, die
  der MCP-Server (orchestrator-server.mjs) erbt — Codex nur über ``env_vars``.

Gegenprobe: ausserhalb eines Gesprächs (Aufgabe) wird nichts durchsucht, und eine
Kennung aus den Werkzeug-Argumenten wird nicht beachtet.
"""

import asyncio
import os
import tempfile
import tomllib
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.tools.api_client import (
    CHAT_SEARCH_NO_SESSION,
    CHAT_SESSION_ENV,
    OrchestratorAPIClient,
    chat_suche_env,
    current_chat_session,
)


class _Recorder(OrchestratorAPIClient):
    def __init__(self, antwort=None):
        self.agent_id = "agent-1"
        self.agent_name = "Agent"
        self.calls: list[tuple] = []
        self.antwort = antwort if antwort is not None else {"total": 0, "matches": []}

    async def _request(self, method, path, json=None, params=None):  # noqa: A002
        self.calls.append((method, path, params))
        return self.antwort


def _im_gespraech(session, coro_factory):
    async def _lauf():
        token = current_chat_session.set(session)
        try:
            return await coro_factory()
        finally:
            current_chat_session.reset(token)
    return asyncio.run(_lauf())


class CustomLlmTests(unittest.TestCase):
    def test_ohne_gespraech_wird_nichts_gesucht(self):
        client = _Recorder()
        out = asyncio.run(client.chat_search({"query": "Rechnung"}))
        self.assertEqual(out, CHAT_SEARCH_NO_SESSION)
        self.assertEqual(client.calls, [])

    def test_das_gespraech_kommt_aus_der_laufzeit(self):
        client = _Recorder({
            "total": 4,
            "matches": [{"message_id": "m9", "role": "user",
                         "timestamp": "2026-10-01T09:00:00", "excerpt": "Rechnung 4711"}],
        })
        out = _im_gespraech("sitzung-1", lambda: client.chat_search(
            {"query": " Rechnung 4711 ", "limit": 99, "session_id": "fremd"}))
        method, path, params = client.calls[0]
        self.assertEqual((method, path), ("GET", "/agents/agent-1/chat/search"))
        self.assertEqual(params, {"session_id": "sitzung-1", "q": "Rechnung 4711", "limit": 30})
        self.assertIn("1 of 4 matches", out)
        self.assertIn('[EXTERNAL-DATA source="chat-history"]', out)
        self.assertIn("user (m9): Rechnung 4711", out)

    def test_leere_und_zu_lange_anfragen(self):
        client = _Recorder()
        self.assertIn("empty", _im_gespraech("s", lambda: client.chat_search({"query": " "})))
        self.assertIn("too long", _im_gespraech("s", lambda: client.chat_search({"query": "x" * 201})))
        self.assertEqual(client.calls, [])

    def test_fehler_der_gegenstelle_wird_durchgereicht(self):
        client = _Recorder("API error 403: Kein Zugriff")
        out = _im_gespraech("s", lambda: client.chat_search({"query": "x"}))
        self.assertEqual(out, "API error 403: Kein Zugriff")

    def test_das_werkzeug_ist_ueberall_eingetragen(self):
        from app.llm_chat_handler import CORE_TOOL_NAMES
        from app.tools.definitions import ORCHESTRATOR_TOOL_NAMES, ORCHESTRATOR_TOOLS
        from app.tools.executor import ALWAYS_ALLOWED_TOOLS, CONCURRENT_SAFE_TOOLS, _CACHEABLE_TOOLS

        self.assertIn("chat_search", ORCHESTRATOR_TOOL_NAMES)
        self.assertIn("chat_search", ALWAYS_ALLOWED_TOOLS)
        self.assertIn("chat_search", CONCURRENT_SAFE_TOOLS)
        self.assertIn("chat_search", CORE_TOOL_NAMES)
        # Der Zwischenspeicher kennt das Gespraech nicht — er darf es nie haben.
        self.assertNotIn("chat_search", _CACHEABLE_TOOLS)
        schema = next(t for t in ORCHESTRATOR_TOOLS if t["function"]["name"] == "chat_search")
        self.assertNotIn("session_id", schema["function"]["parameters"]["properties"])


class UmgebungTests(unittest.TestCase):
    def test_im_gespraech_gesetzt(self):
        env = _im_gespraech("s1", lambda: asyncio.sleep(0, result=chat_suche_env({})))
        self.assertEqual(env, {CHAT_SESSION_ENV: "s1"})

    def test_ohne_gespraech_entfernt(self):
        self.assertEqual(chat_suche_env({CHAT_SESSION_ENV: "alt", "PATH": "/bin"}), {"PATH": "/bin"})


class _Abbruch(Exception):
    pass


def _cli_umgebung(start):
    """Startet einen Zug, faengt den CLI-Start ab und gibt dessen Umgebung zurueck."""
    gesehen: dict = {}

    async def _exec(*_args, **kwargs):
        gesehen.update(kwargs.get("env") or {})
        raise _Abbruch()

    async def _lauf():
        with patch("asyncio.create_subprocess_exec", side_effect=_exec):
            try:
                await start()
            except _Abbruch:
                pass
    return _lauf, gesehen


class CodexTests(unittest.TestCase):
    def _runner(self):
        from app.codex_runner import CodexAgentRunner
        publisher = MagicMock()
        publisher.publish = AsyncMock()
        publisher.publish_chat = AsyncMock()
        return CodexAgentRunner(publisher)

    def _umgebung(self, stream, session):
        runner = self._runner()
        lauf, gesehen = _cli_umgebung(lambda: runner._run_codex("m1", "hi", "gpt-5", stream=stream))
        with patch("app.codex_runner._codex_auth_problem", return_value=None), \
             patch("app.codex_runner._codex_kennt_hook_trust", return_value=False), \
             patch("app.codex_runner._codex_env", return_value={CHAT_SESSION_ENV: "fremd"}):
            _im_gespraech(session, lauf)
        return gesehen

    def test_chat_zug_traegt_das_gespraech(self):
        self.assertEqual(self._umgebung("chat", "s1").get(CHAT_SESSION_ENV), "s1")

    def test_aufgabe_traegt_keines(self):
        self.assertNotIn(CHAT_SESSION_ENV, self._umgebung("task", "s1"))

    def test_config_reicht_die_variable_an_den_orchestrator_server_durch(self):
        from app.codex_runner import _ensure_codex_mcp_config

        heim = tempfile.mkdtemp(prefix="codex-home-")
        env = {"AGENT_ID": "a1", "AGENT_TOKEN": "t", "ORCHESTRATOR_URL": "http://orchestrator:8000"}
        with patch("os.path.exists", return_value=True):
            _ensure_codex_mcp_config(heim, env)
        with open(os.path.join(heim, "config.toml"), "rb") as f:
            cfg = tomllib.load(f)
        server = cfg["mcp_servers"]
        self.assertEqual(server["orchestrator"]["env_vars"], [CHAT_SESSION_ENV])
        # Nur dort: die anderen Server brauchen das Gespraech nicht.
        self.assertFalse([n for n, s in server.items() if n != "orchestrator" and "env_vars" in s])
        # Und nicht als fester Wert — der gaelte fuer jeden Lauf.
        self.assertNotIn(CHAT_SESSION_ENV, server["orchestrator"]["env"])


class ClaudeCodeTests(unittest.TestCase):
    def _umgebung(self, session):
        from app.chat_handler import ChatHandler

        publisher = MagicMock()
        publisher.publish_chat = AsyncMock()
        handler = ChatHandler(publisher)
        lauf, gesehen = _cli_umgebung(lambda: handler._execute_cli("m1", "hi", "claude-sonnet-4-6"))
        with patch.dict(os.environ, {CHAT_SESSION_ENV: "fremd"}):
            _im_gespraech(session, lauf)
        return gesehen

    def test_chat_zug_traegt_das_gespraech(self):
        self.assertEqual(self._umgebung("s1").get(CHAT_SESSION_ENV), "s1")

    def test_ohne_gespraech_keines(self):
        self.assertNotIn(CHAT_SESSION_ENV, self._umgebung(None))


if __name__ == "__main__":
    unittest.main()

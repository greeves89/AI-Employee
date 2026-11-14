"""Automatisches Wecken beachtet den Stopp durch den Nutzer (Plan Sicherheit 2026-11, Punkt 2).

Am 13.11.2026 liefen sieben Agenten wieder, nachdem der Nutzer sie angehalten
hatte. „Alle aktualisieren“ ist seit 1.458 behoben. Aufgaben, Delegationen,
Projekt-Takt und Wächter weckten über ``wake_agent`` aber weiter. Jetzt prüft
``wake_agent`` selbst: angehalten heißt, nur ein Mensch weckt (``durch_mensch``).

MC/DC über (angehalten, durch_mensch): nur (ja, nein) bleibt aus.
"""

import inspect
import unittest
from types import SimpleNamespace

from app.core.agent_duty import ANGEHALTEN_VOM_NUTZER
from app.models.agent import AgentState
from app.services import user_lifecycle


class _Db:
    def __init__(self, agent):
        self.agent, self.commits = agent, 0

    async def scalar(self, _stmt):
        return self.agent

    async def commit(self):
        self.commits += 1


class _Docker:
    def __init__(self):
        self.gestartet = []

    def get_container_status(self, _cid):
        return "running" if self.gestartet else "exited"

    def start_container(self, cid):
        self.gestartet.append(cid)


def _agent(angehalten: bool):
    return SimpleNamespace(id="a1", name="A", state=AgentState.STOPPED, container_id="c1",
                           config={ANGEHALTEN_VOM_NUTZER: True} if angehalten else {})


class WeckenTests(unittest.IsolatedAsyncioTestCase):
    async def _wecken(self, angehalten, durch_mensch):
        docker = _Docker()
        ok = await user_lifecycle.wake_agent(_Db(_agent(angehalten)), docker, "a1", durch_mensch=durch_mensch)
        return ok, docker.gestartet

    async def test_angehalten_automatisch_bleibt_aus(self):
        self.assertEqual(await self._wecken(True, False), (False, []))

    async def test_angehalten_durch_mensch_startet(self):
        self.assertEqual(await self._wecken(True, True), (True, ["c1"]))

    async def test_nicht_angehalten_automatisch_startet(self):
        self.assertEqual(await self._wecken(False, False), (True, ["c1"]))


class AufruferTests(unittest.TestCase):
    """Nur der Chat (Mensch, der verwaltet) und Telegram an den eigenen Bot dürfen
    einen angehaltenen Agenten wecken. Ein neuer Aufrufer mit ``durch_mensch=True``
    fällt hier auf."""

    def test_nur_menschliche_wege_wecken_angehaltene(self):
        import pathlib

        wurzel = pathlib.Path(user_lifecycle.__file__).resolve().parents[1]
        menschlich = set()
        for datei in wurzel.rglob("*.py"):
            text = datei.read_text(encoding="utf-8")
            if "wake_agent(" in text and "durch_mensch=" in text and datei.name != "user_lifecycle.py":
                menschlich.add(datei.relative_to(wurzel).as_posix())
        self.assertEqual(menschlich, {"api/ws.py", "telegram/agent_bot.py"})

    def test_telegram_weckt_nur_den_eigenen_agenten(self):
        from app.telegram import agent_bot

        quelle = inspect.getsource(agent_bot)
        self.assertIn("durch_mensch=target_agent_id in (None, self.agent_id)", quelle)

    def test_chat_weckt_nur_wer_verwaltet(self):
        from app.api import ws

        quelle = inspect.getsource(ws.ws_agent_chat)
        self.assertIn("durch_mensch=not _nur_nutzen", quelle)


if __name__ == "__main__":
    unittest.main()

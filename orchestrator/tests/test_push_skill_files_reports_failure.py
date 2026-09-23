"""Issue #841 (Nebenbefund): ``_push_skill_files_to_agent`` fing jede
Exception ab und protokollierte nur eine Warnung — der Endpunkt meldete dem
Aufrufer danach trotzdem status=assigned/installed. Eine abgelehnte Zielkette
(z.B. Symlink-Vorbereitung) sah damit wie ein Erfolg aus.

Der Fix macht den Ausgang sichtbar (Rueckgabewert + "files_pushed" im
Endpunkt-Response), statt ihn nur wegzuloggen.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.api import skill_marketplace as sm


def _fake_db(agent):
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=agent)
    db.execute = AsyncMock(return_value=result)
    return db


def _fake_request(docker):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(docker=docker)))


class PushSkillFilesReturnsSuccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_keine_dateien_gilt_als_erfolg(self):
        with patch.object(sm, "get_all_files_for_agent", return_value=[]):
            ok = await sm._push_skill_files_to_agent(
                _fake_request(MagicMock()), _fake_db(None), 1, "demo", "agent-1"
            )
        self.assertTrue(ok)

    async def test_fehlender_container_meldet_fehlschlag_statt_stillem_erfolg(self):
        agent = SimpleNamespace(container_id=None)
        with patch.object(sm, "get_all_files_for_agent", return_value=[("a.txt", b"x")]):
            ok = await sm._push_skill_files_to_agent(
                _fake_request(MagicMock()), _fake_db(agent), 1, "demo", "agent-1"
            )
        self.assertFalse(ok)

    async def test_abgelehnte_zielkette_meldet_fehlschlag_statt_stillem_erfolg(self):
        agent = SimpleNamespace(container_id="c1")
        docker = MagicMock()
        docker.write_files_in_container.side_effect = RuntimeError("Symlink abgelehnt")
        with patch.object(sm, "get_all_files_for_agent", return_value=[("a.txt", b"x")]):
            ok = await sm._push_skill_files_to_agent(
                _fake_request(docker), _fake_db(agent), 1, "demo", "agent-1"
            )
        self.assertFalse(ok)

    async def test_erfolgreicher_push_meldet_erfolg(self):
        agent = SimpleNamespace(container_id="c1")
        docker = MagicMock()
        with patch.object(sm, "get_all_files_for_agent", return_value=[("a.txt", b"x")]):
            ok = await sm._push_skill_files_to_agent(
                _fake_request(docker), _fake_db(agent), 1, "demo", "agent-1"
            )
        self.assertTrue(ok)
        docker.write_files_in_container.assert_called_once()


if __name__ == "__main__":
    unittest.main()

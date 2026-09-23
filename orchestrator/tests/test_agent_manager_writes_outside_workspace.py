"""Issue #841: die zwei Stellen in AgentManager, die legitim ausserhalb von
/workspace schreiben (Sudoers-Datei, geteilte Team-Registrierung), muessen
ihre eigene Wurzel UND — fuer die Sudoers-Datei entscheidend — root-uid/gid
bewusst an write_file_in_container mitgeben. Ohne das lehnt der gehaertete
Helfer seit #841 ab (Ziel ausserhalb der Vorgabe-Wurzel /workspace), oder
schlimmer: ein Aufruf ohne explizite uid/gid wuerde /etc/sudoers.d auf den
Agenten chownen.
"""

import json
import unittest
from unittest.mock import MagicMock

from app.core.agent_manager import AgentManager
from app.services.docker_service import DockerService


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


def _manager():
    container = _FakeContainer()
    svc = DockerService.__new__(DockerService)
    svc.client = _FakeClient(container)
    svc.exec_in_container = MagicMock(return_value=(0, ""))
    mgr = AgentManager(db=None, docker=svc, redis=None)
    return mgr, svc, container


class SudoersDateiTests(unittest.TestCase):
    def test_wurzel_und_root_uid_werden_bewusst_mitgegeben(self):
        mgr, svc, container = _manager()
        mgr._apply_permissions("c1", ["full-access"])

        prep_cmd = svc.exec_in_container.call_args_list[0].args[1]
        self.assertEqual(prep_cmd[3:], ["/etc", "0", "0", "sudoers.d"])
        self.assertEqual(len(container.archives), 1)

    def test_ohne_permissions_wird_nicht_geschrieben(self):
        mgr, svc, container = _manager()
        mgr._apply_permissions("c1", [])
        self.assertEqual(container.archives, [])


class TeamRegistryTests(unittest.TestCase):
    def test_wurzel_shared_wird_bewusst_mitgegeben(self):
        mgr, svc, container = _manager()
        mgr._update_team_registry("c1", "agent-1", "Nina", "dev")

        prep_cmd = svc.exec_in_container.call_args_list[-1].args[1]
        self.assertEqual(prep_cmd[3:], ["/shared", "1000", "1000"])
        self.assertEqual(len(container.archives), 1)
        dir_path, tar_bytes = container.archives[0]
        self.assertEqual(dir_path, "/shared")


if __name__ == "__main__":
    unittest.main()

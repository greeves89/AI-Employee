"""Ein Agent muss startbar bleiben, wenn sein Container schon dasteht.

Nutzerbericht vom 18.08.2026: eine Datei riss die Agentenseite mit, und in der
Browserkonsole stand ausserdem

    POST /api/v1/agents/<id>/start   500 Internal Server Error

Im Log der Anlage die ganze Kette:

    NotFound:  No such container: 06644c…        (die gemerkte Kennung ist alt)
    -> neu erstellen
    APIError:  Conflict. The container name "ai-agent-…" is already in use
               by container "1bab6c7e…"

Die Datenbank kannte eine Kennung, die Wirklichkeit einen Namen. Zwischen dem
Abraeumen und dem Anlegen hatte ein zweiter Weg denselben Agenten aufgebaut —
der Verlierer des Rennens gab eine nackte 500 zurueck.

Den fertigen Container zu loeschen waere falsch: er koennte bereits arbeiten.
Also wird er uebernommen.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.core.agent_manager import AgentManager
from docker.errors import APIError


NAME = "ai-agent-testagent-a1"
KONFLIKT = (
    f'409 Client Error: Conflict. The container name "/{NAME}" is already in use '
    'by container "1bab6c7e". You have to remove (or rename) that container.'
)


def _manager(vorhandener):
    m = AgentManager.__new__(AgentManager)
    m.docker = SimpleNamespace(client=SimpleNamespace(containers=MagicMock()))
    m.docker.client.containers.get.return_value = vorhandener
    return m


class AdoptingAnExistingContainerTests(unittest.TestCase):
    def test_the_normal_case_just_creates(self):
        neu = SimpleNamespace(id="neu", status="running", start=MagicMock())
        m = _manager(None)
        self.assertIs(m._create_or_adopt(NAME, lambda: neu), neu)

    def test_a_name_conflict_adopts_instead_of_failing(self):
        """Das war die 500."""
        da = SimpleNamespace(id="1bab6c7e", status="running", start=MagicMock())
        m = _manager(da)
        def anlegen():
            raise APIError(KONFLIKT)
        self.assertIs(m._create_or_adopt(NAME, anlegen), da)
        m.docker.client.containers.get.assert_called_once_with(NAME)

    def test_an_adopted_container_is_started_if_it_lies_still(self):
        """Uebernehmen allein genuegt nicht — der Nutzer hat auf Start
        gedrueckt und will danach einen laufenden Agenten."""
        da = SimpleNamespace(id="1bab6c7e", status="exited", start=MagicMock())
        m = _manager(da)
        m._create_or_adopt(NAME, lambda: (_ for _ in ()).throw(APIError(KONFLIKT)))
        da.start.assert_called_once()

    def test_a_running_container_is_not_started_again(self):
        da = SimpleNamespace(id="1bab6c7e", status="running", start=MagicMock())
        m = _manager(da)
        m._create_or_adopt(NAME, lambda: (_ for _ in ()).throw(APIError(KONFLIKT)))
        da.start.assert_not_called()

    def test_any_other_docker_error_still_surfaces(self):
        """Ein voller Datentraeger oder ein fehlendes Abbild darf NICHT als
        „schon da" durchgehen — sonst verschluckt die Uebernahme echte Fehler."""
        m = _manager(None)
        def anlegen():
            raise APIError("500 Server Error: no space left on device")
        with self.assertRaises(APIError):
            m._create_or_adopt(NAME, anlegen)
        m.docker.client.containers.get.assert_not_called()


class BothRecreatePathsUseItTests(unittest.TestCase):
    """Es gibt ZWEI Wege, die einen Agenten-Container neu aufbauen
    (``restart_agent`` und ``update_agent``). Genau solche Doppelungen sind an
    diesem Tag schon zweimal auseinandergelaufen."""

    def test_no_recreate_path_calls_docker_directly(self):
        from pathlib import Path
        quelle = (Path(__file__).resolve().parents[1] / "app/core/agent_manager.py").read_text()
        for block in quelle.split("def _create_agent_container():")[1:]:
            with self.subTest(stelle=block[:60].strip()):
                self.assertIn("_create_or_adopt", block[:400])


if __name__ == "__main__":
    unittest.main()

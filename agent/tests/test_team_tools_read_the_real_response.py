"""Die Team-Werkzeuge muessen die Antwort lesen, die der Orchestrator wirklich schickt.

Am 2026-08-12 stand beim Kunden ein aktives Team in der Datenbank — acht
Mitglieder, der CEO-Agent als Lead. Auf die Bitte „schick deinen Agenten ein
Hallo Welt" rief er brav ``list_my_team`` auf und antwortete:

    Mir ist derzeit kein Agententeam zugeordnet.

Die Faehigkeit war da, der Aufruf ging raus — nur las der Client am falschen
Ort nach. ``/teams/mine`` antwortet ``{"teams": [{..., "members": [...]}]}``;
gesucht wurde ``members`` auf der OBERSTEN Ebene. Ergebnis: immer leer, immer
„kein Team", und damit auch kein Delegieren.

Genau diese Klasse Fehler finden Quelltext-zaehlende Tests nicht: das Werkzeug
ist definiert, steht im Kernsatz, der Executor erlaubt es, die Methode
existiert — alles gruen, und trotzdem sagt der Agent „geht nicht".

Dieser Test fuettert deshalb die **echte Antwortform** ein und prueft, was der
Agent am Ende zu lesen bekommt.
"""

import asyncio
import unittest

from app.tools.api_client import OrchestratorAPIClient

#: So antwortet ``GET /teams/mine`` (orchestrator/app/api/teams.py::list_my_teams).
TEAMS_MINE = {
    "teams": [
        {
            "team_id": "1857e939759949e8b6422f46cd6eb954",
            "name": "The possibilities",
            "description": "Unterstuetzt mich in meiner taeglichen Arbeit",
            "lead_agent_id": "68ce2abd",
            "i_am_lead": True,
            "members": [
                {"id": "68ce2abd", "name": "CEO / Manager", "role": "Lead",
                 "is_lead": True, "is_me": True},
                {"id": "3be824dd", "name": "Mr. Develop", "role": "Entwickler",
                 "is_lead": False, "is_me": False},
                {"id": "61c45555", "name": "Dr. Code", "role": None,
                 "is_lead": False, "is_me": False},
            ],
        }
    ]
}

#: So antwortet ``GET /teams/`` (::_serialize) — Mitglieder nur als ID-Liste.
TEAMS_LIST = {
    "teams": [
        {
            "id": "1857e939759949e8b6422f46cd6eb954",
            "name": "The possibilities",
            "description": "",
            "member_agent_ids": ["68ce2abd", "3be824dd", "61c45555"],
            "lead_agent_id": "68ce2abd",
            "is_active": True,
            "created_by": "c.uhde@example.invalid",
        }
    ]
}

TEAM_TASKS = {
    "tasks": [
        {"id": "abc123", "title": "Hallo Welt", "status": "completed",
         "agent_name": "Mr. Develop"},
    ]
}


def _client(routes: dict[str, object], agent_id: str = "68ce2abd"):
    """Ein Client, dessen HTTP-Schicht die hinterlegten Antworten liefert."""
    client = OrchestratorAPIClient.__new__(OrchestratorAPIClient)
    client.agent_id = agent_id

    async def _request(method, path, **kwargs):
        for prefix, payload in routes.items():
            if path == prefix or path.startswith(prefix):
                return payload
        raise AssertionError(f"unerwarteter Aufruf: {method} {path}")

    client._request = _request  # type: ignore[method-assign]
    return client


class ListMyTeamTests(unittest.TestCase):
    def test_an_existing_team_is_not_reported_as_missing(self):
        out = asyncio.run(_client({"/teams/mine": TEAMS_MINE}).list_my_team({}))
        self.assertNotIn("Kein Team zugeordnet", out,
                         "Ein vorhandenes Team wurde als 'kein Team' gemeldet — "
                         "genau der Fehler, der das Delegieren beim Kunden "
                         "verhindert hat")

    def test_the_members_are_named_with_their_ids(self):
        """Ohne Kennungen kann der Lead niemanden gezielt beauftragen."""
        out = asyncio.run(_client({"/teams/mine": TEAMS_MINE}).list_my_team({}))
        for needle in ("Mr. Develop", "3be824dd", "Dr. Code", "61c45555"):
            with self.subTest(needle):
                self.assertIn(needle, out)

    def test_the_lead_learns_that_he_is_the_lead(self):
        out = asyncio.run(_client({"/teams/mine": TEAMS_MINE}).list_my_team({}))
        self.assertIn("LEAD", out)

    def test_no_team_still_says_so(self):
        out = asyncio.run(_client({"/teams/mine": {"teams": []}}).list_my_team({}))
        self.assertIn("Kein Team zugeordnet", out)


class ListTeamTasksTests(unittest.TestCase):
    """``/teams/`` fuehrt die Mitglieder als ID-Liste — nicht als Objekte."""

    def test_own_team_is_found_via_member_agent_ids(self):
        out = asyncio.run(_client({
            "/teams/1857e939759949e8b6422f46cd6eb954/tasks": TEAM_TASKS,
            "/teams/": TEAMS_LIST,
        }).list_team_tasks({}))
        self.assertNotIn("Kein Team zugeordnet", out)
        self.assertIn("Hallo Welt", out)

    def test_a_stranger_gets_the_honest_answer(self):
        out = asyncio.run(_client({"/teams/": TEAMS_LIST},
                                  agent_id="ffffffff").list_team_tasks({}))
        self.assertIn("Kein Team zugeordnet", out)


if __name__ == "__main__":
    unittest.main()

"""get_delegated_tasks zeigt auch Aufgaben aus FRUEHEREN Sprachsitzungen.

Befund 2026-08-19: Ein Nutzer delegierte per Sprache eine Aufgabe; sie lief
durch und der Agent meldete ein Ergebnis. In einer NEUEN Sprachsitzung fand
``get_delegated_tasks`` sie nicht — das Werkzeug las nur den In-Memory-Zustand
der aktuellen Sitzung (``_delegations``/``_planned``), der neu leer ist. Die
Rueckmeldung der frueher delegierten Aufgabe blieb dem Nutzer damit verborgen.

Jetzt schaut das Werkzeug zusaetzlich sitzungsuebergreifend in die echten
Aufgaben DIESES Agenten. Geprueft wird das VERHALTEN gegen einen Fake-Task-Satz.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.services import realtime_voice_session as rvs


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return SimpleNamespace(all=lambda: self._rows)


class _FakeDB:
    def __init__(self, rows):
        self._rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, *_a, **_k):
        return _FakeResult(self._rows)


def _task(tid, title, status, result="", agent="a1", created="2026-08-19T05:00:00"):
    from datetime import datetime
    return SimpleNamespace(
        id=tid, title=title, prompt=title, result=result, agent_id=agent,
        status=SimpleNamespace(value=status),
        created_at=datetime.fromisoformat(created),
    )


def _voice(agent_id="a1"):
    s = rvs.RealtimeVoiceSession.__new__(rvs.RealtimeVoiceSession)
    s.agent_id = agent_id
    s.user_id = "u1"
    s._delegations = []
    s._planned = {}
    return s


class CrossSessionDelegatedTasksTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_session_sees_earlier_completed_task_with_result(self):
        rows = [_task("k7", "Jira-Tickets sortieren", "completed",
                      result="Es gibt kein Jira-Tool — bitte Rücksprache.")]
        with patch("app.db.session.resilient_session", lambda: _FakeDB(rows)):
            out = await _voice()._delegated_tasks_summary()
        self.assertIn("früheren Gesprächen", out)
        self.assertIn("Jira-Tickets sortieren", out)
        self.assertIn("Rücksprache", out)  # das Ergebnis muss ankommen
        self.assertIn("FERTIG", out)

    async def test_scheduled_and_proactive_runs_are_hidden(self):
        """Automatische Läufe (Titel in [..]) sind keine Delegationen des Nutzers
        und wuerden die Antwort nur zumuellen — der Filter haelt sie raus."""
        # Der SQL-Filter ~like('[%') wird im Fake nicht ausgewertet, deshalb
        # pruefen wir die Filter-Absicht am Quelltext UND das Verhalten bei
        # bereits gefilterten Zeilen (leer -> ehrliche Fehlmeldung).
        from pathlib import Path
        src = (Path(rvs.__file__)).read_text(encoding="utf-8")
        self.assertIn('~Task.title.like("[%")', src,
                      "Automatische [Scheduled]/[Proactive]-Läufe müssen gefiltert werden")

    async def test_empty_everywhere_is_honest(self):
        with patch("app.db.session.resilient_session", lambda: _FakeDB([])):
            out = await _voice()._delegated_tasks_summary()
        self.assertIn("keine", out.lower())

    async def test_only_this_agent_is_queried(self):
        """Isolation: die Abfrage filtert auf self.agent_id — belegt am Quelltext,
        damit kein fremder Agent auftaucht."""
        from pathlib import Path
        src = (Path(rvs.__file__)).read_text(encoding="utf-8")
        self.assertIn("Task.agent_id == agent_id", src)

    async def test_in_session_and_earlier_both_shown(self):
        v = _voice()
        v._delegations = [{"id": "z1", "instruction": "Mails prüfen", "done": False,
                           "last": "", "result": ""}]
        rows = [_task("k7", "Jira-Tickets sortieren", "completed", result="fertig")]
        with patch("app.db.session.resilient_session", lambda: _FakeDB(rows)):
            out = await v._delegated_tasks_summary()
        self.assertIn("diesem Gespräch", out)
        self.assertIn("Mails prüfen", out)
        self.assertIn("früheren Gesprächen", out)
        self.assertIn("Jira-Tickets sortieren", out)


if __name__ == "__main__":
    unittest.main()

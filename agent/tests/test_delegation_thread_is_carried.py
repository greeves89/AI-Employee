"""Ein Auftrag merkt sich, aus welchem Gespräch er stammt.

Ohne das kann der Orchestrator die Fertigmeldung später nirgendwo hin zustellen —
sie landete in ``webapp:default``, einem Faden, den niemand ansieht. Der Kunde am
2026-08-13: *„Er hat die Aufgabe delegiert — es fehlt aber die Rückmeldung, ob der
Agent das auch gemacht hat."*

Die Gegenprobe ist genauso wichtig: **ausserhalb** eines Gesprächs darf kein Faden
angehängt werden. Ein proaktiver Nachtlauf gehört in keinen Chat, und einen zu
erfinden hiesse, eine Meldung in ein fremdes Gespräch zu schreiben.
"""

import asyncio
import unittest

from app.tools.api_client import OrchestratorAPIClient, current_chat_session


class _Recorder(OrchestratorAPIClient):
    """Ein Client, der den Rumpf festhält statt ihn zu senden."""

    def __init__(self):
        self.agent_id = "lead"
        self.agent_name = "Lead"
        self.sent: dict | None = None
        self.batch: dict | None = None

    async def _request(self, method, path, json=None, params=None):  # noqa: A002
        if json is not None:
            self.sent = json
            if path.endswith("/batch"):
                # ``delegate_and_wait`` fragt danach den Stand ab; ohne eigenes
                # Feld ueberschriebe die Nachfrage den Stapel.
                self.batch = json
        # Sofort fertig, damit die Warteschleife nicht laeuft.
        return {"id": "t1", "tasks": [{"id": "t1"}],
                "status": "completed", "title": "A", "result": "ok"}


class InsideAConversationTests(unittest.TestCase):
    def setUp(self):
        self.client = _Recorder()
        self.token = current_chat_session.set("sess-abc")

    def tearDown(self):
        current_chat_session.reset(self.token)

    def test_a_single_task_carries_the_thread(self):
        asyncio.run(self.client.create_task({"title": "T", "prompt": "P",
                                             "agent_id": "worker"}))
        self.assertEqual(self.client.sent["chat_session_id"], "sess-abc")

    def test_it_also_records_who_delegated(self):
        """Ohne den Auftraggeber gibt es niemanden, dem man berichten koennte."""
        asyncio.run(self.client.create_task({"title": "T", "prompt": "P"}))
        self.assertEqual(self.client.sent["created_by_agent"], "lead")

    def test_a_delegated_batch_carries_it_per_task(self):
        asyncio.run(self.client.delegate_and_wait({
            "tasks": [{"title": "A", "prompt": "x", "agent_id": "w1"},
                      {"title": "B", "prompt": "y", "agent_id": "w2"}],
            "timeout_seconds": 10,
        }))
        for entry in self.client.sent["tasks"]:
            with self.subTest(entry["title"]):
                self.assertEqual(entry["chat_session_id"], "sess-abc")


class OutsideAConversationTests(unittest.TestCase):
    """Proaktive Laeufe und Zeitplaene haben keinen Faden."""

    def test_nothing_is_attached(self):
        client = _Recorder()
        token = current_chat_session.set(None)
        try:
            asyncio.run(client.create_task({"title": "T", "prompt": "P"}))
        finally:
            current_chat_session.reset(token)
        self.assertNotIn("chat_session_id", client.sent)

    def test_an_empty_thread_counts_as_none(self):
        """Ein leerer Text ist kein Faden — er wuerde beim Zustellen ins Leere
        zeigen und sieht dabei aus wie eine gueltige Angabe."""
        client = _Recorder()
        token = current_chat_session.set("")
        try:
            asyncio.run(client.create_task({"title": "T", "prompt": "P"}))
        finally:
            current_chat_session.reset(token)
        self.assertNotIn("chat_session_id", client.sent)


if __name__ == "__main__":
    unittest.main()

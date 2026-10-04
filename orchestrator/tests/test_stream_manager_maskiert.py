"""Der Live-Strom der Aufgaben zeigt Werkzeug-Eingaben ohne Geheimnisse (#911).

Der Agent veroeffentlicht jeden Werkzeugaufruf samt Eingabe; der Orchestrator
reicht ihn an den Browser durch (Verlauf beim Verbinden UND laufende Ereignisse).
Ein ``curl -H "Authorization: Bearer …"`` stand dort bisher im Klartext.
"""

import json
import unittest

from app.core.stream_manager import StreamManager

GEHEIM = "ghp_" + "Q" * 30


def _ereignis(**daten) -> str:
    return json.dumps({"agent_id": "a1", "task_id": "t1", "type": "tool_call",
                       "data": {"tool": "Bash", "tool_use_id": "u1", **daten}})


class _Pubsub:
    def __init__(self, nachrichten):
        self._nachrichten = list(nachrichten)

    async def get_message(self, **_):
        if self._nachrichten:
            return {"type": "message", "data": self._nachrichten.pop(0)}
        raise RuntimeError("Ende des Tests")  # beendet die Schleife

    async def unsubscribe(self, *_):
        pass

    async def aclose(self):
        pass


class _Client:
    def __init__(self, verlauf):
        self._verlauf = verlauf

    async def lrange(self, *_):
        return list(self._verlauf)


class _Redis:
    def __init__(self, verlauf, live):
        self.client = _Client(verlauf)
        self._live = live

    async def subscribe(self, _kanal):
        return _Pubsub(self._live)


class _Socket:
    def __init__(self):
        self.gesendet: list[str] = []

    async def accept(self):
        pass

    async def send_text(self, text):
        self.gesendet.append(text)


class StreamManagerMaskiertTests(unittest.IsolatedAsyncioTestCase):
    async def test_verlauf_und_live_ohne_geheimnis(self):
        verlauf = [_ereignis(input={"command": f"echo {GEHEIM}"}).encode()]
        live = [_ereignis(input={"command": f"curl -H 'Authorization: Bearer {GEHEIM}'"})]
        sock = _Socket()
        await StreamManager(_Redis(verlauf, live)).stream_agent_logs(sock, "a1")
        werkzeug = [json.loads(t) for t in sock.gesendet if '"tool_call"' in t]
        self.assertEqual(len(werkzeug), 2)
        for ereignis in werkzeug:
            self.assertNotIn(GEHEIM, json.dumps(ereignis))
            self.assertIn("command", ereignis["data"]["input"])

    async def test_gesamtstrom_ohne_geheimnis(self):
        sock = _Socket()
        live = [_ereignis(input={"command": f"echo {GEHEIM}"})]
        await StreamManager(_Redis([], live)).stream_all_logs(sock)
        self.assertTrue(sock.gesendet)
        self.assertNotIn(GEHEIM, "".join(sock.gesendet))


if __name__ == "__main__":
    unittest.main()

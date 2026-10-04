"""Claude-Code-Antworten erscheinen live im Chat (#900).

Im Markttest stand die Antwort nach rund 30 Sekunden auf einmal da („Arbeitet
weiter … 11 s"). Ursache: ``claude -p --output-format stream-json`` liefert ohne
``--include-partial-messages`` nur fertige Bloecke.

Mit dem Schalter kommen die Teilstuecke als ``stream_event`` (``text_delta``)
sofort, das spaetere vollstaendige ``assistant``-Ereignis traegt denselben Text
noch einmal. Der Strom-Leser sorgt dafuer, dass jedes Zeichen GENAU EINMAL
veroeffentlicht wird, dass Helfer (Subagenten, ``parent_tool_use_id``) nicht in
die Antwort schreiben, und dass eine neue Nachricht (``message_start``) ein
neuer Absatz ist.

Die Zeilen unten haben die Form, die Claude Code wirklich ausgibt.
"""

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch


def _start(mid, parent=None):
    return {"type": "stream_event", "parent_tool_use_id": parent, "session_id": "s1",
            "event": {"type": "message_start", "message": {"id": mid, "content": []}}}


def _block_start(index=0, typ="text", parent=None):
    return {"type": "stream_event", "parent_tool_use_id": parent, "session_id": "s1",
            "event": {"type": "content_block_start", "index": index,
                      "content_block": {"type": typ, "text": ""}}}


def _delta(text, index=0, parent=None):
    return {"type": "stream_event", "parent_tool_use_id": parent, "session_id": "s1",
            "event": {"type": "content_block_delta", "index": index,
                      "delta": {"type": "text_delta", "text": text}}}


def _assistant(mid, *bloecke, parent=None):
    return {"type": "assistant", "parent_tool_use_id": parent, "session_id": "s1",
            "message": {"id": mid, "content": list(bloecke)}}


def _text(t):
    return {"type": "text", "text": t}


def _werkzeug(tid, name="Bash", eingabe=None):
    return {"type": "tool_use", "id": tid, "name": name, "input": eingabe or {"command": "ls"}}


def _ergebnis(tid, inhalt, parent=None):
    return {"type": "user", "parent_tool_use_id": parent, "session_id": "s1",
            "message": {"content": [{"type": "tool_result", "tool_use_id": tid, "content": inhalt}]}}


class StromLeserTests(unittest.TestCase):

    def _lies(self, *ereignisse):
        from app.chat_handler import StromLeser
        leser = StromLeser()
        raus = []
        for e in ereignisse:
            raus.extend(leser.ereignis(e))
        return leser, raus

    @staticmethod
    def _texte(raus):
        return [d for typ, d in raus if typ == "text"]

    def test_teilstuecke_kommen_sofort_und_der_text_steht_genau_einmal_da(self):
        leser, raus = self._lies(
            _start("m1"), _block_start(), _delta("Hallo "), _delta("Welt"),
            _assistant("m1", _text("Hallo Welt")),
        )
        texte = self._texte(raus)
        self.assertEqual([t["text"] for t in texte], ["Hallo ", "Welt"],
                         "Das vollständige Ereignis darf den Text nicht noch einmal schicken.")
        self.assertEqual(leser.text, "Hallo Welt")

    def test_das_vollstaendige_ereignis_liefert_nur_den_nicht_gestreamten_rest(self):
        leser, raus = self._lies(
            _start("m1"), _delta("Hallo "),
            _assistant("m1", _text("Hallo Welt")),
        )
        self.assertEqual([t["text"] for t in self._texte(raus)], ["Hallo ", "Welt"])
        self.assertEqual(leser.text, "Hallo Welt")

    def test_reihenfolge_vertauscht_verdoppelt_nichts(self):
        """Kommt das vollständige Ereignis vor den letzten Teilstücken, bleiben
        diese stumm."""
        leser, raus = self._lies(
            _start("m1"), _delta("Hallo "),
            _assistant("m1", _text("Hallo Welt")),
            _delta("Welt"),
        )
        self.assertEqual("".join(t["text"] for t in self._texte(raus)), "Hallo Welt")
        self.assertEqual(leser.text, "Hallo Welt")

    def test_subagenten_bleiben_unsichtbar(self):
        leser, raus = self._lies(
            _start("m1"), _delta("Ich frage einen Helfer."),
            _assistant("m1", _text("Ich frage einen Helfer.")),
            _assistant("m1", _werkzeug("agent1", "Agent", {"description": "Suche", "prompt": "x"})),
            # Alles vom Helfer: eigene Nachricht, eigener Text, eigenes Werkzeug.
            _start("h1", parent="agent1"), _delta("Helfer denkt laut", parent="agent1"),
            _assistant("h1", _text("Helfer denkt laut"), parent="agent1"),
            _assistant("h1", _werkzeug("h-t1"), parent="agent1"),
            _ergebnis("h-t1", "intern", parent="agent1"),
            _ergebnis("agent1", "Ergebnis des Helfers"),
            _start("m2"), _delta("Fertig."),
            _assistant("m2", _text("Fertig.")),
        )
        alles = json.dumps(raus, ensure_ascii=False)
        self.assertNotIn("Helfer denkt laut", alles)
        self.assertNotIn("h-t1", alles)
        self.assertEqual(leser.text, "Ich frage einen Helfer.\n\nFertig.")
        werkzeuge = [d["tool"] for typ, d in raus if typ == "tool_call"]
        self.assertEqual(werkzeuge, ["Agent"], "Der Aufruf des Helfers selbst bleibt sichtbar.")

    def test_neue_nachricht_ist_neuer_block(self):
        leser, raus = self._lies(
            _start("m1"), _delta("Ich schau kurz nach."),
            _assistant("m1", _text("Ich schau kurz nach.")),
            _assistant("m1", _werkzeug("t1")),
            _ergebnis("t1", "ok"),
            _start("m2"), _delta("Drei "), _delta("Ideen"),
            _assistant("m2", _text("Drei Ideen")),
        )
        texte = self._texte(raus)
        self.assertEqual([(t["text"], t["neuer_block"]) for t in texte],
                         [("Ich schau kurz nach.", False), ("Drei ", True), ("Ideen", False)])
        self.assertEqual(leser.text, "Ich schau kurz nach.\n\nDrei Ideen")

    def test_ohne_teilstuecke_wie_bisher(self):
        """Ältere CLI ohne den Schalter: nur vollständige Ereignisse."""
        leser, raus = self._lies(
            _assistant("m1", _text("Ich schau kurz nach.")),
            _assistant("m1", _werkzeug("t1")),
            _assistant("m2", _text("Drei kurze Ideen")),
        )
        self.assertEqual([(t["text"], t["neuer_block"]) for t in self._texte(raus)],
                         [("Ich schau kurz nach.", False), ("Drei kurze Ideen", True)])

    def test_werkzeugergebnis_landet_am_aufruf(self):
        leser, raus = self._lies(
            _assistant("m1", _werkzeug("t1", eingabe={"command": "echo " + "x" * 5000})),
            _ergebnis("t1", [{"type": "text", "text": "x" * 20}]),
        )
        eintrag = leser.werkzeuge.liste()[0]
        json.loads(eintrag["input"])
        self.assertEqual(eintrag["output"], "x" * 20)
        self.assertEqual([typ for typ, _ in raus], ["tool_call", "tool_result"])

    def test_datei_markierung_wird_zur_datei(self):
        nutzlast = {"path": "/workspace/a.pdf", "filename": "a.pdf"}
        marker = "__AI_EMPLOYEE_PRESENT_FILE__" + json.dumps(nutzlast)
        _, raus = self._lies(
            _assistant("m1", _werkzeug("t1", "present_file", {"path": "/workspace/a.pdf"})),
            _ergebnis("t1", [{"type": "text", "text": marker}]),
        )
        self.assertIn(("file", nutzlast), raus)
        ergebnis = [d for typ, d in raus if typ == "tool_result"][0]
        self.assertEqual(ergebnis["content"], "File presented to the user.")


class _Strom:
    def __init__(self, zeilen):
        self._daten = [(json.dumps(z) + "\n").encode() for z in zeilen]

    async def read(self, _n=-1):
        return self._daten.pop(0) if self._daten else b""

    async def readline(self):
        return b""


class _Stdin:
    def write(self, _d):
        pass

    async def drain(self):
        pass

    def close(self):
        pass


class _Prozess:
    def __init__(self, zeilen):
        self.stdout = _Strom(zeilen)
        self.stderr = _Strom([])
        self.stdin = _Stdin()
        self.returncode = 0

    async def wait(self):
        return 0


class ChatHandlerTests(unittest.IsolatedAsyncioTestCase):

    async def test_aufruf_verlangt_teilstuecke_und_text_kommt_genau_einmal(self):
        from app.chat_handler import ChatHandler

        pub = AsyncMock()
        pub.last_activity_at = 0.0
        handler = ChatHandler(pub)
        zeilen = [
            {"type": "system", "subtype": "init", "session_id": "s1"},
            _start("m1"), _block_start(), _delta("Hallo "), _delta("Welt"),
            _assistant("m1", _text("Hallo Welt")),
            {"type": "result", "subtype": "success", "is_error": False, "result": "Hallo Welt",
             "session_id": "s1", "usage": {}},
        ]
        aufruf = AsyncMock(return_value=_Prozess(zeilen))
        with patch("app.chat_handler.asyncio.create_subprocess_exec", aufruf), \
             patch("app.chat_handler.get_oauth_token", return_value=""):
            ergebnis = await handler._execute_cli("m1", "hi", "claude-sonnet-5")

        argumente = aufruf.call_args.args
        self.assertIn("--include-partial-messages", argumente)
        texte = [c.args[2]["text"] for c in pub.publish_chat.call_args_list if c.args[1] == "text"]
        self.assertEqual("".join(texte), "Hallo Welt")
        self.assertEqual(ergebnis["text"], "Hallo Welt")


class _Redis:
    def __init__(self):
        self.gesendet: list[dict] = []

    async def publish(self, kanal, nachricht):
        if kanal.endswith(":chat:response"):
            self.gesendet.append(json.loads(nachricht))

    async def rpush(self, *a, **k):
        pass

    async def ltrim(self, *a, **k):
        pass


class PufferTests(unittest.IsolatedAsyncioTestCase):
    """Gemeinsamer kleiner Puffer: viele Kleinststücke werden zu wenigen
    Ereignissen, ohne die Reihenfolge oder den Text zu verändern."""

    def _pub(self):
        from app.log_publisher import LogPublisher
        redis = _Redis()
        return LogPublisher(redis, "a1"), redis

    async def test_kleinststuecke_werden_gebuendelt(self):
        pub, redis = self._pub()
        for wort in ["Ein ", "kurzer ", "Satz."]:
            await pub.publish_chat("m1", "text", {"text": wort, "neuer_block": False})
        await pub.publish_chat("m1", "done", {"status": "completed"})
        typen = [n["type"] for n in redis.gesendet]
        self.assertEqual(typen, ["text", "done"])
        self.assertEqual(redis.gesendet[0]["data"], {"text": "Ein kurzer Satz.", "neuer_block": False})

    async def test_anderes_ereignis_schiebt_den_text_vorher_raus(self):
        pub, redis = self._pub()
        await pub.publish_chat("m1", "text", {"text": "Ich schau nach."})
        await pub.publish_chat("m1", "tool_call", {"tool_use_id": "t1", "tool": "Bash", "input": {}})
        self.assertEqual([n["type"] for n in redis.gesendet], ["text", "tool_call"])
        self.assertEqual(redis.gesendet[0]["data"], {"text": "Ich schau nach."})

    async def test_neuer_block_wird_nicht_an_den_vorigen_geklebt(self):
        pub, redis = self._pub()
        await pub.publish_chat("m1", "text", {"text": "Erstens.", "neuer_block": False})
        await pub.publish_chat("m1", "text", {"text": "Zweitens.", "neuer_block": True})
        await pub.publish_chat("m1", "done", {})
        self.assertEqual([n["data"] for n in redis.gesendet[:2]],
                         [{"text": "Erstens.", "neuer_block": False},
                          {"text": "Zweitens.", "neuer_block": True}])

    async def test_nach_kurzer_zeit_kommt_der_text_auch_ohne_weiteres_ereignis(self):
        pub, redis = self._pub()
        await pub.publish_chat("m1", "text", {"text": "Hallo"})
        self.assertEqual(redis.gesendet, [])
        await asyncio.sleep(0.2)
        self.assertEqual([n["data"]["text"] for n in redis.gesendet], ["Hallo"])

    async def test_grosse_menge_geht_sofort_raus(self):
        pub, redis = self._pub()
        await pub.publish_chat("m1", "text", {"text": "x" * 250})
        self.assertEqual(len(redis.gesendet), 1)

    async def test_lebenszeichen_bleibt_sofort(self):
        """Der Stillstands-Wachhund liest ``last_activity_at`` — gepuffert oder nicht."""
        pub, _ = self._pub()
        pub.last_activity_at = 0.0
        await pub.publish_chat("m1", "text", {"text": "a"})
        self.assertGreater(pub.last_activity_at, 0.0)

    async def test_zwei_gespraeche_mischen_sich_nicht(self):
        pub, redis = self._pub()
        await pub.publish_chat("m1", "text", {"text": "A"})
        await pub.publish_chat("m2", "text", {"text": "B"})
        await pub.publish_chat("m1", "done", {})
        await pub.publish_chat("m2", "done", {})
        nach = {(n["message_id"], n["type"]): n["data"] for n in redis.gesendet}
        self.assertEqual(nach[("m1", "text")], {"text": "A"})
        self.assertEqual(nach[("m2", "text")], {"text": "B"})


if __name__ == "__main__":
    unittest.main()

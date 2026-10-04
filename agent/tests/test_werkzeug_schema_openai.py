"""Ein einziges kaputtes Werkzeug darf einen Custom-LLM-Agenten nicht lahmlegen.

Live: Ein MCP-Server lieferte ein Werkzeug, dessen Parameter-Schema auf OBERSTER
Ebene ``oneOf`` trug (``mcp_Higgsfield-MCP_use_higgsfield``). OpenAI/Azure lehnen
das ab („Invalid schema for function … must have type 'object' and not have
'oneOf'/'anyOf'/'allOf'/'enum'/'not' at the top level") — und zwar die GANZE
Anfrage. Jede Nachricht an den Agenten scheiterte mit 400, im Chat stand das
rohe Fehler-JSON.

Jetzt:
* Schemas werden vor dem Senden in die geforderte Form gebracht — Varianten
  oben werden zu EINEM Objekt zusammengeführt (Eigenschaften vereinigt, Pflicht
  ist nur, was jede Variante verlangt). Die Argumente bleiben flach, der
  MCP-Server bekommt dieselbe Form wie bisher.
* Was sich nicht reparieren lässt, wird einzeln weggelassen (Warnung im Log).
* Lehnt der Anbieter trotzdem ein Werkzeug ab, wird genau dieses gestrichen und
  der Zug wiederholt — der Lauf bricht nicht ab.
* Ein API-Fehler erscheint im Chat als verständlicher deutscher Satz; das rohe
  JSON steht im Log.
"""

import json
import logging
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app.werkzeug_schema import abgelehntes_werkzeug, parameter_fuer_openai

OBEN_VERBOTEN = ("oneOf", "anyOf", "allOf", "enum", "not")

# Form wie beim betroffenen Werkzeug: Varianten je Aktion, kein ``type`` oben.
VARIANTEN = {
    "oneOf": [
        {"type": "object",
         "properties": {"action": {"const": "generate_image"},
                        "prompt": {"type": "string"}},
         "required": ["action", "prompt"]},
        {"type": "object",
         "properties": {"action": {"const": "generate_video"},
                        "prompt": {"type": "string"},
                        "image_url": {"type": "string"}},
         "required": ["action", "image_url"]},
    ],
}


def assert_openai_tauglich(test: unittest.TestCase, schema: dict) -> None:
    test.assertIsInstance(schema, dict)
    test.assertEqual(schema.get("type"), "object")
    test.assertIsInstance(schema.get("properties"), dict)
    for schluessel in OBEN_VERBOTEN:
        test.assertNotIn(schluessel, schema, f"{schluessel} oben ist verboten")
    for name in schema.get("required", []):
        test.assertIsInstance(name, str)


class SchemaReparatur(unittest.TestCase):
    def test_oneof_oben_wird_ein_objekt(self):
        s = parameter_fuer_openai(VARIANTEN)
        assert_openai_tauglich(self, s)
        self.assertEqual(set(s["properties"]), {"action", "prompt", "image_url"})
        # Pflicht nur, was JEDE Variante verlangt.
        self.assertEqual(s["required"], ["action"])
        # Beide Werte für ``action`` bleiben wählbar.
        varianten = s["properties"]["action"]["anyOf"]
        self.assertEqual({v["const"] for v in varianten}, {"generate_image", "generate_video"})

    def test_anyof_oben_ebenso(self):
        s = parameter_fuer_openai({"type": "object", "anyOf": VARIANTEN["oneOf"]})
        assert_openai_tauglich(self, s)
        self.assertEqual(s["required"], ["action"])

    def test_allof_oben_wird_vereinigt(self):
        s = parameter_fuer_openai({"allOf": [
            {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]},
            {"type": "object", "properties": {"b": {"type": "integer"}}, "required": ["b"]},
        ]})
        assert_openai_tauglich(self, s)
        self.assertEqual(set(s["properties"]), {"a", "b"})
        self.assertEqual(sorted(s["required"]), ["a", "b"])

    def test_verweis_auf_defs_wird_aufgeloest_und_defs_bleiben(self):
        s = parameter_fuer_openai({
            "$defs": {"Bild": {"type": "object", "properties": {"prompt": {"type": "string"}},
                               "required": ["prompt"]},
                      "Farbe": {"type": "string"}},
            "oneOf": [{"$ref": "#/$defs/Bild"},
                      {"type": "object", "properties": {"farbe": {"$ref": "#/$defs/Farbe"}}}],
        })
        assert_openai_tauglich(self, s)
        self.assertIn("prompt", s["properties"])
        self.assertIn("$defs", s, "verschachtelte Verweise brauchen ihre Definitionen")

    def test_enum_oben_faellt_weg(self):
        s = parameter_fuer_openai({"type": "object", "properties": {}, "enum": [{}]})
        assert_openai_tauglich(self, s)

    def test_gewoehnliches_schema_bleibt_wie_es_ist(self):
        roh = {"type": "object", "properties": {"q": {"type": "string",
                                                      "oneOf": [{"minLength": 1}]}},
               "required": ["q"]}
        self.assertEqual(parameter_fuer_openai(roh), roh)

    def test_fehlendes_schema_ist_ein_leeres_objekt(self):
        assert_openai_tauglich(self, parameter_fuer_openai(None))

    def test_nicht_reparierbar(self):
        """Varianten, die gar keine Objekte sind, lassen sich nicht zu
        Funktionsargumenten machen."""
        self.assertIsNone(parameter_fuer_openai({"oneOf": [{"type": "string"},
                                                           {"type": "number"}]}))
        self.assertIsNone(parameter_fuer_openai({"type": "array", "items": {}}))


def _mcp_antwort(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content or b"{}")
    methode = body.get("method")
    if methode == "initialize":
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {}})
    if methode == "tools/list":
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 2, "result": {"tools": [
            {"name": "use_higgsfield", "description": "Bilder", "inputSchema": VARIANTEN},
            {"name": "kaputt", "description": "x",
             "inputSchema": {"oneOf": [{"type": "string"}, {"type": "number"}]}},
            {"name": "suche", "description": "y",
             "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}}},
        ]}})
    return httpx.Response(202)


class McpWerkzeugeKommenTauglichAn(unittest.IsolatedAsyncioTestCase):
    async def test_nur_das_kaputte_werkzeug_faellt_weg(self):
        from app.tools.mcp_client import MCPHTTPClient

        client = MCPHTTPClient()
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(_mcp_antwort))
        with self.assertLogs("app.tools.mcp_client", level=logging.WARNING) as log:
            werkzeuge = await client._list_server_tools("Higgsfield-MCP", "http://mcp.example.invalid")
        namen = [w["function"]["name"] for w in werkzeuge]
        self.assertEqual(namen, ["mcp_Higgsfield-MCP_use_higgsfield", "mcp_Higgsfield-MCP_suche"])
        for w in werkzeuge:
            assert_openai_tauglich(self, w["function"]["parameters"])
        self.assertTrue(any("kaputt" in z for z in log.output))
        self.assertNotIn("mcp_Higgsfield-MCP_kaputt", client._tool_registry)
        await client._client.aclose()


class _Ereignis:
    def __init__(self, type, **kw):
        self.type = type
        self.text = kw.get("text", "")
        self.tool_id = ""
        self.tool_name = ""
        self.tool_input = {}
        self.input_tokens = 1
        self.output_tokens = 1


ABGELEHNT = ('API error 400: {"error": {"message": "Invalid schema for function '
             "'mcp_Higgsfield-MCP_use_higgsfield': schema must have type 'object' and not "
             "have 'oneOf'/'anyOf'/'allOf'/'enum'/'not' at the top level.\", "
             '"type": "invalid_request_error", "param": "tools[3].function.parameters", '
             '"code": "invalid_function_parameters"}}')


class _Anbieter:
    """Erst lehnt der Anbieter ein Werkzeug ab, dann antwortet er."""

    def __init__(self, fehler_zuerst=ABGELEHNT, immer_fehler=False):
        self.aufrufe: list[list[str]] = []
        self.fehler = fehler_zuerst
        self.immer_fehler = immer_fehler
        self.reasoning_effort = ""

    def stream_completion(self, messages, tools=None):
        self.aufrufe.append([t["function"]["name"] for t in (tools or [])])
        erster = len(self.aufrufe) == 1

        async def gen():
            if erster or self.immer_fehler:
                yield _Ereignis("error", text=self.fehler)
                return
            yield _Ereignis("text_delta", text="Fertig.")
            yield _Ereignis("done")

        return gen()

    async def close(self):
        pass


class _Publisher:
    def __init__(self):
        self.events: list[tuple[str, object]] = []
        self.last_activity_at = 0.0

    async def publish_chat(self, message_id, kind, payload):
        self.events.append((kind, payload))

    async def publish(self, task_id, kind, payload):
        self.events.append((kind, payload))

    def notiere_fortschritt(self, *_a):
        pass


def _katalog():
    return [
        {"type": "function", "function": {"name": "mcp_Higgsfield-MCP_use_higgsfield",
                                          "description": "", "parameters": {}}},
        {"type": "function", "function": {"name": "mcp_X_suche", "description": "",
                                          "parameters": {}}},
    ]


class AbgelehntesWerkzeugWirdGestrichen(unittest.IsolatedAsyncioTestCase):
    def test_name_aus_der_meldung(self):
        self.assertEqual(abgelehntes_werkzeug(ABGELEHNT), "mcp_Higgsfield-MCP_use_higgsfield")
        self.assertIsNone(abgelehntes_werkzeug("API error 429: rate limit"))
        self.assertIsNone(abgelehntes_werkzeug(None))

    async def _chat(self, anbieter):
        from app.config import settings
        from app.llm_chat_handler import LLMChatHandler
        from app.providers.base import ChatMessage

        pub = _Publisher()
        h = LLMChatHandler(log_publisher=pub)
        h._context_window = 1_000_000
        h._history = [ChatMessage(role="system", content="S")]
        h._all_tools = _katalog()
        h._activated = ["mcp_Higgsfield-MCP_use_higgsfield", "mcp_X_suche"]
        with patch.object(settings, "llm_tools_enabled", True), \
             patch.object(h, "_get_provider", return_value=anbieter), \
             patch("app.llm_chat_handler._core_tool_names", return_value=set()), \
             patch("app.llm_chat_handler.report_result_status", new=AsyncMock()):
            erg = await h.handle_message("m1", "mach ein Bild")
        return h, pub, erg

    async def test_chat_laeuft_weiter_ohne_das_werkzeug(self):
        anbieter = _Anbieter()
        h, pub, erg = await self._chat(anbieter)
        self.assertEqual(erg["status"], "completed")
        self.assertEqual(erg["text"], "Fertig.")
        self.assertIn("mcp_Higgsfield-MCP_use_higgsfield", anbieter.aufrufe[0])
        self.assertNotIn("mcp_Higgsfield-MCP_use_higgsfield", anbieter.aufrufe[1])
        self.assertIn("mcp_X_suche", anbieter.aufrufe[1], "die anderen Werkzeuge bleiben")
        self.assertEqual([e for e in pub.events if e[0] == "error"], [])

    async def test_auftrag_laeuft_weiter_ohne_das_werkzeug(self):
        from app.config import settings
        from app.llm_runner import LLMRunner

        anbieter = _Anbieter()
        pub = _Publisher()
        r = LLMRunner(log_publisher=pub)
        r._all_tools = _katalog()
        r._activated = ["mcp_Higgsfield-MCP_use_higgsfield", "mcp_X_suche"]
        leer = [patch(f"app.llm_runner.{n}", return_value="") for n in (
            "get_identity_context", "get_memory_preload", "get_skills_context",
            "get_mounts_context", "get_marketplace_skill_suggestions")]
        for p in leer:
            p.start()
        self.addCleanup(patch.stopall)
        with patch.object(settings, "llm_tools_enabled", True), \
             patch.object(r, "_get_provider", return_value=anbieter), \
             patch("app.llm_runner._core_tool_names", return_value=set()), \
             patch("app.llm_runner.report_result_status", new=AsyncMock()):
            erg = await r.execute_task("t1", "mach ein Bild", lightweight=True)
        self.assertEqual(erg["status"], "completed")
        self.assertNotIn("mcp_Higgsfield-MCP_use_higgsfield", anbieter.aufrufe[-1])


class VerstaendlicheFehlermeldung(unittest.IsolatedAsyncioTestCase):
    async def test_im_chat_steht_kein_roher_api_text(self):
        from app.config import settings
        from app.llm_chat_handler import LLMChatHandler
        from app.providers.base import ChatMessage

        roh = ('API error 400: {"error": {"code": "OperationNotSupported", "message": '
               '"The chatCompletion operation does not work with the specified model, '
               'gpt-5.3-codex. Please choose different model and try again."}}')
        pub = _Publisher()
        h = LLMChatHandler(log_publisher=pub)
        h._context_window = 1_000_000
        h._history = [ChatMessage(role="system", content="S")]
        with patch.object(settings, "llm_tools_enabled", False), \
             patch.object(h, "_get_provider", return_value=_Anbieter(roh, immer_fehler=True)), \
             patch("app.llm_chat_handler.report_result_status", new=AsyncMock()), \
             self.assertLogs("app.llm_chat_handler", level=logging.WARNING) as log:
            erg = await h.handle_message("m1", "hallo")
        self.assertEqual(erg["status"], "error")
        meldungen = [str(d.get("message")) for art, d in pub.events if art == "error"]
        self.assertEqual(len(meldungen), 1)
        text = meldungen[0]
        self.assertNotIn("{", text)
        self.assertNotIn("OperationNotSupported", text)
        self.assertIn("400", text)
        self.assertRegex(text, "[äöüÄÖÜß]", "deutscher Satz mit echten Umlauten")
        self.assertTrue(any("OperationNotSupported" in z for z in log.output),
                        "die Einzelheiten stehen im Log")

    def test_andere_fehler_bleiben_unveraendert(self):
        from app.providers.base import nutzertext_fuer_api_fehler

        self.assertEqual(nutzertext_fuer_api_fehler("ConnectError: weg"), "ConnectError: weg")

    def test_statuscodes(self):
        from app.providers.base import nutzertext_fuer_api_fehler

        for code in (400, 401, 403, 404, 429, 500, 503):
            with self.subTest(code=code):
                text = nutzertext_fuer_api_fehler(f'API error {code}: {{"error": "x"}}')
                self.assertIn(str(code), text)
                self.assertNotIn("{", text)


if __name__ == "__main__":
    unittest.main()

"""Eine Netzwerkunterbrechung muss als solche ankommen, nicht als AttributeError.

29.09.2026, Kundenanlage: Aufgaben scheiterten mit
``AttributeError: 'OpenAIProvider' object has no attribute 'model' <- ReadError``.
Die Diagnose fuer Netzwerkfehler (``describe_failure``) las ``self.model`` —
das Attribut heisst ``model_name``. Jede Unterbrechung loeste so einen zweiten
Fehler aus, der den echten ueberdeckte; die Erkennung „voruebergehend, erneut
versuchen" griff nie. Betroffen: alle drei Anbieter.

Der Test faehrt den echten Streaming-Pfad jedes Anbieters mit einer Verbindung,
die mitten im Aufbau abreisst.
"""
import unittest

import httpx

from app.providers.anthropic_provider import AnthropicProvider
from app.providers.base import ChatMessage
from app.providers.google_provider import GoogleProvider
from app.providers.openai_provider import OpenAIProvider


class _AbreissenderStrom:
    async def __aenter__(self):
        raise httpx.ReadError("")

    async def __aexit__(self, *a):
        return False


class _Http:
    is_closed = False

    def stream(self, *a, **k):
        return _AbreissenderStrom()

    async def post(self, *a, **k):
        raise httpx.ReadError("")

    async def aclose(self):
        pass


class NetzfehlerTests(unittest.IsolatedAsyncioTestCase):
    async def lauf(self, klasse, modell):
        anbieter = klasse(api_endpoint="https://example.invalid", api_key="k", model_name=modell)
        anbieter._http = _Http()
        ereignisse = [e async for e in anbieter.stream_completion([ChatMessage(role="user", content="hallo")])]
        fehler = [e.text for e in ereignisse if e.type == "error"]
        self.assertTrue(fehler, f"{klasse.__name__}: kein Fehler-Ereignis")
        self.assertNotIn("AttributeError", " ".join(fehler))
        self.assertNotIn("has no attribute", " ".join(fehler))

    async def test_openai_chat(self):
        await self.lauf(OpenAIProvider, "gpt-4.1")

    async def test_openai_responses(self):
        await self.lauf(OpenAIProvider, "gpt-5.4")

    async def test_anthropic(self):
        await self.lauf(AnthropicProvider, "claude-sonnet-5")

    async def test_google(self):
        await self.lauf(GoogleProvider, "gemini-2.5-pro")


if __name__ == "__main__":
    unittest.main()

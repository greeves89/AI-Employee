"""Issue #979: Der Server meldet den Auszug-Schnitt — der Client darf ihn nicht wegwerfen.

``projekt_wissen_schreiben`` und ``projekt_wissen_ergaenzen`` bauen ihren
Antworttext aus einzelnen Feldern der Serverantwort selbst zusammen. Ein neues
Feld geht dort still verloren: der Agent hält seine zu lange ``index.md`` für
vollständig ausgeliefert, obwohl nur ihr Anfang in Projektaufträge eingeht.
"""

import asyncio
import unittest

from app.tools.api_client import OrchestratorAPIClient

HINWEIS = "Hinweis: nur die ersten 1500 Zeichen von index.md gehen in einen Projektauftrag ein."


class _Client(OrchestratorAPIClient):
    def __init__(self, antwort):
        self.agent_id = "agent-1"
        self.agent_name = "Agent"
        self.antwort = antwort

    async def _request(self, method, path, json=None, params=None):  # noqa: A002
        return self.antwort


class HinweisErreichtDenAgenten(unittest.TestCase):
    def _text(self, antwort, werkzeug, params):
        client = _Client(antwort)
        return asyncio.run(getattr(client, werkzeug)(params))

    def test_schreiben_haengt_den_hinweis_an(self):
        text = self._text({"path": "index.md", "created": False, "bytes": 2600, "hinweis": HINWEIS},
                          "projekt_wissen_schreiben",
                          {"projekt_id": "p1", "pfad": "index.md", "inhalt": "x"})
        self.assertIn(HINWEIS, text)
        self.assertIn("Aktualisiert: index.md", text)

    def test_ergaenzen_haengt_den_hinweis_an(self):
        text = self._text({"path": "index.md", "bytes": 2600, "hinweis": HINWEIS},
                          "projekt_wissen_ergaenzen",
                          {"projekt_id": "p1", "pfad": "index.md", "abschnitt": "x"})
        self.assertIn(HINWEIS, text)

    def test_ohne_hinweis_bleibt_die_meldung_unveraendert(self):
        """Trennschärfe: der Normalfall darf keinen Zusatz bekommen."""
        text = self._text({"path": "index.md", "created": True, "bytes": 12},
                          "projekt_wissen_schreiben",
                          {"projekt_id": "p1", "pfad": "index.md", "inhalt": "x"})
        self.assertEqual(text, "Angelegt: index.md (12 Bytes).")


if __name__ == "__main__":
    unittest.main()

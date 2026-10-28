"""`secondbrain_search` nimmt den Index — der Dateiweg ist nur noch der Notnagel.

Bis #727 war das Werkzeug ein Substring-Zaehler ueber die gemounteten Dateien,
obwohl die Hybrid-Suche (Vektor + Volltext, RRF) im Orchestrator laengst lief —
nur eben fuer den externen MCP-Endpunkt und die Sprach-Sitzung, nicht fuer den
Agenten selbst.

Beim Umstellen entsteht eine Fehlerklasse, die kein Build sichtbar macht: Wenn
JEDE unfreundliche Antwort des Orchestrators als „nicht erreichbar" gilt, sucht
der Agent bei einem Rechtefehler still lokal weiter — und liefert Treffer aus
einem Vault, zu dem der Server gerade „nein" gesagt hat. Umgekehrt genauso: gilt
ein Verbindungsabbruch als Serveraussage, ist der Agent ohne Orchestrator blind,
obwohl der Vault im Container liegt.

Diese Tests ziehen deshalb die Grenze zwischen beidem — und pruefen zusaetzlich
den Rollout-Fall, in dem das Agenten-Abbild schon #727 hat und der Orchestrator
noch nicht (Routen-404).
"""
import unittest
from unittest.mock import AsyncMock, patch

from app.tools.api_client import ORCHESTRATOR_UNREACHABLE
from app.tools import executor as ex


class _Antwort:
    """Minimale httpx-Antwort — nur was ``_secondbrain_request`` liest."""

    def __init__(self, status, daten=None, text=""):
        self.status_code = status
        self._daten = daten
        self.text = text

    def json(self):
        if self._daten is None:
            raise ValueError("kein JSON")
        return self._daten


def _client(antwort=None, fehler=None):
    """Ein ``OrchestratorAPIClient`` mit vorgegebener HTTP-Antwort."""
    from app.tools.api_client import OrchestratorAPIClient

    c = OrchestratorAPIClient.__new__(OrchestratorAPIClient)
    c.base_url = "http://orch:8000/api/v1"
    c._client = AsyncMock()
    if fehler is not None:
        c._client.request.side_effect = fehler
    else:
        c._client.request.return_value = antwort
    return c


class RueckfallGrenzeTests(unittest.IsolatedAsyncioTestCase):
    """``_secondbrain_request`` klassifiziert — davon haengt alles ab."""

    async def test_erfolg_ist_ok(self):
        art, daten = await _client(_Antwort(200, {"hits": []}))._secondbrain_request(
            "GET", "/brains/agent/search")
        self.assertEqual(art, "ok")
        self.assertEqual(daten, {"hits": []})

    async def test_verbindungsfehler_ist_unerreichbar(self):
        art, _ = await _client(fehler=OSError("connection refused"))._secondbrain_request(
            "GET", "/brains/agent/search")
        self.assertEqual(art, "unerreichbar")

    async def test_5xx_ist_unerreichbar(self):
        """Eine kaputte Hybrid-Suche darf den Agenten nicht blind machen."""
        art, _ = await _client(_Antwort(500, text="boom"))._secondbrain_request(
            "GET", "/brains/agent/search")
        self.assertEqual(art, "unerreichbar")

    async def test_scoping_404_ist_eine_serveraussage(self):
        """Fremdes Label: NICHT lokal weitersuchen — der Server hat nein gesagt."""
        art, text = await _client(
            _Antwort(404, {"detail": "Kein zugewiesener Second Brain 'finanzen'. Verfuegbar: it"})
        )._secondbrain_request("GET", "/brains/agent/search")
        self.assertEqual(art, "fehler")
        self.assertIn("finanzen", text)

    async def test_403_ist_eine_serveraussage(self):
        art, _ = await _client(_Antwort(403, {"detail": "nur lesend"}))._secondbrain_request(
            "POST", "/brains/agent/write")
        self.assertEqual(art, "fehler")

    async def test_routen_404_ist_unerreichbar(self):
        """Rollout-Fall: Agenten-Abbild hat #727, der Orchestrator noch nicht.

        FastAPI antwortet fuer eine unbekannte Route mit genau ``Not Found``.
        Ohne diese Unterscheidung scheitert jede Suche hart, statt lokal
        weiterzusuchen — und zwar auf JEDER Anlage waehrend des Rollouts.
        """
        art, grund = await _client(
            _Antwort(404, {"detail": "Not Found"})
        )._secondbrain_request("GET", "/brains/agent/search")
        self.assertEqual(art, "unerreichbar")
        self.assertIn("727", grund)


class WerkzeugNimmtDenIndexTests(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.ex = ex.ToolExecutor.__new__(ex.ToolExecutor)

    async def test_erfolg_nutzt_den_api_weg_und_nicht_den_walk(self):
        treffer = "[SECOND BRAIN — hybrid | 1 Treffer]\n## it/Drucker/x17137.md"
        api = AsyncMock()
        api.secondbrain_search_remote.return_value = treffer
        with patch.object(self.ex, "_get_api_client", lambda: api), \
             patch.object(self.ex, "_secondbrain_search_local",
                          AsyncMock(side_effect=AssertionError("Walk darf nicht laufen"))):
            out = await self.ex._tool_secondbrain_search({"query": "x17137"})
        self.assertEqual(out, treffer)

    async def test_unerreichbar_faellt_auf_den_walk_zurueck(self):
        api = AsyncMock()
        api.secondbrain_search_remote.return_value = ORCHESTRATOR_UNREACHABLE
        with patch.object(self.ex, "_get_api_client", lambda: api), \
             patch.object(self.ex, "_secondbrain_search_local",
                          AsyncMock(return_value="LOKAL")):
            out = await self.ex._tool_secondbrain_search({"query": "x17137"})
        self.assertEqual(out, "LOKAL")

    async def test_ausnahme_im_api_weg_faellt_auf_den_walk_zurueck(self):
        api = AsyncMock()
        api.secondbrain_search_remote.side_effect = RuntimeError("Token weg")
        with patch.object(self.ex, "_get_api_client", lambda: api), \
             patch.object(self.ex, "_secondbrain_search_local",
                          AsyncMock(return_value="LOKAL")):
            out = await self.ex._tool_secondbrain_search({"query": "x17137"})
        self.assertEqual(out, "LOKAL")

    async def test_serverfehler_wird_durchgereicht_statt_lokal_gesucht(self):
        """Sonst liefert der Agent Treffer aus einem Vault, den der Server
        ihm gerade verweigert hat."""
        api = AsyncMock()
        api.secondbrain_search_remote.return_value = "Error: 404: Kein zugewiesener Second Brain"
        with patch.object(self.ex, "_get_api_client", lambda: api), \
             patch.object(self.ex, "_secondbrain_search_local",
                          AsyncMock(side_effect=AssertionError("Walk darf nicht laufen"))):
            out = await self.ex._tool_secondbrain_search({"query": "x"})
        self.assertIn("Error", out)

    async def test_leere_anfrage_fragt_den_server_gar_nicht(self):
        api = AsyncMock()
        with patch.object(self.ex, "_get_api_client", lambda: api):
            out = await self.ex._tool_secondbrain_search({"query": "   "})
        self.assertIn("required", out)
        api.secondbrain_search_remote.assert_not_called()


class WerkzeugEinordnungTests(unittest.TestCase):
    """Die Mengen, die ueber Freigabe, Parallelitaet und Zwischenspeicher
    entscheiden — ein Werkzeug, das in keiner steht, ist faktisch gesperrt."""

    def test_such_und_schreibweg_sind_freigegeben(self):
        for t in ("secondbrain_search", "secondbrain_write",
                  "secondbrain_read", "secondbrain_list"):
            with self.subTest(t):
                self.assertIn(t, ex.ALWAYS_ALLOWED_TOOLS)

    def test_suche_ist_parallel_sicher_und_zwischenspeicherbar(self):
        self.assertIn("secondbrain_search", ex.CONCURRENT_SAFE_TOOLS)
        self.assertIn("secondbrain_search", ex._CACHEABLE_TOOLS)

    def test_schreiben_ist_nicht_zwischenspeicherbar(self):
        self.assertNotIn("secondbrain_write", ex._CACHEABLE_TOOLS)


class SchreibenInvalidiertDenZwischenspeicherTests(unittest.IsolatedAsyncioTestCase):
    """Ohne das liefert die Suche direkt nach dem Schreiben den alten Stand —
    und der Agent haelt seinen eigenen Artikel fuer nicht gespeichert."""

    async def test_suche_nach_dem_schreiben_sieht_den_neuen_stand(self):
        e = ex.ToolExecutor.__new__(ex.ToolExecutor)
        e._cache = ex._ToolCache()
        stand = {"wert": "ALT"}

        async def suche(self, params):
            return stand["wert"]

        async def schreiben(self, params):
            stand["wert"] = "NEU"
            return "gespeichert"

        with patch.object(ex.ToolExecutor, "_tool_secondbrain_search", suche), \
             patch.object(ex.ToolExecutor, "_tool_secondbrain_write", schreiben), \
             patch.object(ex.ToolExecutor, "_get_api_client", lambda self: None):
            erst = await e._execute_inner("secondbrain_search", {"query": "x"})
            nochmal = await e._execute_inner("secondbrain_search", {"query": "x"})
            await e._execute_inner("secondbrain_write", {"path": "it/a.md", "content": "x"})
            danach = await e._execute_inner("secondbrain_search", {"query": "x"})

        self.assertEqual((erst, nochmal), ("ALT", "ALT"))   # Zwischenspeicher greift
        self.assertEqual(danach, "NEU")                      # ... und wurde geleert


if __name__ == "__main__":
    unittest.main()

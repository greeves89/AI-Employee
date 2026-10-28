"""Der Suchmodus ist kein Schmuck — er ist das einzige Drift-Signal.

``_has_chunks`` haelt einen Vault schon bei EINER Chunk-Zeile fuer indiziert.
Ein teilweise veralteter Index faellt deshalb nie von sich aus auf den Dateiweg
zurueck: neu geschriebene Dateien waeren unsichtbar, und zwar lautlos. Abschnitt 9
der Spec begruendet damit, dass Hybrid-Suche und Re-Index-Durchlauf zusammen
ausgeliefert werden muessen.

``hybrid_search_with_mode`` macht diesen Zustand lesbar. Zwei Aussagen muessen
dafuer stimmen, und beide sind leicht falsch zu bauen:

* „hybrid" heisst, die Treffer kommen aus dem Index. Wer das Feld fest auf
  „hybrid" setzt oder beim Rueckfall vergisst, meldet einen driftenden Vault als
  gesund.
* Die Warnung kommt NUR, wenn der Dateiweg Dateien findet, die der Index nicht
  kannte. Beide leer heisst, die Anfrage trifft wirklich nichts — eine Warnung
  darauf wuerde das echte Signal zumuellen.
"""
import unittest
from unittest.mock import patch

from app.services import vault_search as vs

HOST = "/mnt/brains/it"
LABEL = "brain-it"


class _DB:
    """Nur die beiden Fragen, die ``hybrid_search_with_mode`` wirklich stellt."""

    def __init__(self, zeilen=None):
        self._zeilen = zeilen or []

    async def execute(self, *a, **k):
        class _Erg:
            def __init__(self, zeilen):
                self._z = zeilen

            def all(self):
                return self._z

        return _Erg(self._zeilen)


class SuchmodusTests(unittest.IsolatedAsyncioTestCase):

    async def test_leere_anfrage_sucht_nicht(self):
        modus, treffer = await vs.hybrid_search_with_mode(_DB(), LABEL, HOST, "   ")
        self.assertEqual((modus, treffer), ("hybrid", []))

    async def test_ohne_index_ist_der_modus_grep(self):
        with patch.object(vs, "_has_chunks", self._antwort(False)), \
             patch.object(vs.vault, "search", lambda h, q, l: [{"path": "a.md"}]):
            modus, treffer = await vs.hybrid_search_with_mode(_DB(), LABEL, HOST, "x17137")
        self.assertEqual(modus, "grep")
        self.assertEqual(treffer, [{"path": "a.md"}])

    async def test_mit_index_und_treffern_ist_der_modus_hybrid(self):
        zeilen = [_Zeile(1, "Drucker/x17137.md", "Symptom", "x17137 tritt auf")]
        with patch.object(vs, "_has_chunks", self._antwort(True)), \
             patch.object(vs, "_semantic_ids", self._antwort([1])), \
             patch.object(vs, "_keyword_ids", self._antwort([1])), \
             patch.object(vs.vault, "search",
                          lambda *a: self.fail("der Dateiweg darf hier nicht laufen")):
            modus, treffer = await vs.hybrid_search_with_mode(_DB(zeilen), LABEL, HOST, "x17137")
        self.assertEqual(modus, "hybrid")
        self.assertEqual([t["path"] for t in treffer], ["Drucker/x17137.md"])

    async def test_index_ohne_kandidaten_faellt_auf_den_dateiweg_und_sagt_es(self):
        with patch.object(vs, "_has_chunks", self._antwort(True)), \
             patch.object(vs, "_semantic_ids", self._antwort([])), \
             patch.object(vs, "_keyword_ids", self._antwort([])), \
             patch.object(vs.vault, "search", lambda h, q, l: [{"path": "neu.md"}]):
            with self.assertLogs(vs.logger, level="WARNING") as protokoll:
                modus, treffer = await vs.hybrid_search_with_mode(_DB(), LABEL, HOST, "x17137")
        self.assertEqual(modus, "grep")
        self.assertEqual(treffer, [{"path": "neu.md"}])
        self.assertTrue(any("stale" in z or "index" in z.lower() for z in protokoll.output),
                        f"keine Drift-Warnung: {protokoll.output}")

    async def test_beide_leer_ist_eine_ehrliche_antwort_und_keine_warnung(self):
        """Sonst warnt die Anlage bei jeder Anfrage, die nichts trifft — und das
        echte Drift-Signal geht darin unter."""
        with patch.object(vs, "_has_chunks", self._antwort(True)), \
             patch.object(vs, "_semantic_ids", self._antwort([])), \
             patch.object(vs, "_keyword_ids", self._antwort([])), \
             patch.object(vs.vault, "search", lambda h, q, l: []):
            with patch.object(vs.logger, "warning") as warnung:
                modus, treffer = await vs.hybrid_search_with_mode(_DB(), LABEL, HOST, "gibtsnicht")
        self.assertEqual((modus, treffer), ("grep", []))
        warnung.assert_not_called()

    async def test_der_duenne_wrapper_liefert_weiter_nur_die_treffer(self):
        """Externer MCP-Endpunkt und Sprach-Sitzung haengen an dieser Form —
        der Umbau darf sie nicht aendern."""
        with patch.object(vs, "_has_chunks", self._antwort(False)), \
             patch.object(vs.vault, "search", lambda h, q, l: [{"path": "a.md"}]):
            ergebnis = await vs.hybrid_search(_DB(), LABEL, HOST, "x")
        self.assertEqual(ergebnis, [{"path": "a.md"}])

    @staticmethod
    def _antwort(wert):
        async def _f(*a, **k):
            return wert
        return _f


class _Zeile:
    def __init__(self, id, path, heading, content):
        self.id, self.path, self.heading, self.content = id, path, heading, content


if __name__ == "__main__":
    unittest.main()

"""Der 5-Minuten-Durchlauf ist das Sicherheitsnetz, ohne das #727 schaedlich waere.

Abschnitt 9 der Spec begruendet, warum Hybrid-Suche und Re-Index-Durchlauf
zusammen ausgeliefert werden muessen: ``_has_chunks`` haelt einen Vault schon bei
EINER Chunk-Zeile fuer indiziert. Ein teilweise veralteter Index faellt deshalb
nie auf den Dateiweg zurueck — neu geschriebene Dateien waeren unsichtbar.
Haette man nur die Suche umgestellt, waere das Ergebnis also schlechter als der
Substring-Zaehler von vorher.

Geprueft wird hier der Takt, nicht die Indexierung selbst (die hat mit
``reindex_vault`` einen aelteren, eigenen Aufrufer in der Admin-Oberflaeche):
Drosselung, Isolation eines kaputten Vaults, und dass der Zeitstempel VOR der
Arbeit gesetzt wird.
"""
import unittest
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.services import scheduler_service as ss


class _Brain:
    def __init__(self, label, host_path):
        self.label, self.host_path = label, host_path


class _Ergebnis:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _DB:
    def __init__(self, brains):
        self._brains = brains

    async def execute(self, *a, **k):
        return _Ergebnis(self._brains)


class ReindexDurchlaufTests(unittest.IsolatedAsyncioTestCase):

    def _dienst(self, brains):
        s = ss.SchedulerService.__new__(ss.SchedulerService)
        s._vault_reindex_last_run = None

        @asynccontextmanager
        async def sitzung():
            yield _DB(brains)

        self._sitzung = patch.object(ss, "resilient_session", sitzung)
        self._sitzung.start()
        self.addCleanup(self._sitzung.stop)
        return s

    async def test_intervall_ist_fuenf_minuten(self):
        self.assertEqual(ss._VAULT_REINDEX_INTERVAL, timedelta(minutes=5))

    async def test_laeuft_und_summiert_ueber_alle_vaults(self):
        s = self._dienst([_Brain("brain-it", "/x/it"), _Brain("brain-hr", "/x/hr")])
        aufrufe = []

        async def reindex(db, label, host):
            aufrufe.append(label)
            return {"files": 2, "chunks": 7, "pruned": 1, "errors": 0}

        with patch("app.services.vault_indexer.reindex_vault", reindex):
            stats = await s._tick_vault_reindex()

        self.assertEqual(sorted(aufrufe), ["brain-hr", "brain-it"])
        self.assertEqual(stats, {"brains": 2, "files": 4, "chunks": 14,
                                 "pruned": 2, "errors": 0})

    async def test_zweiter_aufruf_im_intervall_wird_gedrosselt(self):
        """Die Hauptschleife tickt sekuendlich — ohne Drosselung liefe der
        Durchlauf dauernd und wuerde Embeddings verbrennen."""
        s = self._dienst([_Brain("brain-it", "/x/it")])
        laeufe = []

        async def reindex(db, label, host):
            laeufe.append(label)
            return {"files": 0, "chunks": 0, "pruned": 0, "errors": 0}

        with patch("app.services.vault_indexer.reindex_vault", reindex):
            await s._tick_vault_reindex()
            zweiter = await s._tick_vault_reindex()

        self.assertEqual(len(laeufe), 1)
        self.assertEqual(zweiter, {"skipped": 1})

    async def test_nach_ablauf_des_intervalls_laeuft_er_wieder(self):
        s = self._dienst([_Brain("brain-it", "/x/it")])
        laeufe = []

        async def reindex(db, label, host):
            laeufe.append(label)
            return {"files": 0, "chunks": 0, "pruned": 0, "errors": 0}

        with patch("app.services.vault_indexer.reindex_vault", reindex):
            await s._tick_vault_reindex()
            s._vault_reindex_last_run = (
                datetime.now(timezone.utc) - ss._VAULT_REINDEX_INTERVAL - timedelta(seconds=1)
            )
            await s._tick_vault_reindex()

        self.assertEqual(len(laeufe), 2)

    async def test_ein_kaputter_vault_reisst_die_uebrigen_nicht_mit(self):
        s = self._dienst([_Brain("brain-kaputt", "/weg"), _Brain("brain-it", "/x/it")])
        gesund = []

        async def reindex(db, label, host):
            if label == "brain-kaputt":
                raise RuntimeError("Vault nicht gemountet")
            gesund.append(label)
            return {"files": 1, "chunks": 1, "pruned": 0, "errors": 0}

        with patch("app.services.vault_indexer.reindex_vault", reindex):
            stats = await s._tick_vault_reindex()

        self.assertEqual(gesund, ["brain-it"])
        self.assertEqual(stats["errors"], 1)
        self.assertEqual(stats["brains"], 1)

    async def test_zeitstempel_wird_vor_der_arbeit_gesetzt(self):
        """Ein langer Durchlauf darf keinen zweiten gleich hinterher starten."""
        s = self._dienst([_Brain("brain-it", "/x/it")])
        gesehen = {}

        async def reindex(db, label, host):
            gesehen["stempel_waehrend_der_arbeit"] = s._vault_reindex_last_run
            return {"files": 0, "chunks": 0, "pruned": 0, "errors": 0}

        with patch("app.services.vault_indexer.reindex_vault", reindex):
            await s._tick_vault_reindex()

        self.assertIsNotNone(gesehen["stempel_waehrend_der_arbeit"])

    async def test_die_hauptschleife_ruft_ihn_ueberhaupt_auf(self):
        """Ein Durchlauf, den niemand ticken laesst, ist toter Code — genau die
        Klasse, die #727 beim Gegenmessen beim MCP-Server aufgefallen ist."""
        import inspect
        quelle = inspect.getsource(ss.SchedulerService)
        self.assertIn("_tick_vault_reindex()", quelle)


if __name__ == "__main__":
    unittest.main()

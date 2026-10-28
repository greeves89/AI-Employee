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
import asyncio
import unittest
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.services import scheduler_service as ss


async def _bis(bedingung, takt: float = 0.01):
    """Warten, bis die Bedingung gilt — ohne feste Schlafzeit."""
    while not bedingung():
        await asyncio.sleep(takt)


class _Brain(tuple):
    """Eine Zeile ``(label, host_path)`` — so liest der Durchlauf die Brains seit
    dem Rollback-Fix (ORM-Objekte verfallen nach einem Rollback)."""

    def __new__(cls, label, host_path):
        return super().__new__(cls, (label, host_path))

    @property
    def label(self):
        return self[0]


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

    async def rollback(self):
        self.zurueckgerollt = getattr(self, "zurueckgerollt", 0) + 1


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

    async def test_der_starter_laesst_den_durchlauf_wirklich_laufen(self):
        """Nebenlaeufig heisst nicht losgelassen: die Aufgabe muss entstehen,
        wirklich laufen, und ein zweiter Anstoss darf sie nicht doppeln."""
        s = self._dienst([_Brain("brain-it", "/x/it")])
        s._vault_reindex_task = None
        laeufe = []
        weiter = asyncio.Event()

        async def reindex(db, label, host):
            laeufe.append(label)
            await weiter.wait()          # Durchlauf haengt absichtlich
            return {"files": 0, "chunks": 0, "pruned": 0, "errors": 0}

        with patch("app.services.vault_indexer.reindex_vault", reindex):
            self.assertTrue(s._starte_vault_reindex())
            await asyncio.wait_for(_bis(lambda: laeufe == ["brain-it"]), timeout=2)
            # Waehrend er laeuft: kein zweiter Durchlauf desselben Vaults.
            self.assertFalse(s._starte_vault_reindex())
            weiter.set()
            await asyncio.wait_for(s._vault_reindex_task, timeout=2)

        self.assertEqual(laeufe, ["brain-it"])

    async def test_der_starter_blockiert_die_schleife_nicht(self):
        """Der Grund fuer die Auslagerung: der Durchlauf liest jede Datei jedes
        Vaults. Inline erwartet waere das eine Pause der ganzen Schleife."""
        s = self._dienst([_Brain("brain-it", "/x/it")])
        s._vault_reindex_task = None
        weiter = asyncio.Event()

        async def reindex(db, label, host):
            await weiter.wait()
            return {"files": 0, "chunks": 0, "pruned": 0, "errors": 0}

        with patch("app.services.vault_indexer.reindex_vault", reindex):
            vorher = asyncio.get_running_loop().time()
            s._starte_vault_reindex()
            # Der Anstoss selbst kehrt sofort zurueck, obwohl der Durchlauf haengt.
            self.assertLess(asyncio.get_running_loop().time() - vorher, 0.5)
            weiter.set()
            await asyncio.wait_for(s._vault_reindex_task, timeout=2)

    async def test_eine_ausnahme_im_durchlauf_wird_protokolliert_nicht_verschluckt(self):
        """Eine losgelassene Aufgabe, deren Ausnahme niemand abholt, ist
        unsichtbar — deshalb haengt am Starter ein Abschluss-Rueckruf."""
        s = self._dienst([_Brain("brain-it", "/x/it")])
        s._vault_reindex_task = None

        async def kaputt(*a, **k):
            raise RuntimeError("Datenbank weg")

        with patch.object(ss.SchedulerService, "_tick_vault_reindex", kaputt), \
             self.assertLogs(ss.logger, level="WARNING") as protokoll:
            s._starte_vault_reindex()
            await asyncio.sleep(0)
            await asyncio.sleep(0)
        self.assertTrue(any("Datenbank weg" in z for z in protokoll.output),
                        f"Ausnahme nicht protokolliert: {protokoll.output}")
        self.assertIsNone(s._vault_reindex_task)

    def test_die_hauptschleife_ruft_den_starter_wirklich_auf(self):
        """Ein Durchlauf, den niemand ticken laesst, ist toter Code — genau die
        Klasse, die in diesem Issue beim MCP-Server schon einmal zugeschlagen hat.

        Geprueft wird der SYNTAXBAUM der Schleifenmethode, nicht ihr Quelltext:
        eine Textsuche besteht ein auskommentiertes ``pass  # await
        self._tick_vault_reindex()`` klaglos — ein Kommentar ist kein Knoten.
        """
        import ast
        import inspect

        baum = ast.parse(inspect.getsource(ss).replace("\t", "    "))
        lauf = next(
            (k for k in ast.walk(baum)
             if isinstance(k, (ast.AsyncFunctionDef, ast.FunctionDef)) and k.name == "run"),
            None,
        )
        self.assertIsNotNone(lauf, "Schleifenmethode `run` nicht gefunden")
        aufrufe = [
            k for k in ast.walk(lauf)
            if isinstance(k, ast.Call) and isinstance(k.func, ast.Attribute)
            and k.func.attr in ("_starte_vault_reindex", "_tick_vault_reindex")
        ]
        self.assertTrue(
            aufrufe,
            "Die Scheduler-Schleife stoesst den Re-Index-Durchlauf nicht an — "
            "er wuerde nie laufen.",
        )


if __name__ == "__main__":
    unittest.main()

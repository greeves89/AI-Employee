"""Zu grosse Gesetze werden aufgeteilt statt verworfen (#866).

Das SGB V war als EINE Markdown-Datei groesser als die 2-MB-Grenze des
Vaults (``vault.MAX_FILE_BYTES``). ``write_file`` lehnte es bei jedem Lauf ab,
die Norm fehlte dauerhaft in der Wissensbasis, und die Sammelmeldung nannte
nur den Bereich. Jetzt: mehrere Teildateien unter der Grenze, verwaiste Teile
eines frueheren, laengeren Stands werden entfernt, und die Meldung nennt die
betroffenen Gesetze samt Hinweis, wenn derselbe Fehlschlag stehen bleibt.
"""

import asyncio
import os
import tempfile
import unittest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

from app.core import vault
from app.services import gesetz_crawler as gc


def _gesetz(anzahl: int, textlaenge: int = 1000, jurabk: str = "SGB V") -> dict:
    return {
        "jurabk": jurabk,
        "title": "Sozialgesetzbuch Fünftes Buch",
        "paragraphs": [
            {"enbez": f"§ {i}", "titel": f"Titel {i}", "text": f"Absatz {i} " + "ä" * textlaenge}
            for i in range(1, anzahl + 1)
        ],
    }


def _groesse(md: str) -> int:
    return len(md.encode("utf-8"))


class AufteilenTests(unittest.TestCase):
    def test_kleines_gesetz_bleibt_eine_datei_wie_bisher(self):
        law = _gesetz(3)
        self.assertEqual(gc.render_markdown_teile(law), [gc.render_markdown(law)])

    def test_grosses_gesetz_wird_in_teile_unter_der_grenze_geteilt(self):
        law = _gesetz(60, textlaenge=500)  # ~60 KB
        teile = gc.render_markdown_teile(law, max_bytes=10_000)
        self.assertGreater(len(teile), 1)
        for nr, md in enumerate(teile, 1):
            self.assertLessEqual(_groesse(md), 10_000)
            self.assertTrue(md.startswith(f"# SGB V — Sozialgesetzbuch Fünftes Buch (Teil {nr} von {len(teile)})"))

    def test_kein_paragraph_geht_verloren_oder_doppelt_reihenfolge_bleibt(self):
        law = _gesetz(60, textlaenge=500)
        alles = "\n".join(gc.render_markdown_teile(law, max_bytes=10_000))
        positionen = [alles.find(f"## § {i} Titel {i}\n") for i in range(1, 61)]
        self.assertNotIn(-1, positionen)
        self.assertEqual(positionen, sorted(positionen))
        for i in range(1, 61):
            self.assertEqual(alles.count(f"## § {i} Titel {i}\n"), 1)

    def test_ein_einzelner_riesiger_paragraph_wird_ebenfalls_geteilt(self):
        law = {
            "jurabk": "X",
            "title": "Y",
            "paragraphs": [{"enbez": "§ 1", "titel": "", "text": "\n\n".join(["ö" * 3000] * 20)}],
        }
        teile = gc.render_markdown_teile(law, max_bytes=10_000)
        self.assertGreater(len(teile), 1)
        for md in teile:
            self.assertLessEqual(_groesse(md), 10_000)
        # Der ganze Text ist noch da (Umlaute ganz, nichts abgeschnitten).
        self.assertEqual(sum(md.count("ö") for md in teile), 60_000)
        self.assertIn("## § 1 (Fortsetzung)", teile[1])

    def test_ein_absatz_ohne_absatzgrenze_wird_hart_geteilt_ohne_zeichen_zu_zerbrechen(self):
        law = {"jurabk": "X", "title": "Y", "paragraphs": [{"enbez": "§ 1", "titel": "", "text": "ü" * 30_000}]}
        teile = gc.render_markdown_teile(law, max_bytes=10_000)
        for md in teile:
            self.assertLessEqual(_groesse(md), 10_000)
        self.assertEqual(sum(md.count("ü") for md in teile), 30_000)

    def test_standardgrenze_liegt_unter_der_grenze_des_vaults(self):
        self.assertLess(gc.TEIL_MAX_BYTES, vault.MAX_FILE_BYTES)

    def test_dateinamen_erster_teil_behaelt_den_alten_namen(self):
        self.assertEqual(gc._teil_pfad("sgb_5", 1), "sgb_5.md")
        self.assertEqual(gc._teil_pfad("sgb_5", 3), "sgb_5.teil-3.md")


class _Toc:
    def __init__(self, laws):
        self.laws = laws

    async def toc(self, _client):
        return [(slug, slug) for slug in self.laws]

    async def eine(self, _client, slug):
        return self.laws[slug]


class CrawlSchreibtTeileTests(unittest.TestCase):
    """VERHALTEN am echten crawl(): echte Dateien im Vault-Ordner, Index als Attrappe."""

    def setUp(self):
        self.ordner = tempfile.mkdtemp()
        self.indiziert: list[str] = []
        self.entfernt: list[str] = []

        async def _index(_db, label, host, rel):
            self.indiziert.append(rel)

        async def _remove(_db, label, rel):
            self.entfernt.append(rel)

        @asynccontextmanager
        async def _sitzung():
            yield AsyncMock()

        self._patches = [
            patch.object(gc, "HOST_PATH", self.ordner),
            patch.object(gc, "TEIL_MAX_BYTES", 10_000),
            patch("app.services.vault_indexer.index_file", _index),
            patch("app.services.vault_indexer.remove_file", _remove),
            patch("app.db.session.resilient_session", _sitzung),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()

    def _lauf(self, laws: dict) -> int:
        q = _Toc(laws)
        with patch.object(gc.GesetzCrawlerService, "_fetch_toc", q.toc), \
                patch.object(gc.GesetzCrawlerService, "_fetch_one", q.eine):
            return asyncio.run(gc.GesetzCrawlerService().crawl())

    def test_zu_grosses_gesetz_landet_als_teile_im_vault_und_im_index(self):
        with self.assertNoLogs("app.services.gesetz_crawler", level="WARNING"):
            geschrieben = self._lauf({"sgb_5": _gesetz(60, textlaenge=500), "klein": _gesetz(2)})
        self.assertEqual(geschrieben, 2)
        dateien = sorted(os.listdir(self.ordner))
        self.assertIn("sgb_5.md", dateien)
        self.assertIn("sgb_5.teil-2.md", dateien)
        self.assertIn("klein.md", dateien)
        self.assertEqual(sorted(self.indiziert), sorted(f for f in dateien if f.endswith(".md")))

    def test_verwaiste_teile_eines_frueheren_staends_werden_entfernt(self):
        self._lauf({"sgb_5": _gesetz(60, textlaenge=500)})
        vorher = {f for f in os.listdir(self.ordner) if f.startswith("sgb_5.teil-")}
        self.assertGreater(len(vorher), 1)
        self.entfernt.clear()

        self._lauf({"sgb_5": _gesetz(2)})  # Gesetz ist jetzt klein

        self.assertEqual(sorted(os.listdir(self.ordner)), ["sgb_5.md"])
        self.assertEqual(sorted(self.entfernt), sorted(vorher))

    def test_fremde_dateien_mit_aehnlichem_namen_bleiben_unberuehrt(self):
        with open(os.path.join(self.ordner, "sgb_5_neu.md"), "w") as fh:
            fh.write("x")
        self._lauf({"sgb_5": _gesetz(2)})
        self.assertIn("sgb_5_neu.md", os.listdir(self.ordner))
        self.assertEqual(self.entfernt, [])


class StehenderFehlschlagTests(unittest.TestCase):
    def test_meldung_nennt_die_betroffenen_kennungen(self):
        z = gc._AusfallZaehler("Test")
        for k in ("a", "b", "c", "d", "e", "f", "g"):
            z.melde(k, ValueError("x"))
        bericht = z.bericht(10)
        self.assertIn("betroffen: a, b, c, d, e …", bericht)

    def test_derselbe_fehlschlag_ueber_mehrere_laeufe_wird_so_benannt(self):
        dienst = gc.GesetzCrawlerService()

        async def _toc(_s, _c):
            return [("kaputt", "kaputt")]

        async def _eine(_s, _c, slug):
            raise RuntimeError("immer derselbe")

        meldungen = []
        with patch.object(gc.GesetzCrawlerService, "_fetch_toc", _toc), \
                patch.object(gc.GesetzCrawlerService, "_fetch_one", _eine):
            for _ in range(3):
                with self.assertLogs("app.services.gesetz_crawler", level="WARNING") as log:
                    asyncio.run(dienst.crawl())
                meldungen.append("\n".join(log.output))
        self.assertNotIn("unverändert", meldungen[0])
        self.assertIn("unverändert seit 2 Läufen", meldungen[1])
        self.assertIn("unverändert seit 3 Läufen", meldungen[2])


if __name__ == "__main__":
    unittest.main()

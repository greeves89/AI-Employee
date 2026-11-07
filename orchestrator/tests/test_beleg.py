"""„Fertig nur mit Beleg“ (core/beleg) — Erkennung und alle Stellen, an denen sie wirkt.

* Erkennung: eine Zeile „Beleg: …“ oder „Ungeprüft: …“, auch hinter Markdown-Zeichen;
  eine bloße Erwähnung im Satz zählt nicht.
* Ziel-Modus: „ZIEL ERREICHT“ ohne Beleg wird EINMAL nachgefordert (eine weitere Runde
  mit dem Hinweis), beim zweiten Mal gilt es — keine Schleife bis zur Rundengrenze.
* Aufträge: der Stand landet in ``task.metadata_["beleg"]``, das Board zeigt ihn im Review.
* Anleitung: der Abschnitt steht in jeder Laufzeit.
"""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core import beleg, ziel
from app.models.chat_session import ChatSession


class ErkennungTests(unittest.TestCase):
    def test_staende(self):
        faelle = {
            "Erledigt.\nBeleg: pytest 42 grün": beleg.BELEG,
            "**Beleg:** curl -I liefert 200": beleg.BELEG,
            "- Beleg: Screenshot unter /workspace/transfer/x.png": beleg.BELEG,
            "> Ungeprüft: kein Zugang zur Testumgebung": beleg.UNGEPRUEFT,
            "Ungeprueft: Abbild fehlt": beleg.UNGEPRUEFT,
            "Beleg: a\nUngeprüft: b": beleg.BELEG,
            # Live so geschrieben (1.436.0, Demo-Agent): Beleg im selben Satz nach Gedankenstrich.
            "Erledigt. Datei `/workspace/beleg_test.txt` mit Inhalt `42` angelegt — Beleg: "
            "`cat /workspace/beleg_test.txt` ergab `42`.": beleg.BELEG,
            "Fertig. Beleg: Tests grün": beleg.BELEG,
            "Fertig (Beleg: Tests grün)": beleg.FEHLT,               # Klammer-Auf ist kein Trenner
            "Fertig; ungeprüft: kein Zugang": beleg.UNGEPRUEFT,
            "**Beleg**: Bildschirmfoto liegt bei": beleg.BELEG,
            "Erledigt, alles gut.": beleg.FEHLT,
            "Ich habe den Beleg: noch nicht": beleg.FEHLT,
            "Den Beleg: liefere ich später": beleg.FEHLT,       # im Satz, nicht als Zeile
            "Beleg:": beleg.FEHLT,                              # leer zählt nicht
            "": beleg.FEHLT,
            None: beleg.FEHLT,
        }
        for text, erwartet in faelle.items():
            with self.subTest(text=text):
                self.assertEqual(beleg.stand(text), erwartet)


class ZielTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(ChatSession.metadata.create_all, tables=[ChatSession.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        self.gesetzt = set()

        async def setzen(schluessel, wert, nx=False, ex=None):
            if nx and schluessel in self.gesetzt:
                return None
            self.gesetzt.add(schluessel)
            return True
        self.redis = SimpleNamespace(set=setzen)
        self.p = patch.object(ziel, "naechste_runde", AsyncMock(return_value="neu"))
        self.naechste = self.p.start()
        async with self.Session() as db:
            await ziel.setzen(db, "a1", "s1", "Snake fertig")

    async def asyncTearDown(self):
        self.p.stop()
        await self.engine.dispose()

    async def zug(self, mid, antwort):
        async with self.Session() as db:
            return await ziel.nach_zug(db, self.redis, "a1", "s1", mid, antwort)

    async def test_mit_beleg_fertig(self):
        self.assertEqual(await self.zug("m1", "Snake läuft.\nBeleg: Spiel gestartet, 3 Züge gespielt\nZIEL ERREICHT"),
                         ziel.ERLEDIGT)
        self.naechste.assert_not_awaited()

    async def test_ungeprueft_gilt_als_ehrliche_meldung(self):
        self.assertEqual(await self.zug("m1", "Ungeprüft: kein Bildschirm\nZIEL ERREICHT"), ziel.ERLEDIGT)

    async def test_ohne_beleg_einmal_nachfordern_dann_gilt_es(self):
        self.assertEqual(await self.zug("m1", "Fertig.\nZIEL ERREICHT"), ziel.AKTIV)
        self.naechste.assert_awaited_once()
        self.assertTrue(self.naechste.await_args.kwargs["beleg_fehlt"])
        self.assertEqual(await self.zug("m2", "Fertig, wirklich.\nZIEL ERREICHT"), ziel.ERLEDIGT,
                         "kein zweites Nachfordern — sonst Schleife bis zur Rundengrenze")
        self.assertEqual(self.naechste.await_count, 1)

    async def test_nachfordern_nicht_ueber_die_rundengrenze(self):
        async with self.Session() as db:
            z = await db.scalar(select(ChatSession))
            z.goal_rounds = ziel.ZIEL_MAX_RUNDEN - 1
            await db.commit()
        self.assertEqual(await self.zug("m1", "Fertig.\nZIEL ERREICHT"), ziel.ERLEDIGT)

    async def test_hinweis_landet_vor_dem_auftrag(self):
        self.p.stop()
        eingereiht = {}

        async def einreihen(redis, agent_id, nachricht, **k):
            eingereiht.update(nachricht)
            return SimpleNamespace(eingereiht=True)
        with patch("app.services.chat_persistence.upsert_chat_message", AsyncMock(return_value=True)), \
                patch("app.core.chat_auftrag.einreihen", einreihen):
            await ziel.naechste_runde(self.redis, "a1", "s1", "Snake fertig", 2, beleg_fehlt=True)
        self.p.start()
        self.assertTrue(eingereiht["text"].startswith(ziel.BELEG_NACHFORDERN))
        self.assertIn("Snake fertig", eingereiht["text"])

    def test_regeln_verlangen_den_beleg(self):
        self.assertIn("Beleg:", ziel.auftrag("x", 1))


class AuftragUndBoardTests(unittest.TestCase):
    def test_task_router_vermerkt_den_stand(self):
        """Verdrahtung im Syntaxbaum: der Abschluss eines Auftrags setzt metadata_['beleg']."""
        import ast
        from pathlib import Path
        quelle = (Path(__file__).resolve().parents[1] / "app/core/task_router.py").read_text()
        fn = next(n for n in ast.walk(ast.parse(quelle))
                  if isinstance(n, ast.AsyncFunctionDef) and n.name == "handle_task_completion")
        self.assertIn("_beleg_stand(task.result)", ast.get_source_segment(quelle, fn))

    def test_karte_zeigt_den_stand_im_review(self):
        from app.core.projekte import karte_dict
        from app.models.projekt_karte import ProjektKarte
        from app.models.task import Task, TaskStatus
        karte = ProjektKarte(id="k1", team_id="t1", titel="x", text="", quelle="eigen", spalte="pruefung",
                             task_id="t9", labels=[])
        lauf = Task(id="t9", title="x", prompt="x", status=TaskStatus.COMPLETED, result="Fertig.",
                    metadata_={"beleg": "fehlt", "karte_id": "k1"})
        self.assertEqual(karte_dict(karte, lauf)["beleg"], "fehlt")
        karte.spalte = "in_arbeit"
        self.assertIsNone(karte_dict(karte, lauf)["beleg"], "nur wo das Ergebnis gezeigt wird")

    def test_kartenauftrag_nennt_die_regel(self):
        from app.core.projekte import karten_auftrag
        from app.models.projekt_karte import ProjektKarte
        karte = ProjektKarte(id="k1", team_id="t1", titel="x", text="y", quelle="eigen", spalte="bereit", labels=[])
        self.assertIn("Beleg:", karten_auftrag(karte, "Projekt"))


class AnleitungTests(unittest.TestCase):
    def test_abschnitt_in_jeder_laufzeit(self):
        from app.core.agent_manager import _render_claude_md
        for mode in ("claude_code", "codex_cli", "custom_llm"):
            with self.subTest(mode):
                text = _render_claude_md([], {}, agent_name="A", mode=mode)
                self.assertIn("## Fertig nur mit Beleg", text)
                self.assertIn(beleg.REGEL, text)

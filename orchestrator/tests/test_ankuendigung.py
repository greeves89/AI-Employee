"""#891 — Ein Agent kuendigt Arbeit an und beendet dann seinen Zug.

Zwei Teile:

* ``ist_ankuendigung``: Erkennung an den Schlusssaetzen der Antwort (de + en).
  Fragen an den Nutzer, Angebote und abgeschlossene Ergebnisse duerfen NICHT
  anschlagen.
* ``nach_zug``: Entscheidung nach kurzer Wartezeit. Nur wenn danach weder ein Zug
  noch eine Aufgabe laeuft und kein /goal weitertreibt, erscheint im Gespraech die
  Statuszeile „Der Agent arbeitet gerade nicht weiter.“
"""

import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models.agent  # noqa: F401 — Fremdschluessel tasks.agent_id braucht die Tabelle im Metadatenbestand
from app.core import ankuendigung, ziel
from app.models.chat_message import ChatMessage
from app.models.chat_session import ChatSession
from app.models.task import Task, TaskStatus


POSITIV = [
    # Die Faelle aus dem Issue
    "Ich passe noch die Übergänge an und rendere dann das ganze Video.",
    "Alles klar, ich mache das im Hintergrund.",
    "Das Skript steht. Ich melde mich, sobald das Video fertig ist.",
    # weitere deutsche Ankuendigungen
    "Jetzt baue ich die Startseite.",
    "Als Nächstes schreibe ich die Tests.",
    "Ich werde die Daten jetzt auswerten.",
    "Einen Moment, ich schaue mir das an.",
    "Gib mir ein paar Minuten.",
    "Ich starte jetzt den Export. Das dauert etwa zehn Minuten.",
    "Ich kümmere mich gleich darum.",
    "Super Idee! Ich lege direkt los.",
    "Hier der Plan:\n\n1. Daten laden\n2. Auswerten\n\nIch fange jetzt mit Schritt 1 an.",
    "Ich habe die Farben angepasst und rendere jetzt das Video.",
    "Ich warte noch, bis der Build durch ist, und melde mich dann.",
    "Okay, ich mach das.",
    "Kein Problem, mache ich.",
    "Damit ist der Fehler behoben. Ich teste das morgen nochmal live.",
    "Ich lade jetzt die Datei hoch und prüfe danach die Vorschau.",
    # Hoeflichkeitsfloskel am Ende macht die Ankuendigung nicht ungeschehen
    "Ich rendere jetzt das Video.\n\nSag Bescheid, falls du Änderungen willst.",
    "**Ich rendere jetzt das finale Video.**",
    # englisch
    "I'll render the full video next.",
    "Let me run the tests now.",
    "I'm working on it in the background and will get back to you.",
    "Give me a few minutes.",
    "Next, I will deploy the app.",
    "Got it. I’m on it.",
]

NEGATIV = [
    "",
    "   ",
    # Fragen an den Nutzer
    "Soll ich das Video jetzt rendern?",
    "Ich rendere jetzt das Video. Soll ich danach auch Untertitel ergänzen?",
    "Möchtest du, dass ich weitermache?",
    "Ich würde jetzt als Nächstes die Tests schreiben — passt das?",
    "Should I deploy it now?",
    # Angebote, Hoeflichkeit
    "Das Video ist fertig. Wenn du willst, rendere ich danach noch eine kurze Fassung.",
    "Sag Bescheid, wenn ich weitermachen soll.",
    "Ich warte auf deine Rückmeldung.",
    "Ich hoffe, das hilft dir weiter!",
    "Ich empfehle, das jetzt zu testen.",
    "Let me know if you want changes.",
    "If you'd like, I can also add subtitles.",
    "Bei Fragen melde dich gern.",
    # Abgeschlossenes
    "Ich habe das Video gerendert und unter /workspace/video.mp4 abgelegt.",
    "Ich habe alle Tests erledigt. Fertig.",
    "Erledigt: Die Startseite ist gebaut.",
    "Ich habe jetzt alles umgestellt.",
    "Die Seite läuft jetzt.",
    "Ich bin jetzt fertig.",
    "Ich habe die Farben angepasst und rendere jetzt das Video.\n\nFertig — hier ist der Link: /workspace/video.mp4",
    "Ich schaue mir zuerst die Logs an.\n\nErgebnis: Der Fehler lag in der Konfiguration. "
    "Ich habe ihn behoben und den Dienst neu gestartet.",
    "I've rendered the video and saved it to /workspace/out.mp4.",
    "Done. All tests pass.",
    # Bedarf statt Ankuendigung
    "Ich bräuchte noch die Zugangsdaten.",
    "Ich denke, das passt jetzt so.",
    "Ich stelle fest, dass die Daten jetzt stimmen.",
    "Alles erledigt. Ich bin hier, falls du noch etwas brauchst.",
    "I'll be here if you need anything else.",
    "Wie mache ich das am besten?",
    "Ich werde das nicht weiter verfolgen.",
    # Ankuendigung nur im Codeblock
    "Beispiel:\n\n```\nich rendere jetzt das Video\n```",
]


class ErkennungTests(unittest.TestCase):
    def test_ankuendigungen_schlagen_an(self):
        for text in POSITIV:
            with self.subTest(text=text):
                self.assertTrue(ankuendigung.ist_ankuendigung(text))

    def test_fragen_angebote_und_ergebnisse_schlagen_nicht_an(self):
        for text in NEGATIV:
            with self.subTest(text=text):
                self.assertFalse(ankuendigung.ist_ankuendigung(text))

    def test_ansage_vor_einem_werkzeugschritt_ist_keine_ankuendigung(self):
        """Abnahme v1.362.1: normal beendete Antworten bekamen „Weitermachen“.

        Der gespeicherte Text eines Zugs enthaelt auch die Saetze VOR einem
        Werkzeugaufruf („Ich erstelle jetzt …“) — als eigenen Absatz, denn ein
        neuer Textblock beginnt nach jedem Werkzeug mit einer Leerzeile. Was
        danach kommt, ist das Ergebnis. Bisher zaehlten die letzten beiden Saetze
        ueber die Absatzgrenze hinweg, und ein Codeblock am Ende verschwand ganz:
        die Ansage stand dann als „letzter Satz“ da.
        """
        for text in (
            # Ergebnis endet mit einem Codeblock (Buchungszeile, Skript, CSV)
            "Ich erstelle jetzt den DATEV-Buchungsstapel.\n\nHier die Buchungszeile:\n\n"
            "```csv\n119,00;S;EUR;4930;70000\n```",
            "Ich schreibe dir das Skript jetzt.\n\n```python\nprint('hallo')\n```",
            # Ansage, Werkzeug, kurzes Ergebnis ohne „fertig“
            "Ich schaue kurz in die Rechnung.\n\nBuchungssatz: 4930 Bürobedarf an 70000 Lieferant",
            "Ich führe den Befehl jetzt aus.\n\nAusgabe: abnahme-911",
        ):
            with self.subTest(text=text):
                self.assertFalse(ankuendigung.ist_ankuendigung(text))

    def test_wartet_auf_die_antwort_des_menschen(self):
        """Nachabnahme v1.362.2: Der Agent hatte nachgefragt und wartete zu Recht."""
        for text in (
            "Dafür brauche ich noch deine Firmendaten und den Kunden.\n\n"
            "Sobald ich das habe, kalkuliere ich das Angebot und liefere es dir direkt als PDF.",
            "Mir fehlen noch Rechnungsnummer und Datum. Sobald ich diese Angaben habe, erstelle ich "
            "die Buchungszeile. Dann liefere ich dir sofort die CSV.",
            "Bitte nenne mir den Empfänger. Ich schreibe die Mail dann gleich.",
            "Wenn du mir die Datei schickst, werte ich sie sofort aus.",
            "Verstanden — ich frage dich ab jetzt immer erst per Rückfrage, bevor ich eine Notiz anlege.",
        ):
            with self.subTest(text=text):
                self.assertFalse(ankuendigung.ist_ankuendigung(text))

    def test_echte_ankuendigung_bleibt_erkannt(self):
        for text in (
            "Ich rendere jetzt das Video.",
            "Die Daten habe ich. Ich baue jetzt die Auswertung und melde mich gleich.",
        ):
            with self.subTest(text=text):
                self.assertTrue(ankuendigung.ist_ankuendigung(text))

    def test_ankuendigung_nach_dem_codeblock_zaehlt_weiter(self):
        self.assertTrue(ankuendigung.ist_ankuendigung(
            "Hier der Entwurf:\n\n```\nTitel: Sommerfest\n```\n\nIch rendere jetzt das Video."))

    def test_zweiter_satz_desselben_absatzes_zaehlt_weiter(self):
        """Die Zwei-Satz-Regel bleibt — nur nicht ueber eine Absatzgrenze."""
        self.assertTrue(ankuendigung.ist_ankuendigung(
            "Ich passe jetzt die Farben an. Die Startseite bekommt drei Abschnitte."))

    def test_nur_das_ende_zaehlt(self):
        # Mitten im Text angekuendigt, am Ende Ergebnis geliefert.
        text = ("Ich schaue jetzt in die Tabelle.\n\nDie Umsätze liegen bei 12 Prozent über Vorjahr.\n\n"
                "Die größten Treiber sind Region Nord und Süd.")
        self.assertFalse(ankuendigung.ist_ankuendigung(text))


class _FakeRedis:
    def __init__(self):
        self.kv: dict = {}
        self.hashes: dict = {}
        self.lists: dict = {}
        self.published: list = []

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.kv:
            return None
        self.kv[key] = value
        return True

    async def hgetall(self, key):
        return self.hashes.get(key, {})

    async def llen(self, key):
        return len(self.lists.get(key, []))

    async def publish(self, channel, message):
        self.published.append((channel, message))
        return 1


ANKUENDIGUNG = "Ich passe noch die Übergänge an und rendere dann das ganze Video."


class EntscheidungTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(ChatSession.metadata.create_all, tables=[
                ChatSession.__table__, ChatMessage.__table__, Task.__table__,
            ])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        self.redis = _FakeRedis()
        self.beginn = datetime.now(timezone.utc) - timedelta(minutes=2)
        async with self.Session() as db:
            db.add(ChatMessage(agent_id="a1", session_id="s1", message_id="m1", role="user",
                               content="Mach das Video fertig.", timestamp=self.beginn))
            db.add(ChatMessage(agent_id="a1", session_id="s1", message_id="m1", role="assistant",
                               content=ANKUENDIGUNG, timestamp=self.beginn + timedelta(seconds=30)))
            await db.commit()
        self.zeile = patch("app.services.chat_persistence.upsert_chat_message",
                           AsyncMock(return_value=True))
        self.upsert = self.zeile.start()

    async def asyncTearDown(self):
        self.zeile.stop()
        await self.engine.dispose()

    async def _zug(self, antwort=ANKUENDIGUNG, mid="m1", session="s1", fehler=False):
        lauf = ankuendigung.nach_zug(
            self.redis, "a1", session, mid, antwort, fehlgeschlagen=fehler,
            warten=0, session_factory=self.Session,
        )
        if lauf is None:
            return None
        return await lauf

    def _hinweis_geschrieben(self) -> bool:
        return self.upsert.await_count > 0

    async def test_ankuendigung_und_nichts_laeuft_schreibt_hinweis(self):
        self.assertTrue(await self._zug())
        args, kwargs = self.upsert.await_args
        self.assertEqual(args[:2], ("a1", "s1"))
        self.assertEqual(args[3], "system")
        self.assertEqual(kwargs["content"], ankuendigung.HINWEIS)
        self.assertEqual(kwargs["meta"]["source"], ankuendigung.QUELLE)
        # Live in DIESES Gespraech, ohne fremde Nachrichtenkennung (sonst verwirft der WS).
        kanal, nutzlast = self.redis.published[-1]
        self.assertEqual(kanal, "agent:a1:chat:response")
        ereignis = json.loads(nutzlast)
        self.assertEqual(ereignis["type"], ankuendigung.QUELLE)
        self.assertEqual(ereignis["session_id"], "s1")
        self.assertNotIn("message_id", ereignis)

    async def test_ohne_ankuendigung_keine_pruefung(self):
        self.assertIsNone(await self._zug(antwort="Fertig. Das Video liegt unter /workspace/video.mp4."))
        self.assertFalse(self._hinweis_geschrieben())

    async def test_laufende_aufgabe_unterdrueckt(self):
        async with self.Session() as db:
            db.add(Task(id="t1", title="Rendern", prompt="x", agent_id="a1",
                        status=TaskStatus.RUNNING, metadata_={}))
            await db.commit()
        self.assertFalse(await self._zug())
        self.assertFalse(self._hinweis_geschrieben())

    async def test_im_zug_angelegte_aufgabe_fuer_kollegen_unterdrueckt(self):
        async with self.Session() as db:
            db.add(Task(id="t2", title="Schnitt", prompt="x", agent_id="b2",
                        status=TaskStatus.PENDING, metadata_={"created_by_agent": "a1"},
                        created_at=self.beginn + timedelta(seconds=10)))
            await db.commit()
        self.assertFalse(await self._zug())

    async def test_alte_liegengebliebene_aufgabe_unterdrueckt_nicht(self):
        async with self.Session() as db:
            db.add(Task(id="t3", title="Alt", prompt="x", agent_id="a1",
                        status=TaskStatus.PENDING, metadata_={},
                        created_at=self.beginn - timedelta(days=3)))
            await db.commit()
        self.assertTrue(await self._zug())

    async def test_fertige_aufgabe_unterdrueckt_nicht(self):
        async with self.Session() as db:
            db.add(Task(id="t4", title="Fertig", prompt="x", agent_id="a1",
                        status=TaskStatus.COMPLETED, metadata_={},
                        created_at=self.beginn + timedelta(seconds=10)))
            await db.commit()
        self.assertTrue(await self._zug())

    async def test_aktives_ziel_unterdrueckt(self):
        async with self.Session() as db:
            await ziel.setzen(db, "a1", "s1", "Video fertig")
        self.assertFalse(await self._zug())
        self.assertFalse(self._hinweis_geschrieben())

    async def test_erreichtes_ziel_unterdrueckt_nicht(self):
        async with self.Session() as db:
            await ziel.setzen(db, "a1", "s1", "Video fertig")
            await ziel.beenden(db, "a1", "s1", status=ziel.ERLEDIGT)
        self.assertTrue(await self._zug())

    async def test_agent_arbeitet_noch_unterdrueckt(self):
        self.redis.hashes["agent:a1:status"] = {"state": "working", "current_task": "chat:s2",
                                                 "active_sessions": '["chat:s2"]'}
        self.assertFalse(await self._zug())

    async def test_ruhender_agent_unterdrueckt_nicht(self):
        self.redis.hashes["agent:a1:status"] = {b"state": b"idle", b"current_task": b"",
                                                 b"active_sessions": b"[]"}
        self.assertTrue(await self._zug())

    async def test_wartende_chatnachricht_unterdrueckt(self):
        self.redis.lists["agent:a1:chat"] = ['{"id": "m2"}']
        self.assertFalse(await self._zug())

    async def test_neuere_nachricht_im_gespraech_unterdrueckt(self):
        async with self.Session() as db:
            db.add(ChatMessage(agent_id="a1", session_id="s1", message_id="m2", role="user",
                               content="Und?", timestamp=self.beginn + timedelta(seconds=40)))
            await db.commit()
        self.assertFalse(await self._zug())

    async def test_dieselbe_fertigmeldung_nur_einmal(self):
        self.assertTrue(await self._zug())
        self.assertFalse(await self._zug())
        self.assertEqual(self.upsert.await_count, 1)

    async def test_fehlgeschlagener_zug_und_zeitplan_ohne_pruefung(self):
        self.assertIsNone(await self._zug(fehler=True))
        self.assertIsNone(await self._zug(session="scheduler"))
        self.assertFalse(self._hinweis_geschrieben())


if __name__ == "__main__":
    unittest.main()

"""#916: Die Anweisung an das Modell gehört nicht in die Nachricht des Menschen.

Nach einem Datei-Upload stand „[Angehängte Datei(en) … WICHTIG: … Read-Tool …]"
sichtbar in der eigenen Nachricht und im Gesprächstitel. Der Browser hatte sie in
den Text gebaut, gespeichert wurde, was der Agent bekam.

Jetzt schickt der Browser ``{text, anhaenge, plan}``; der Server baut daraus den
Auftrag (``app.core.chat_auftrag``) und speichert nur den Nutzertext samt
``meta.anhaenge``. Geprüft wird das Verhalten: Was der Agent bekommt, was in der
Datenbank steht, was als Titel herauskommt, was aus Altzeilen wird.
"""

import os
import tempfile
import unittest

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core import chat_auftrag
from app.core.chat_history import derive_title
from app.models.chat_message import ChatMessage
from app.models.chat_session import ChatSession

AGENT = "a1"

# So schickte der alte Browser den Text (chat.tsx bis v1.361).
ALT_ANHANG = (
    "Bitte vorkontieren\n\n[Angehängte Datei(en) im Workspace: /workspace/rechnung.pdf, "
    "/workspace/beleg 2.png. WICHTIG: Öffne und lies die Datei(en) ZUERST selbst mit deinem "
    "Read-Tool (PDFs und Bilder werden unterstützt; große Textdateien ggf. mit bash/grep) "
    "und antworte dann auf Basis des TATSÄCHLICHEN Inhalts — rate NICHT aus dem Dateinamen.]"
)
ALT_PLAN = (
    "[NUR PLANEN — NICHT AUSFÜHREN] Beschreibe kurz und konkret, welche Schritte du für die "
    "folgende Aufgabe gehen würdest (Tools, betroffene Dateien/Befehle, externe Aktionen, grober "
    "Aufwand/Risiken). Führe nichts aus, ändere nichts, sende nichts — gib NUR den Plan zurück."
    "\n\nAufgabe: " + ALT_ANHANG
)


class AuftragTests(unittest.TestCase):
    def test_agent_bekommt_lese_anweisung_mit_pfaden(self):
        anh = chat_auftrag.anhaenge_pruefen([{"path": "/workspace/rechnung.pdf", "size": 12}])
        auftrag = chat_auftrag.auftrag("Bitte vorkontieren", anh)
        self.assertTrue(auftrag.startswith("Bitte vorkontieren"))
        self.assertIn("/workspace/rechnung.pdf", auftrag)
        self.assertIn("Read-Tool", auftrag)

    def test_nur_datei_ohne_text(self):
        anh = chat_auftrag.anhaenge_pruefen([{"path": "/workspace/a.pdf"}])
        self.assertIn("/workspace/a.pdf", chat_auftrag.auftrag("", anh))

    def test_ohne_anhang_und_plan_bleibt_der_text_unveraendert(self):
        self.assertEqual(chat_auftrag.auftrag("Hallo", []), "Hallo")

    def test_plan_rahmt_den_auftrag(self):
        auftrag = chat_auftrag.auftrag("Server umziehen", [], plan=True)
        self.assertNotEqual(auftrag, "Server umziehen")
        self.assertTrue(auftrag.endswith("Server umziehen"))
        self.assertIn("NICHT AUSFÜHREN", auftrag)

    def test_pfade_ausserhalb_des_workspace_fallen_weg(self):
        anh = chat_auftrag.anhaenge_pruefen([
            {"path": "/workspace/../etc/passwd"},
            {"path": "/etc/shadow"},
            {"path": "/workspace/ok.txt"},
            {"path": "/workspace/zeile\numbruch.txt"},
            "kein-dict",
            {"path": "/workspace/ok.txt"},   # doppelt
        ])
        self.assertEqual([a["path"] for a in anh], ["/workspace/ok.txt"])
        self.assertEqual(anh[0]["filename"], "ok.txt")

    def test_kein_liste_ergibt_nichts(self):
        self.assertEqual(chat_auftrag.anhaenge_pruefen(None), [])
        self.assertEqual(chat_auftrag.anhaenge_pruefen("x"), [])


class NachrichtTests(unittest.TestCase):
    """Was aus einer eingehenden WebSocket-Nachricht wird — neu und alt."""

    def test_neuer_client(self):
        text, anh, plan = chat_auftrag.aus_nachricht({
            "text": "Bitte vorkontieren",
            "anhaenge": [{"path": "/workspace/rechnung.pdf", "filename": "rechnung.pdf"}],
            "plan": True,
        })
        self.assertEqual(text, "Bitte vorkontieren")
        self.assertEqual([a["path"] for a in anh], ["/workspace/rechnung.pdf"])
        self.assertTrue(plan)

    def test_alter_client_wird_toleriert(self):
        text, anh, plan = chat_auftrag.aus_nachricht({"text": ALT_PLAN})
        self.assertEqual(text, "Bitte vorkontieren")
        self.assertEqual([a["path"] for a in anh],
                         ["/workspace/rechnung.pdf", "/workspace/beleg 2.png"])
        self.assertTrue(plan)
        # Der Agent bekommt trotzdem die volle Anweisung.
        auftrag = chat_auftrag.auftrag(text, anh, plan)
        self.assertIn("Read-Tool", auftrag)
        self.assertIn("/workspace/beleg 2.png", auftrag)

    def test_gewoehnlicher_text_bleibt_unangetastet(self):
        self.assertEqual(chat_auftrag.aus_nachricht({"text": "  Hallo  "}), ("Hallo", [], False))

    def test_verlauf_hinweis_fuers_modell(self):
        """Custom-LLM laedt den Verlauf nach: dort muss der Anhang wieder stehen."""
        inhalt = chat_auftrag.mit_verlauf_hinweis(
            "Bitte vorkontieren", {"anhaenge": [{"path": "/workspace/rechnung.pdf"}]})
        self.assertIn("Bitte vorkontieren", inhalt)
        self.assertIn("/workspace/rechnung.pdf", inhalt)
        self.assertEqual(chat_auftrag.mit_verlauf_hinweis("Hallo", None), "Hallo")


class AltzeilenTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        fd, self._pfad = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{self._pfad}")
        async with self.engine.begin() as conn:
            await conn.run_sync(ChatMessage.metadata.create_all,
                                tables=[ChatMessage.__table__, ChatSession.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()
        os.unlink(self._pfad)

    async def _anlegen(self, sid, inhalt, titel):
        async with self.Session() as db:
            db.add(ChatMessage(agent_id=AGENT, session_id=sid, message_id=f"m-{sid}",
                               role="user", content=inhalt))
            db.add(ChatSession(agent_id=AGENT, session_id=sid, title=titel))
            await db.commit()

    async def test_altzeile_wird_bereinigt_und_auto_titel_neu_abgeleitet(self):
        await self._anlegen("s1", ALT_ANHANG, derive_title(ALT_ANHANG))
        await self._anlegen("s2", ALT_PLAN, "Mein eigener Titel")
        await self._anlegen("s3", "Ganz normale Frage", "Ganz normale Frage")

        async with self.Session() as db:
            zeilen, titel = await chat_auftrag.altzeilen_bereinigen(db)
        self.assertEqual(zeilen, 2)
        self.assertEqual(titel, 1)

        async with self.Session() as db:
            m1 = await db.scalar(select(ChatMessage).where(ChatMessage.session_id == "s1"))
            m2 = await db.scalar(select(ChatMessage).where(ChatMessage.session_id == "s2"))
            m3 = await db.scalar(select(ChatMessage).where(ChatMessage.session_id == "s3"))
            t1 = await db.scalar(select(ChatSession.title).where(ChatSession.session_id == "s1"))
            t2 = await db.scalar(select(ChatSession.title).where(ChatSession.session_id == "s2"))
        self.assertEqual(m1.content, "Bitte vorkontieren")
        self.assertEqual([a["path"] for a in m1.meta["anhaenge"]],
                         ["/workspace/rechnung.pdf", "/workspace/beleg 2.png"])
        self.assertEqual(m2.content, "Bitte vorkontieren")
        self.assertTrue(m2.meta["plan"])
        self.assertEqual(m3.content, "Ganz normale Frage")
        # Titel = Nutzertext (derive_title nimmt nur die Anrede „Bitte" weg).
        self.assertEqual(t1, derive_title("Bitte vorkontieren"))
        self.assertNotIn("Angehängte", t1)
        self.assertEqual(t2, "Mein eigener Titel")   # selbst vergeben: bleibt

    async def test_titel_aus_dateiname_wenn_kein_text(self):
        """Nur eine Datei geschickt: der Titel nennt die Datei statt leer zu bleiben."""
        from app.core.chat_history import ensure_title
        async with self.Session() as db:
            db.add(ChatMessage(agent_id=AGENT, session_id="s9", message_id="m9", role="user",
                               content="", meta={"anhaenge": [{"path": "/workspace/angebot.pdf",
                                                               "filename": "angebot.pdf"}]}))
            await db.commit()
            self.assertEqual(await ensure_title(db, AGENT, "s9"), "Angebot.pdf")

    async def test_zweiter_lauf_aendert_nichts(self):
        await self._anlegen("s1", ALT_ANHANG, derive_title(ALT_ANHANG))
        async with self.Session() as db:
            await chat_auftrag.altzeilen_bereinigen(db)
        async with self.Session() as db:
            self.assertEqual(await chat_auftrag.altzeilen_bereinigen(db), (0, 0))


class VerlaufFuersModellTests(unittest.IsolatedAsyncioTestCase):
    """Der Verlauf-Endpunkt: Mensch sieht den Nutzertext, der Agent (Custom-LLM
    laedt seinen Verlauf so nach) bekommt den Anhang-Vermerk dazu."""

    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(ChatMessage.metadata.create_all, tables=[ChatMessage.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.Session() as db:
            db.add(ChatMessage(agent_id=AGENT, session_id="s1", message_id="m1", role="user",
                               content="Bitte vorkontieren",
                               meta={"anhaenge": [{"path": "/workspace/rechnung.pdf"}]}))
            await db.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _verlauf(self, als_agent: bool) -> str:
        from unittest.mock import AsyncMock, patch
        from app.api import agents as agents_api
        with patch.object(agents_api, "_check_owner_or_self", AsyncMock()), \
                patch("app.dependencies.is_agent_principal", return_value=als_agent):
            async with self.Session() as db:
                antwort = await agents_api.get_chat_history(
                    AGENT, session_id="s1", limit=50, before_id=None, user=object(), db=db)
        return antwort["messages"][0]["content"]

    async def test_mensch_sieht_nur_seinen_text(self):
        self.assertEqual(await self._verlauf(False), "Bitte vorkontieren")

    async def test_agent_bekommt_den_anhang_vermerk(self):
        inhalt = await self._verlauf(True)
        self.assertTrue(inhalt.startswith("Bitte vorkontieren"))
        self.assertIn("/workspace/rechnung.pdf", inhalt)


if __name__ == "__main__":
    unittest.main()

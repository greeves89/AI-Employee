"""Von Hand angelegtes Wissen muss fuer die Agenten auffindbar sein.

Die Agentensuche (``brain_search`` → ``/brain/agent/search``) durchsucht semantisch
nur Eintraege MIT Embedding — und sobald es davon auch nur einen gibt, kommt der
Stichwort-Ausweichweg nicht mehr zum Zug. Zwei Luecken machten deshalb alles, was
ein Mensch unter „Wissen" eintrug, fuer seine Agenten unsichtbar:

1. ``POST``/``PUT /knowledge/entries`` betteten nicht ein, sie verliessen sich auf
   den Nachtrags-Job.
2. Der Nachtrags-Job startete nur, wenn der LOKALE Embedding-Dienst lief. Anlagen
   ohne ihn (der Raspberry Pi) betteten Suchanfragen ueber die Cloud ein, holten
   die Eintraege aber nie nach.

Auf einer Anlage fand so die Suche nach „Herbstkarte" den Eintrag „Herbstkarte"
nicht; der Agent schrieb „Nichts Spezifisches zur Herbstkarte" und erfand Inhalte.
"""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.api import knowledge as knowledge_api
from app.models.knowledge import KnowledgeEntry
from app.models.user import UserRole
from app.services import embedding_backfill
from app.services.embedding_service import EmbeddingService
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

NUTZER = SimpleNamespace(id="nutzer-a", role=UserRole.MEMBER)


class _MitDatenbank(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(
                KnowledgeEntry.metadata.create_all, tables=[KnowledgeEntry.__table__],
            )
        self.Session = async_sessionmaker(self.engine)

    async def asyncTearDown(self):
        await self.engine.dispose()


class AnlegenBettetEinTests(_MitDatenbank):
    async def test_neuer_eintrag_wird_eingebettet(self):
        with patch("app.core.knowledge_write.embed_and_link", new=AsyncMock(return_value=True)) as einbetten:
            async with self.Session() as db:
                antwort = await knowledge_api.create_entry(
                    knowledge_api.KnowledgeCreate(title="Herbstkarte", content="Kürbis-Latte, Apfelkuchen, Chai"),
                    user=NUTZER, db=db,
                )
        einbetten.assert_awaited_once()
        _db, eintrag_id, besitzer, text = einbetten.await_args.args
        self.assertEqual(eintrag_id, antwort["id"])
        self.assertEqual(besitzer, "nutzer-a")
        self.assertEqual(text, "Herbstkarte: Kürbis-Latte, Apfelkuchen, Chai")

    async def test_antwort_bleibt_vollstaendig_wenn_einbetten_scheitert(self):
        # embed_and_link schluckt Fehler selbst und meldet False — das Anlegen gelingt trotzdem.
        with patch("app.core.knowledge_write.embed_and_link", new=AsyncMock(return_value=False)):
            async with self.Session() as db:
                antwort = await knowledge_api.create_entry(
                    knowledge_api.KnowledgeCreate(title="Öffnungszeiten", content="Neu ab Oktober"),
                    user=NUTZER, db=db,
                )
        self.assertEqual(antwort["title"], "Öffnungszeiten")
        self.assertEqual(antwort["content"], "Neu ab Oktober")


class AendernBettetNeuEinTests(_MitDatenbank):
    async def _anlegen(self):
        with patch("app.core.knowledge_write.embed_and_link", new=AsyncMock(return_value=True)):
            async with self.Session() as db:
                return (await knowledge_api.create_entry(
                    knowledge_api.KnowledgeCreate(title="Sortiment", content="Kaffee und Kuchen"),
                    user=NUTZER, db=db,
                ))["id"]

    async def _aendern(self, eintrag_id, **felder):
        with patch("app.core.knowledge_write.embed_and_link", new=AsyncMock(return_value=True)) as einbetten:
            async with self.Session() as db:
                await knowledge_api.update_entry(
                    eintrag_id, knowledge_api.KnowledgeUpdate(**felder), user=NUTZER, db=db,
                )
        return einbetten

    async def test_neuer_inhalt_wird_eingebettet(self):
        einbetten = await self._aendern(await self._anlegen(), content="Kaffee, Kuchen, Herbstkarte")
        einbetten.assert_awaited_once()
        self.assertEqual(einbetten.await_args.args[3], "Sortiment: Kaffee, Kuchen, Herbstkarte")

    async def test_neuer_titel_wird_eingebettet(self):
        einbetten = await self._aendern(await self._anlegen(), title="Angebot")
        self.assertEqual(einbetten.await_args.args[3], "Angebot: Kaffee und Kuchen")

    async def test_nur_schlagworte_aendern_bettet_nicht_ein(self):
        einbetten = await self._aendern(await self._anlegen(), tags=["cafe"])
        einbetten.assert_not_awaited()


class AnbieterVerfuegbarTests(unittest.IsolatedAsyncioTestCase):
    """MC/DC über (lokal erreichbar, Cloud-Schlüssel gesetzt)."""

    async def _verfuegbar(self, lokal, schluessel):
        svc = EmbeddingService()
        with patch.object(svc, "_check_local_available", new=AsyncMock(return_value=lokal)), \
             patch("app.services.embedding_service.settings", SimpleNamespace(openai_api_key=schluessel)):
            return await svc.available()

    async def test_nur_lokal(self):
        self.assertTrue(await self._verfuegbar(True, ""))

    async def test_nur_cloud(self):
        self.assertTrue(await self._verfuegbar(False, "sk-test"))

    async def test_keiner(self):
        self.assertFalse(await self._verfuegbar(False, ""))


class NachtragsJobStartetMitCloudTests(unittest.IsolatedAsyncioTestCase):
    async def test_ohne_lokalen_dienst_wird_nachgeholt(self):
        svc = SimpleNamespace(available=AsyncMock(return_value=True))

        class _Ende(BaseException):  # die Schleife faengt Exception ab
            pass

        schlafen = AsyncMock(side_effect=[None, _Ende()])  # Startverzoegerung, dann nach dem ersten Durchlauf
        wissen = AsyncMock(return_value=0)

        class _Sitzung:
            async def __aenter__(self):
                return object()

            async def __aexit__(self, *a):
                return False

        with patch.object(embedding_backfill, "get_embedding_service", return_value=svc), \
             patch.object(embedding_backfill.asyncio, "sleep", schlafen), \
             patch.object(embedding_backfill, "resilient_session", lambda **kw: _Sitzung()), \
             patch.object(embedding_backfill, "backfill_memory_embeddings", AsyncMock(return_value=0)), \
             patch.object(embedding_backfill, "backfill_knowledge_embeddings", wissen), \
             patch.object(embedding_backfill, "backfill_skill_embeddings", AsyncMock(return_value=0)):
            with self.assertRaises(_Ende):
                await embedding_backfill.run_backfill_loop(None)

        wissen.assert_awaited_once()


if __name__ == "__main__":
    asyncio.run(unittest.main())

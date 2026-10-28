"""Der Agenten-Zugang zum Second Brain holt seinen Umfang NUR aus dem Token.

#727 gibt dem Agenten die Hybrid-Suche (vorher: Substring-Zaehler ueber die
gemounteten Dateien). Damit wandert eine Entscheidung vom Dateisystem in den
Orchestrator: Welche Vaults darf dieser Agent sehen? Das Dateisystem hat sie
bisher beantwortet — gemountet war nur, was zugewiesen war. Der neue Endpunkt
liest aus DERSELBEN Datenbank wie die Admin-Oberflaeche und muss die Kappung
selbst durchsetzen.

Deshalb pruefen diese Tests nicht „kommen Treffer zurueck", sondern die drei
Aussagen, bei denen ein Fehler still waere:

* Der Umfang kommt aus dem Token, nicht aus dem ``label`` der Anfrage.
* Ein fremdes Label ergibt **404**, nicht eine leere Trefferliste — ein
  Tippfehler darf nicht wie „nichts gefunden" aussehen (Spec 3.1).
* Ein nur lesend eingebundener Vault lehnt das Schreiben mit **403** ab.

Dazu die Gesamtaussage ueber den Suchmodus: „hybrid" gilt nur, wenn JEDER
durchsuchte Vault aus dem Index geantwortet hat. Sonst verdeckt ein gesunder
Vault einen driftenden — genau die Lage, vor der Abschnitt 9 der Spec warnt.
"""
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import brains as api
from app.models.agent import Agent
from app.models.second_brain import SecondBrain

AGENT_ID = "a1"
AUTH = {"agent_id": AGENT_ID}


class AgentenEndpunktScopingTests(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(SecondBrain.metadata.create_all,
                                tables=[SecondBrain.__table__])
            await conn.run_sync(Agent.metadata.create_all,
                                tables=[Agent.__table__])
        self.db = async_sessionmaker(self.engine, expire_on_commit=False)()
        # Drei Vaults im Katalog, der Agent hat zwei — "finanzen" ist der
        # fremde. "hr" ist zusaetzlich nur lesend eingebunden.
        for slug, modus in (("it", "rw"), ("hr", "rw"), ("finanzen", "rw")):
            self.db.add(SecondBrain(label=f"brain-{slug}", name=slug.upper(), slug=slug,
                                    host_path=f"/mnt/brains/{slug}",
                                    container_path=f"/mnt/brains/{slug}",
                                    default_mode=modus, is_active=True))
        self.db.add(Agent(id=AGENT_ID, name="Agent", config={
            "mounts": ["brain-it", "brain-hr"],
            "mount_modes": {"brain-hr": "ro"},
        }))
        await self.db.commit()

        async def suche(db, label, host_path, q, limit):
            # Ein Vault antwortet aus dem Index, der andere faellt auf grep
            # zurueck — so ist die Gesamtaussage ueberhaupt pruefbar.
            modus = "hybrid" if label == "brain-it" else "grep"
            return modus, [{"path": f"{label}/a.md", "score": 1.0, "snippet": "x"}]

        self.p_suche = patch.object(api.vault_search, "hybrid_search_with_mode", suche)
        self.p_suche.start()

    async def asyncTearDown(self):
        self.p_suche.stop()
        await self.db.close()
        await self.engine.dispose()

    # --- Vorbedingung: ohne sie beweist kein Negativbefund unten etwas ------

    async def test_vorbedingung_der_fremde_vault_ist_wirklich_da(self):
        """Sonst ist „sieht ihn nicht" kein Scoping-Beweis, sondern ein leerer
        Katalog."""
        katalog = await api.get_effective_catalog(self.db) if hasattr(api, "get_effective_catalog") else None
        if katalog is None:
            from app.core.mounts import get_effective_catalog
            katalog = await get_effective_catalog(self.db)
        self.assertIn("brain-finanzen", katalog)
        self.assertIn("brain-it", katalog)

    # --- Umfang kommt aus dem Token -----------------------------------------

    async def test_sucht_nur_in_zugewiesenen_vaults(self):
        res = await api.agent_brain_search(q="x", limit=10, label=None, auth=AUTH, db=self.db)
        self.assertEqual(sorted(b["brain"] for b in res["brains"]), ["hr", "it"])

    async def test_label_verkleinert_den_umfang(self):
        res = await api.agent_brain_search(q="x", limit=10, label="it", auth=AUTH, db=self.db)
        self.assertEqual([b["brain"] for b in res["brains"]], ["it"])

    async def test_fremdes_label_ist_404_und_nicht_leer(self):
        with self.assertRaises(HTTPException) as ctx:
            await api.agent_brain_search(q="x", limit=10, label="finanzen", auth=AUTH, db=self.db)
        self.assertEqual(ctx.exception.status_code, 404)
        # Der fremde Slug darf in der Fehlermeldung NICHT als verfuegbar
        # erscheinen — die Meldung nennt nur die eigenen.
        self.assertNotIn("finanzen", str(ctx.exception.detail).split("Verfuegbar:")[-1])

    async def test_unbekanntes_label_ist_404(self):
        with self.assertRaises(HTTPException) as ctx:
            await api.agent_brain_search(q="x", limit=10, label="gibtsnicht", auth=AUTH, db=self.db)
        self.assertEqual(ctx.exception.status_code, 404)

    # --- Gesamtaussage ueber den Modus --------------------------------------

    async def test_ein_grep_vault_macht_die_gesamtaussage_grep(self):
        res = await api.agent_brain_search(q="x", limit=10, label=None, auth=AUTH, db=self.db)
        self.assertEqual(res["mode"], "grep")
        self.assertEqual({b["brain"]: b["mode"] for b in res["brains"]},
                         {"it": "hybrid", "hr": "grep"})

    async def test_nur_index_vaults_ergeben_hybrid(self):
        res = await api.agent_brain_search(q="x", limit=10, label="it", auth=AUTH, db=self.db)
        self.assertEqual(res["mode"], "hybrid")

    # --- Schreibweg ----------------------------------------------------------

    async def test_schreiben_in_fremden_vault_ist_404(self):
        with self.assertRaises(HTTPException) as ctx:
            await api.agent_brain_write(
                body=api.AgentBrainWrite(path="finanzen/Zahlen/2026.md", content="x"),
                auth=AUTH, db=self.db)
        self.assertEqual(ctx.exception.status_code, 404)

    async def test_schreiben_in_nur_lesenden_vault_ist_403(self):
        with self.assertRaises(HTTPException) as ctx:
            await api.agent_brain_write(
                body=api.AgentBrainWrite(path="hr/Urlaub/antrag.md", content="x"),
                auth=AUTH, db=self.db)
        self.assertEqual(ctx.exception.status_code, 403)

    async def test_pfad_ohne_slug_ist_422(self):
        with self.assertRaises(HTTPException) as ctx:
            await api.agent_brain_write(
                body=api.AgentBrainWrite(path="einzeldatei.md", content="x"),
                auth=AUTH, db=self.db)
        self.assertEqual(ctx.exception.status_code, 422)

    async def test_schreiben_indexiert_sofort(self):
        """Der Agent war bis #727 der einzige Schreibweg ohne Index."""
        gesehen = {}

        def schreiben(host_path, rest, content):
            gesehen["write"] = (host_path, rest)
            return {"created": True, "bytes": len(content)}

        async def indexieren(db, label, host_path, rest):
            gesehen["index"] = (label, rest)
            return 3

        with patch.object(api.vault, "write_file", schreiben), \
             patch.object(api.vault_indexer, "index_file", indexieren):
            res = await api.agent_brain_write(
                body=api.AgentBrainWrite(path="it/Drucker/x17137.md", content="# x17137"),
                auth=AUTH, db=self.db)

        self.assertEqual(gesehen["write"], ("/mnt/brains/it", "Drucker/x17137.md"))
        self.assertEqual(gesehen["index"], ("brain-it", "Drucker/x17137.md"))
        self.assertEqual(res["indexed_chunks"], 3)
        self.assertEqual(res["brain"], "it")

    async def test_geschrieben_bleibt_geschrieben_wenn_der_index_faellt(self):
        """Ein Indexfehler darf den Schreibzugriff nicht verwerfen — der
        5-Minuten-Durchlauf holt ihn nach."""
        async def kaputt(db, label, host_path, rest):
            raise RuntimeError("Embedding-Dienst weg")

        with patch.object(api.vault, "write_file",
                          lambda *a: {"created": True, "bytes": 7}), \
             patch.object(api.vault_indexer, "index_file", kaputt):
            res = await api.agent_brain_write(
                body=api.AgentBrainWrite(path="it/Drucker/x.md", content="x"),
                auth=AUTH, db=self.db)
        self.assertTrue(res["ok"])
        self.assertEqual(res["indexed_chunks"], 0)


if __name__ == "__main__":
    unittest.main()

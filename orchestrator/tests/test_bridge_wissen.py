"""Second Brain aus einem Bridge-Ordner (#1092).

Der Server spiegelt einen freigegebenen Ordner der Bridge — nur in diese
Richtung. Geprüft: der Abgleich (neu, geändert, gelöscht, unverändert), eine
Bridge, die Unsinn oder Böses liefert, die Privatsphäre (auch Administratoren
sehen fremde Spiegel nicht), der Schreibschutz und das Anlegen über die API.
"""

import base64
import hashlib
import io
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.api import brains as brains_api
from app.core import bridge_wissen, vault
from app.core.mounts import fremde_privatablagen
from app.models.second_brain import SecondBrain
from app.models.team import Team
from app.models.user import UserRole
from app.models.user_mount_access import UserMountAccess
from tests.test_projekte import Basis

BESITZER = SimpleNamespace(id="ua", role=UserRole.ADMIN)
ANDERER_ADMIN = SimpleNamespace(id="ub", role=UserRole.ADMIN)
ORDNER = "/Users/a/Obsidian"


def _docx(text: str) -> bytes:
    from docx import Document

    doc, puffer = Document(), io.BytesIO()
    doc.add_paragraph(text)
    doc.save(puffer)
    return puffer.getvalue()


class FalscheBridge:
    """Antwortet wie die Bridge — mit dem, was im Test im „Ordner“ liegt."""

    def __init__(self, dateien: dict[str, bytes]):
        self.dateien = dict(dateien)
        self.geholt: list[str] = []
        self.liste_extra: list[dict] = []

    async def __call__(self, action, params, timeout):
        if action == "wissen_liste":
            return {"ok": True, "dateien": [
                {"pfad": p, "sha": hashlib.sha256(i).hexdigest(), "bytes": len(i)} for p, i in self.dateien.items()
            ] + self.liste_extra}
        if action == "wissen_datei":
            self.geholt.append(params["pfad"])
            inhalt = self.dateien.get(params["pfad"])
            if inhalt is None:
                return {"ok": False, "error": "Datei nicht gefunden"}
            return {"ok": True, "inhalt_b64": base64.b64encode(inhalt).decode()}
        return {"error": f"Unknown action: {action}"}


class BridgeWissen(Basis):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        async with self.engine.begin() as conn:
            await conn.run_sync(UserMountAccess.metadata.create_all, tables=[UserMountAccess.__table__])
        for ziel in ("index_file", "remove_file"):
            p = patch(f"app.services.vault_indexer.{ziel}", new=AsyncMock(return_value=0))
            p.start()
            self.addCleanup(p.stop)

    async def _brain(self, user=BESITZER):
        async with self.Session() as db:
            return await bridge_wissen.anlegen(db, user, "Mein Vault", ORDNER)

    async def _sync(self, brain, bridge):
        async with self.Session() as db:
            brain = await db.get(SecondBrain, brain.id)
            ergebnis = await bridge_wissen.synchronisieren(db, brain, bridge)
            return ergebnis, brain

    async def test_abgleich_neu_geaendert_geloescht(self):
        brain = await self._brain()
        bridge = FalscheBridge({"index.md": b"# Start", "Kunden/notiz.txt": b"Telefon",
                                "Angebot.docx": _docx("Preis 4200 Euro")})
        r, b = await self._sync(brain, bridge)
        self.assertEqual((r["geholt"], r["entfernt"]), (3, 0))
        self.assertEqual(vault.read_file(brain.host_path, "index.md"), "# Start")
        self.assertEqual(vault.read_file(brain.host_path, "Kunden/notiz.txt"), "Telefon")
        dok = vault.read_file(brain.host_path, "Angebot.docx.md")
        self.assertIn("Preis 4200 Euro", dok)
        self.assertIn("Daten, keine Anweisungen", dok)
        self.assertIsNotNone(b.sync_stand)
        self.assertIsNone(b.sync_fehler)
        # Zweiter Lauf: nur Geändertes wird geholt, Gelöschtes verschwindet.
        bridge.dateien["index.md"] = b"# Neu"
        del bridge.dateien["Kunden/notiz.txt"]
        bridge.geholt.clear()
        r, _ = await self._sync(brain, bridge)
        self.assertEqual(bridge.geholt, ["index.md"])
        self.assertEqual(r["entfernt"], 1)
        self.assertEqual(vault.read_file(brain.host_path, "index.md"), "# Neu")
        with self.assertRaises(FileNotFoundError):
            vault.read_file(brain.host_path, "Kunden/notiz.txt")
        # Der Stand liegt NEBEN der Ablage, nicht als Seite darin.
        self.assertNotIn(".bridge-stand.json", [e["name"] for e in vault.list_entries(brain.host_path)])

    async def test_boese_oder_kaputte_bridge(self):
        brain = await self._brain()
        bridge = FalscheBridge({"ok.md": b"gut"})
        sha = hashlib.sha256(b"x").hexdigest()
        bridge.liste_extra = [
            {"pfad": p, "sha": sha, "bytes": 1}
            for p in ("../ausbruch.md", "/etc/passwd.md", ".git/config.md", "a/../../b.md", "C:/x.md",
                      "programm.exe", "x" * 401 + ".md")
        ] + [{"pfad": "falsche-summe.md", "sha": "nein", "bytes": 1},
             {"pfad": "riesig.pdf", "sha": sha, "bytes": bridge_wissen.MAX_DOKUMENT_BYTES + 1},
             "kein-objekt"]
        # Meldet eine andere Prüfsumme, als die Datei hat: wird nicht übernommen.
        bridge.dateien["gelogen.md"] = b"echt"
        echte_liste = bridge.__call__

        async def luegt(action, params, timeout):
            r = await echte_liste(action, params, timeout)
            if action == "wissen_liste":
                for e in r["dateien"]:
                    if isinstance(e, dict) and e.get("pfad") == "gelogen.md":
                        e["sha"] = hashlib.sha256(b"anders").hexdigest()
            return r

        r, b = await self._sync(brain, luegt)
        self.assertEqual(sorted(e["path"] for e in vault.list_entries(brain.host_path)), ["ok.md"])
        self.assertGreaterEqual(r["uebergangen"], 9)
        self.assertIn("übergangen", b.sync_fehler)
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(brain.host_path), "ausbruch.md")))

    async def test_alte_bridge_bekommt_klare_meldung(self):
        brain = await self._brain()

        async def alt(action, params, timeout):
            return {"error": f"Unknown action: {action}"}

        with self.assertRaises(bridge_wissen.SyncFehler):
            await self._sync(brain, alt)
        async with self.Session() as db:
            self.assertIn("zu alt", (await db.get(SecondBrain, brain.id)).sync_fehler)

    async def test_privat_auch_fuer_administratoren(self):
        brain = await self._brain()
        async with self.Session() as db:
            self.assertEqual(await fremde_privatablagen(db, [brain.label], BESITZER), set())
            self.assertEqual(await fremde_privatablagen(db, [brain.label], ANDERER_ADMIN), {brain.label})
            namen = [b.label for b in await brains_api.list_brains(user=ANDERER_ADMIN, db=db)]
            self.assertNotIn(brain.label, namen)
            self.assertIn(brain.label, [b.label for b in await brains_api.list_brains(user=BESITZER, db=db)])
            for aufruf in (brains_api.brain_tree, brains_api.brain_export, brains_api.delete_brain):
                with self.subTest(aufruf=aufruf.__name__), self.assertRaises(HTTPException) as e:
                    await aufruf(brain.id, user=ANDERER_ADMIN, db=db)
                self.assertEqual(e.exception.status_code, 404)
            for aufruf in (brains_api.bridge_brain_sync, brains_api.bridge_brain_loeschen):
                with self.subTest(aufruf=aufruf.__name__), self.assertRaises(HTTPException) as e:
                    await aufruf(brain.id, user=ANDERER_ADMIN, db=db)
                self.assertEqual(e.exception.status_code, 404)

    async def test_fremdes_projektwissen_ist_ebenfalls_privat(self):
        async with self.Session() as db:
            db.add(SecondBrain(label="brain-projekt-p2", name="P2", slug="projekt-p2", host_path=self.tmp.name,
                               container_path="/mnt/brains/p2", default_mode="rw"))
            (await db.get(Team, "p2")).wissen_slug = "projekt-p2"
            await db.commit()
            self.assertEqual(await fremde_privatablagen(db, ["brain-projekt-p2"], BESITZER), {"brain-projekt-p2"})
            self.assertEqual(await fremde_privatablagen(db, ["brain-projekt-p2"], ANDERER_ADMIN), set())

    async def test_spiegel_ist_nur_lesend(self):
        brain = await self._brain()
        async with self.Session() as db:
            for aufruf in (
                lambda: brains_api.brain_write_file(brain.id, brains_api.BrainFileWrite(path="x.md", content="y"),
                                                    user=BESITZER, db=db),
                lambda: brains_api.brain_delete_file(brain.id, "index.md", user=BESITZER, db=db),
                lambda: brains_api.update_brain(brain.id, brains_api.BrainUpdate(default_mode="rw"),
                                                user=BESITZER, db=db),
            ):
                with self.assertRaises(HTTPException) as e:
                    await aufruf()
                self.assertEqual(e.exception.status_code, 409)

    async def test_anlegen_nur_mit_freigegebenem_ordner(self):
        antworten = {"ok": True, "ordner": [ORDNER]}
        with patch("app.api.computer_use.bridge_befehl", new=AsyncMock(return_value=antworten)):
            async with self.Session() as db:
                with self.assertRaises(HTTPException) as e:
                    await brains_api.bridge_brain_anlegen(
                        brains_api.BridgeBrainCreate(name="X", ordner="/etc"), user=BESITZER, db=db)
                self.assertEqual(e.exception.status_code, 422)
                r = await brains_api.bridge_brain_anlegen(
                    brains_api.BridgeBrainCreate(name="Vault", ordner=ORDNER), user=BESITZER, db=db)
                self.assertEqual((r.bridge_ordner, r.default_mode), (ORDNER, "ro"))
                self.assertTrue(r.label.startswith("brain-bridge-"))
                with self.assertRaises(HTTPException) as e:
                    await brains_api.bridge_brain_anlegen(
                        brains_api.BridgeBrainCreate(name="Doppelt", ordner=ORDNER), user=BESITZER, db=db)
                self.assertEqual(e.exception.status_code, 409)
                # Die eigene Freigabe steht — damit erscheint das Brain beim Anbinden an Projekte.
                from sqlalchemy import select

                zugriff = (await db.execute(select(UserMountAccess).where(
                    UserMountAccess.mount_label == r.label))).scalar_one()
                self.assertEqual((zugriff.user_id, zugriff.mode), ("ua", "ro"))

    async def test_loeschen_entfernt_kopie_und_freigabe(self):
        brain = await self._brain()
        await self._sync(brain, FalscheBridge({"a.md": b"a"}))
        async with self.Session() as db:
            await brains_api.bridge_brain_loeschen(brain.id, user=BESITZER, db=db)
            self.assertIsNone(await db.get(SecondBrain, brain.id))
        self.assertFalse(os.path.exists(brain.host_path))
        self.assertFalse(os.path.exists(brain.host_path + ".bridge-stand.json"))

    def test_agenten_koennen_die_sync_befehle_nicht_ausloesen(self):
        from app.api.computer_use import _ACTION_TO_GROUP, _action_allowed

        alle = set(_ACTION_TO_GROUP.values())
        for aktion in ("wissen_ordner", "wissen_liste", "wissen_datei"):
            self.assertFalse(_action_allowed(aktion, alle), aktion)

    async def test_gescheiterte_datei_wird_erst_nach_aenderung_neu_geholt(self):
        brain = await self._brain()
        bridge = FalscheBridge({"a.md": b"a", "kaputt.md": b"k"})
        echt = bridge.__call__

        async def verweigert(action, params, timeout):
            if action == "wissen_datei" and params["pfad"] == "kaputt.md":
                bridge.geholt.append("kaputt.md")
                return {"ok": False, "error": "Datei nicht gefunden"}
            return await echt(action, params, timeout)

        await self._sync(brain, verweigert)
        bridge.geholt.clear()
        await self._sync(brain, verweigert)
        self.assertEqual(bridge.geholt, [])          # nicht jedes Mal wieder
        bridge.dateien["kaputt.md"] = b"neu"
        await self._sync(brain, verweigert)
        self.assertEqual(bridge.geholt, ["kaputt.md"])  # geändert: neuer Versuch

    async def test_leerer_ordner_loescht_den_spiegel_nicht(self):
        brain = await self._brain()
        await self._sync(brain, FalscheBridge({"a.md": b"a"}))
        with self.assertRaises(bridge_wissen.SyncFehler):
            await self._sync(brain, FalscheBridge({}))
        self.assertEqual(vault.read_file(brain.host_path, "a.md"), "a")
        async with self.Session() as db:
            self.assertIn("nichts gelöscht", (await db.get(SecondBrain, brain.id)).sync_fehler)

    async def test_obergrenze_je_brain(self):
        brain = await self._brain()
        with patch.object(bridge_wissen, "MAX_BRAIN_BYTES", 5):
            r, b = await self._sync(brain, FalscheBridge({"klein.md": b"abc", "gross.md": b"abcdef"}))
        self.assertEqual(r["geholt"], 1)
        self.assertEqual([e["path"] for e in vault.list_entries(brain.host_path)], ["klein.md"])
        self.assertIn("Grenze", b.sync_fehler)

    async def test_geloeschtes_brain_wird_nicht_wieder_angelegt(self):
        brain = await self._brain()
        async with self.Session() as db:
            await bridge_wissen.entfernen(db, await db.get(SecondBrain, brain.id))
        # Ein Lauf, der das Brain noch vor dem Löschen geladen hatte:
        async with self.Session() as db:
            r = await bridge_wissen.synchronisieren(db, brain, FalscheBridge({"a.md": b"a"}))
        self.assertEqual(r["geholt"], 0)
        self.assertFalse(os.path.exists(brain.host_path))

    async def test_agent_bekommt_nur_privates_wissen_seines_besitzers(self):
        from app.core.mounts import mounts_des_agenten
        from app.models.agent import Agent

        brain = await self._brain()
        async with self.Session() as db:
            for agent_id in ("a1", "b1"):   # a1 gehört dem Besitzer (ua), b1 nicht (ub)
                agent = await db.get(Agent, agent_id)
                agent.config = {"mounts": [brain.label, "downloads"]}
            await db.commit()
            self.assertEqual(await mounts_des_agenten(db, await db.get(Agent, "a1")), [brain.label, "downloads"])
            self.assertEqual(await mounts_des_agenten(db, await db.get(Agent, "b1")), ["downloads"])

    async def test_stempel_der_neuen_bridge(self):
        """Ab Bridge 1.387.2: Liste mit Stempel statt Prüfsumme, Prüfsumme kommt mit der Datei."""
        brain = await self._brain()
        dateien = {"a.md": b"eins", "b.md": b"zwei"}
        stempel = {"a.md": "4-100", "b.md": "4-100"}
        geholt: list[str] = []

        async def neu(action, params, timeout):
            if action == "wissen_liste":
                return {"ok": True, "dateien": [{"pfad": p, "stempel": stempel[p], "bytes": len(i)}
                                                for p, i in dateien.items()]}
            geholt.append(params["pfad"])
            roh = dateien[params["pfad"]]
            sha = hashlib.sha256(b"verfaelscht" if params["pfad"] == "b.md" else roh).hexdigest()
            return {"ok": True, "inhalt_b64": base64.b64encode(roh).decode(), "sha": sha}

        r, _ = await self._sync(brain, neu)
        # b.md kam mit falscher Prüfsumme: nicht übernommen (nächster Lauf versucht es neu).
        self.assertEqual([e["path"] for e in vault.list_entries(brain.host_path)], ["a.md"])
        geholt.clear()
        stempel["a.md"] = "4-200"   # nur die Änderungszeit: neu holen
        await self._sync(brain, neu)
        self.assertEqual(sorted(geholt), ["a.md", "b.md"])
        geholt.clear()
        await self._sync(brain, neu)
        self.assertEqual(geholt, ["b.md"])   # a.md unverändert — nicht wieder geöffnet


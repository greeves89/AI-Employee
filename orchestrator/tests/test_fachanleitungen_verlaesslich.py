"""Zugewiesene Fach-Skills werden bei passenden Aufträgen VERLÄSSLICH angewendet.

Befund aus der Abnahme von v1.362.1 (Claude-Code-Agenten aus Vorlagen):

* Buchhaltung (#894): Der Skill ``buchhaltung-vorkontieren`` war zugewiesen und
  enthielt die Brutto-Regel samt Prüfschritt — der Agent schrieb den
  DATEV-Buchungsstapel trotzdem mit Nettobetrag bei BU 9. Er hatte den Skill nie
  geladen.
* Angebot (#893): Der Agent erzeugte zuerst ein PDF mit „[Ihr Firmenname]“ und
  fragte danach; ``angebot-kalkulieren`` wurde nicht benutzt.

Warum der Skill nie ankam (nachgemessen, nicht vermutet):

1. Im Chat der CLI-Laufzeiten (Claude Code, Codex) erschien die Liste der
   zugewiesenen Skills überhaupt nicht — nur Dateien unter
   ``/workspace/.claude/skills`` und eine Stichwortsuche im Marktplatz.
2. In Aufträgen stand die Liste da, aber ohne ID: ``/skills/agent/available``
   lieferte keine ``id``, der Agent las ``skill_id=?`` und konnte nichts laden.
3. Die Beschreibungen nannten die Auslöser nicht („DATEV“, „Buchungsstapel“,
   „Kostenvoranschlag“ …) — das Modell konnte gar nicht erkennen, dass ein
   DATEV-Export zu „Belege vorkontieren“ gehört.

Jetzt: Jeder mitgelieferte Skill nennt seine Auslöser, der Server ordnet einen
Auftrag den zugewiesenen Skills zu und liefert passende Anleitungen vollständig
mit — für alle Laufzeiten über denselben Endpunkt.
"""

import asyncio
import unittest
from types import SimpleNamespace

from app.core import fachanleitungen, mitgelieferte_skills


def _skill(sid: int, name: str) -> dict:
    s = next(s for s in mitgelieferte_skills.laden() if s["name"] == name)
    return {"id": sid, "name": s["name"], "description": s["description"], "content": s["content"]}


def _namen(ergebnis: dict) -> list[str]:
    return [a["name"] for a in ergebnis["anleitungen"]]


class JederMitgelieferteSkillNenntAusloeserTests(unittest.TestCase):
    def test_jede_beschreibung_hat_mindestens_drei_ausloeser(self):
        ohne = {
            s["name"]: fachanleitungen.ausloeser(s["description"])
            for s in mitgelieferte_skills.laden()
            if len(fachanleitungen.ausloeser(s["description"])) < 3
        }
        self.assertEqual({}, ohne, "Skills ohne (genug) Auslöser in der Beschreibung")

    def test_jeder_skill_wird_von_seinen_eigenen_ausloesern_gefunden(self):
        skills = [_skill(i, s["name"]) for i, s in enumerate(mitgelieferte_skills.laden(), start=1)]
        for s in skills:
            for wort in fachanleitungen.ausloeser(s["description"]):
                with self.subTest(skill=s["name"], ausloeser=wort):
                    treffer = fachanleitungen.passende(skills, f"Kannst du mir bei {wort} helfen?")
                    self.assertIn(s["name"], [t["name"] for t in treffer])


class AuftragFindetSeineAnleitungTests(unittest.TestCase):
    def setUp(self):
        self.skills = [
            _skill(7, "buchhaltung-vorkontieren"),
            _skill(8, "angebot-kalkulieren"),
            {"id": 9, "name": "xlsx", "description": "Spreadsheet creation and editing.", "content": "x"},
        ]

    def test_datev_auftraege_landen_bei_der_buchhaltung(self):
        for auftrag in (
            "Erstelle mir einen DATEV-Buchungsstapel für diese Eingangsrechnung.",
            "Mach daraus eine CSV für den Steuerberater.",
            "Bitte die Belege vom August vorkontieren.",
            "Welcher BU-Schlüssel gehört zu 19 % Vorsteuer?",
        ):
            with self.subTest(auftrag=auftrag):
                self.assertEqual(["buchhaltung-vorkontieren"],
                                 _namen(fachanleitungen.fuer_agent(self.skills, auftrag)))

    def test_angebotsauftraege_landen_bei_der_kalkulation(self):
        for auftrag in (
            "Erstelle mir bitte ein Angebot: 40 m² Wandfläche weiß streichen.",
            "Ich brauche einen Kostenvoranschlag für die Badsanierung.",
            "Kalkulier mir den Nachtrag für die zusätzliche Wand.",
        ):
            with self.subTest(auftrag=auftrag):
                self.assertEqual(["angebot-kalkulieren"],
                                 _namen(fachanleitungen.fuer_agent(self.skills, auftrag)))

    def test_fremder_auftrag_laedt_nichts(self):
        self.assertEqual([], _namen(fachanleitungen.fuer_agent(self.skills, "Wie wird das Wetter morgen?")))

    def test_gross_klein_und_beugung_spielen_keine_rolle(self):
        self.assertEqual(["angebot-kalkulieren"],
                         _namen(fachanleitungen.fuer_agent(self.skills, "zwei KOSTENVORANSCHLÄGE bitte")))

    def test_wortanfang_zaehlt_nicht_die_wortmitte(self):
        # „Rechnungsbeleg“ endet zwar auf „beleg“, „Beleg“ als Auslöser greift
        # aber nur am Wortanfang — sonst fände jede zweite Nachricht einen Skill.
        self.assertNotIn("buchhaltung-vorkontieren",
                         [t["name"] for t in fachanleitungen.passende(self.skills, "Sonderbelegung des Saals")])

    def test_passende_anleitung_kommt_vollstaendig_mit_id_und_bewertung(self):
        ergebnis = fachanleitungen.fuer_agent(self.skills, "DATEV-Export für die Kanzlei bitte")
        text = ergebnis["anleitungen"][0]["text"]
        # Der Prüfschritt steht am Ende des Skills — kommt er mit, kam alles mit.
        self.assertIn("def kontrolle_buchungsstapel", text)
        self.assertIn("skill_rate(skill_id=7", text)

    def test_liste_nennt_jeden_zugewiesenen_skill_mit_ladbarer_id_und_ausloesern(self):
        liste = fachanleitungen.fuer_agent(self.skills, "")["prompt"]
        for sid, name in ((7, "buchhaltung-vorkontieren"), (8, "angebot-kalkulieren"), (9, "xlsx")):
            self.assertIn(f"skill_install(skill_id={sid})", liste)
            self.assertIn(name, liste)
        self.assertIn("Buchungsstapel", liste)
        self.assertIn("Kostenvoranschlag", liste)

    def test_ohne_zugewiesene_skills_kein_block(self):
        self.assertEqual({"prompt": "", "anleitungen": []}, fachanleitungen.fuer_agent([], "DATEV-Export"))

    def test_hoechstens_zwei_anleitungen_je_auftrag(self):
        skills = [_skill(i, s["name"]) for i, s in enumerate(mitgelieferte_skills.laden(), start=1)]
        auftrag = " ".join(w for s in skills for w in fachanleitungen.ausloeser(s["description"])[:1])
        self.assertLessEqual(len(fachanleitungen.fuer_agent(skills, auftrag)["anleitungen"]), 2)


class _Ergebnis:
    def __init__(self, werte):
        self._werte = werte

    def scalars(self):
        return self

    def all(self):
        return list(self._werte)

    def scalar_one_or_none(self):
        return self._werte[0] if self._werte else None


class _Db:
    """Gibt die vorbereiteten Ergebnisse in Aufrufreihenfolge zurück."""

    def __init__(self, *ergebnisse):
        self._ergebnisse = list(ergebnisse)

    async def execute(self, _stmt):
        return _Ergebnis(self._ergebnisse.pop(0))


def _db_skill(sid, name, description="", content="Inhalt"):
    return SimpleNamespace(id=sid, name=name, description=description, content=content, roles=None)


class EndpunkteTests(unittest.TestCase):
    """Der Agent kann nur laden, was er mit ID zu sehen bekommt."""

    def test_available_liefert_die_id(self):
        from app.api.skill_marketplace import agent_available_skills

        db = _Db([_db_skill(5, "buchhaltung-vorkontieren")], [None])
        antwort = asyncio.run(agent_available_skills(db=db, auth={"agent_id": "a1"}))
        self.assertEqual(5, antwort["skills"][0]["id"])

    def test_fachanleitungen_ordnet_den_auftrag_zu(self):
        from app.api.skill_marketplace import AgentFachanleitungenBody, agent_fachanleitungen

        s = _skill(5, "buchhaltung-vorkontieren")
        db = _Db([_db_skill(5, s["name"], s["description"], s["content"])], [None])
        antwort = asyncio.run(agent_fachanleitungen(
            body=AgentFachanleitungenBody(auftrag="Bitte einen DATEV-Buchungsstapel erstellen"),
            db=db, auth={"agent_id": "a1"},
        ))
        self.assertIn("skill_install(skill_id=5)", antwort["prompt"])
        self.assertEqual(["buchhaltung-vorkontieren"], [a["name"] for a in antwort["anleitungen"]])


class AnleitungUndVorlagenTests(unittest.TestCase):
    def test_gemeinsame_anleitung_verlangt_die_fachanleitung_vor_dem_ergebnis(self):
        from app.core.agent_manager import _render_claude_md
        from app.core.agent_templates import BUILTIN_TEMPLATES  # noqa: F401 — Import muss gehen

        for modus in ("claude_code", "codex_cli", "custom_llm"):
            with self.subTest(modus=modus):
                text = _render_claude_md([], agent_name="Test", agent_role="Buchhaltung", mode=modus)
                # Weit vorne: die Laufzeit kürzt eine lange Anleitung von hinten.
                kopf = text[: 6000]
                self.assertIn("## Fachanleitungen", kopf)
                self.assertIn("skill_install", kopf)

    def test_vorlagenwissen_traegt_die_muss_regeln(self):
        from app.core.agent_templates import BUILTIN_TEMPLATES

        wissen = {t["name"]: t["knowledge_template"] for t in BUILTIN_TEMPLATES}
        self.assertIn("buchhaltung-vorkontieren", wissen["bookkeeper"])
        self.assertIn("119,00", wissen["bookkeeper"])  # Rechenbeispiel der Brutto-Regel
        self.assertIn("angebot-kalkulieren", wissen["quote-clerk"])
        self.assertIn("[Ihr Firmenname]", wissen["quote-clerk"])  # als Verbot genannt


if __name__ == "__main__":
    unittest.main()


class RollenSkillsNurBekannterHerkunft(unittest.IsolatedAsyncioTestCase):
    """Über die Rolle fällt ein Skill jedem Agenten der Anlage zu, und seine Anleitung
    landet im Prompt. Ein fremder, selbst angelegter Skill mit passendem Rollennamen
    darf das nicht (Sicherheitsprüfung v1.362: nutzerübergreifende Einschleusung)."""

    async def asyncSetUp(self):
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.models.agent import Agent, AgentState
        from app.models.skill import AgentSkillAssignment, Skill, SkillCategory, SkillStatus

        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            for m in (Agent, Skill, AgentSkillAssignment):
                await conn.run_sync(m.metadata.create_all, tables=[m.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        from app.core.mitgelieferte_skills import QUELLE

        async with self.Session() as db:
            db.add(Agent(id="a1", name="Buchhaltung", state=AgentState.RUNNING, user_id="besitzer",
                         config={"role": "Buchhaltung"}))
            for i, (name, herkunft, erstellt) in enumerate([
                ("mitgeliefert", QUELLE, "system"),
                ("eigener", None, "besitzer"),
                ("fremder", None, "jemand-anderes"),
                ("falsche-quelle", "irgendwer/repo", "import:github"),
            ], 1):
                db.add(Skill(id=i, name=name, description="d", content="ANLEITUNG " + name,
                             category=SkillCategory.TEMPLATE, status=SkillStatus.ACTIVE,
                             created_by=erstellt, source_repo=herkunft, roles=["Buchhaltung"]))
            await db.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_nur_bekannte_herkunft_und_eigene(self):
        from app.api.skill_marketplace import _skills_des_agenten

        async with self.Session() as db:
            namen = sorted(s.name for s in await _skills_des_agenten(db, "a1"))
        self.assertEqual(namen, ["eigener", "mitgeliefert"])

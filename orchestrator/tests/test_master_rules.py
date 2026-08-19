"""Master-Regeln: Verhaltensvorgaben fuer ALLE Agenten aller Nutzer.

Wunsch des Kunden vom 18.08.2026, woertlich: eine „globale Verhaltensvariable
fuer normalen User (nicht Admins)" — „ich will aber nicht bei jedem agenten das
einzeln vorgeben … nur so grosse verhaltensregeln".

Der Schwerpunkt dieser Tests: **keine Laufzeit darf fehlen.** Genau das ist am
selben Tag zweimal passiert (die Rueckfrage-Anzeige gab es dreimal, eine wurde
vergessen; die Sprachfront hatte eine eigene Werkzeugliste). Eine Regel, die
fuer sechs von sieben Agenten gilt, ist keine Regel.
"""

import unittest
from unittest.mock import AsyncMock, patch

from app.core import master_rules


REGELN = "- Keine Anwendungen mit pornografischen Inhalten.\n- Nichts ohne Freigabe veroeffentlichen."


class RenderingTests(unittest.TestCase):
    def test_the_rules_appear_verbatim(self):
        self.assertIn(REGELN, master_rules.render(REGELN))

    def test_they_are_marked_as_non_negotiable(self):
        """Ohne diesen Rahmen liest das Modell sie als einen Wunsch unter vielen
        und laesst sich davon abbringen."""
        block = master_rules.render(REGELN)
        self.assertIn("MASTER-REGELN", block)
        self.assertIn("nicht verhandelbar", block)

    def test_nothing_set_means_nothing_added(self):
        """Aufrufer haengen bedingungslos an — ein leerer Block darf keinen
        sinnlosen Abschnitt in jede Anleitung schreiben."""
        self.assertEqual(master_rules.render(""), "")
        self.assertEqual(master_rules.render(None), "")
        self.assertEqual(master_rules.render("   "), "")

    def test_switched_off_means_nothing_added(self):
        self.assertEqual(master_rules.render(REGELN, aktiv=False), "")

    def test_an_overlong_text_is_cut_not_refused(self):
        """Die Regeln gehen in JEDEN Systemkontext — ein Roman ginge auf Kosten
        des Platzes fuer die eigentliche Arbeit."""
        block = master_rules.render("x" * (master_rules.MAX_ZEICHEN + 500))
        self.assertLess(len(block), master_rules.MAX_ZEICHEN + 800)
        self.assertIn("[…]", block)


class LoadingTests(unittest.IsolatedAsyncioTestCase):
    async def _load(self, werte):
        svc = AsyncMock()
        svc.get = AsyncMock(side_effect=lambda k: werte.get(k))
        with patch("app.services.settings_service.SettingsService", return_value=svc):
            return await master_rules.load(object())

    async def test_rules_are_read_from_the_settings_store(self):
        block = await self._load({"master_rules": REGELN})
        self.assertIn(REGELN, block)

    async def test_unset_switch_means_on(self):
        """Wer Regeln hinterlegt, will sie angewandt sehen — nicht erst noch
        einen zweiten Haken suchen."""
        self.assertIn(REGELN, await self._load({"master_rules": REGELN}))

    async def test_switching_off_wins(self):
        self.assertEqual(await self._load({"master_rules": REGELN, "master_rules_enabled": False}), "")

    async def test_a_broken_store_does_not_stop_the_agent(self):
        """Ein Agent, der nicht hochkommt, hilft niemandem — und die
        technischen Sperren greifen unabhaengig davon."""
        svc = AsyncMock()
        svc.get = AsyncMock(side_effect=RuntimeError("DB weg"))
        with patch("app.services.settings_service.SettingsService", return_value=svc):
            self.assertEqual(await master_rules.load(object()), "")


class EveryRuntimeGetsThemTests(unittest.TestCase):
    """Claude Code, Codex und Custom-LLM lesen alle die Anleitungsdatei; die
    Sprachfront baut ihren Prompt selbst und braucht einen eigenen Anschluss."""

    from pathlib import Path
    ROOT = Path(__file__).resolve().parents[1]
    MANAGER = (ROOT / "app/core/agent_manager.py").read_text()
    VOICE = (ROOT / "app/services/realtime_voice_session.py").read_text()

    def test_the_instruction_file_carries_them(self):
        """Damit sind Claude Code (CLAUDE.md), Codex (AGENT.md) und Custom-LLM
        (liest die Datei ueber get_identity_context) abgedeckt."""
        self.assertIn("master_rules: str = \"\"", self.MANAGER)
        # Seit dem laufzeitspezifischen Zusatz folgt darauf noch ein kurzer
        # Absatz je Harness — die Regeln bleiben aber der ERSTE Summand.
        self.assertIn("return master_rules + ", self.MANAGER)

    def test_they_stand_at_the_very_top(self):
        """Die Agenten-Laufzeit kuerzt eine zu lange Anleitung von HINTEN.
        Angehaengt waeren ausgerechnet die Regeln als Erstes weg."""
        rumpf = self.MANAGER.split("def _render_claude_md", 1)[1][:1200]
        rueckgabe = rumpf.split("return ", 1)[1]
        self.assertTrue(
            rueckgabe.startswith("master_rules"),
            "die Master-Regeln stehen nicht mehr als Erstes in der Anleitung",
        )

    def test_no_render_path_forgets_them(self):
        """Es gibt VIER Stellen, die die Anleitung schreiben (anlegen,
        auffrischen, neustarten, aktualisieren). Eine ohne Regeln waere ein
        Agent ohne Gesetz."""
        stellen = self.MANAGER.split("_render_claude_md(")[1:]
        # Die erste Fundstelle ist die Definition selbst.
        for i, block in enumerate(stellen[1:], start=1):
            with self.subTest(aufruf=i):
                self.assertIn("master_rules=", block[:400])

    def test_the_voice_front_gets_them_too(self):
        self.assertIn("master_rules as _mr", self.VOICE)
        block = self.VOICE.split("sys_prompt = (", 1)[1][:200]
        self.assertIn("_master", block)

    def test_the_voice_front_puts_them_first(self):
        block = self.VOICE.split("sys_prompt = (", 1)[1][:200]
        self.assertLess(block.index("_master"), block.index("_system_prompt("))


class TheAdminCanSetThemTests(unittest.TestCase):
    from pathlib import Path
    ROOT = Path(__file__).resolve().parents[2]
    SETTINGS = (ROOT / "orchestrator/app/services/settings_service.py").read_text()
    SEITE = (ROOT / "frontend/src/app/admin/page.tsx").read_text()
    ANSICHT = (ROOT / "frontend/src/app/admin/master-rules-view.tsx").read_text()

    def test_the_keys_are_writable(self):
        self.assertIn('"master_rules"', self.SETTINGS)
        self.assertIn('"master_rules_enabled"', self.SETTINGS)

    def test_the_tab_sits_under_security(self):
        block = self.SEITE.split('label: "Sicherheit"', 1)[1][:160]
        self.assertIn("master-rules", block)

    def test_the_tab_actually_renders_something(self):
        """Ein registrierter Reiter allein zeigt NICHTS: der Inhaltsblock haengt
        an ``EMBEDDED_TABS``. Genau das fehlte beim ersten Anlauf — Build und
        Typpruefung sahen es nicht, die Seite blieb leer."""
        zeile = next(z for z in self.SEITE.splitlines() if "EMBEDDED_TABS: Tab[]" in z)
        self.assertIn("master-rules", zeile)
        self.assertIn('{tab === "master-rules" && <MasterRulesView', self.SEITE)

    def test_the_global_command_policies_are_editable_there(self):
        """Das Datenmodell konnte globale Sperren laengst — es gab nur nirgends
        eine Stelle, sie einzustellen."""
        self.assertIn('scope === "global"', self.ANSICHT)
        self.assertIn("createCommandPolicy", self.ANSICHT)

    def test_the_page_says_that_rules_are_not_a_lock(self):
        """Sonst entsteht beim Betreiber der Eindruck, damit sei etwas
        technisch unmoeglich gemacht."""
        self.assertIn("keine Sperre", self.ANSICHT)


if __name__ == "__main__":
    unittest.main()

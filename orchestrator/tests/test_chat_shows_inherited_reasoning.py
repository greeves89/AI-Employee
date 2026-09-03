"""Der Chat muss zeigen, welche Denktiefe wirklich gilt.

Kundenmeldung (03.09.2026): „wenn ich einen neuen Chat oeffne steht bei
Reasoning 'Auto' — obwohl die Standard-Denktiefe auf 'Extra High' fuer diesen
Agenten gesetzt ist."

Die Einstellung WIRKT dabei die ganze Zeit: `chat_handler.py` waehlt
`reasoning or default_reasoning`, ein neuer Chat hat keine eigene Stufe, also
greift die Vorgabe des Agenten. Falsch war allein die Anzeige — der Knopf kannte
nur den chat-eigenen Wert und schrieb stur „Auto". Wer seine Einstellung
gewissenhaft gesetzt hat, muss daraus schliessen, sie sei wirkungslos. Genau das
hat der Kunde geschlossen.
"""

import re
import unittest
from pathlib import Path

_WURZEL = Path(__file__).resolve().parents[2]
_CHAT = (_WURZEL / "frontend" / "src" / "components" / "agents" / "chat.tsx").read_text()
_AGENT_SEITE = (_WURZEL / "frontend" / "src" / "app" / "agents" / "[id]" / "page.tsx").read_text()
_HANDLER = (_WURZEL / "agent" / "app" / "chat_handler.py").read_text()


class DieVorgabeWirktWirklichTests(unittest.TestCase):
    """Vorbedingung des ganzen Befunds: es war ein ANZEIGE-Fehler, kein
    Funktionsfehler. Bricht das hier, ist es plötzlich beides."""

    def test_die_laufzeit_faellt_auf_die_vorgabe_zurueck(self):
        self.assertIn("reasoning or _settings.default_reasoning", _HANDLER)

    def test_die_vorgabe_erreicht_den_container(self):
        mgr = (_WURZEL / "orchestrator" / "app" / "core" / "agent_manager.py").read_text()
        self.assertIn('"DEFAULT_REASONING": str(config.get("default_reasoning", "") or "")', mgr)


class DerKnopfZeigtWasGiltTests(unittest.TestCase):
    def test_die_vorgabe_wird_geladen(self):
        self.assertIn("const [agentDefaultReasoning, setAgentDefaultReasoning]", _CHAT)
        self.assertIn("asReasoningLevel(cfg?.default_reasoning)", _CHAT)

    def test_sie_wird_im_selben_aufruf_geholt_wie_das_modell(self):
        """Ein eigener Abruf nur fuer die Denktiefe waere Verschwendung — die
        Antwort enthaelt Modell und Vorgabe zusammen."""
        block = _CHAT.split("const [agentModel, setAgentModel]", 1)[1][:800]
        self.assertIn("setAgentModel(a.model", block)
        self.assertIn("setAgentDefaultReasoning(", block)
        self.assertEqual(block.count("api.getAgent(agentId)"), 1)

    def test_die_reihenfolge_entspricht_der_laufzeit(self):
        """Die Anzeige muss dieselbe Regel anwenden wie der Agent, sonst zeigt
        sie erneut etwas anderes als gilt."""
        zeile = [z for z in _CHAT.splitlines() if "const wirksameStufe" in z][0]
        self.assertIn("reasoning || agentDefaultReasoning", zeile)

    def test_der_knopf_zeigt_die_wirksame_stufe(self):
        block = _CHAT.split("<Brain className=\"h-4 w-4\" />", 1)[1][:400]
        self.assertIn("wirksameStufe", block)

    def test_geerbt_sieht_anders_aus_als_selbst_gewaehlt(self):
        """Sonst sieht es aus, als haette man hier etwas eingestellt — und die
        naechste Frage waere, warum man es nicht zuruecksetzen kann."""
        self.assertIn("stufeIstGeerbt", _CHAT)
        block = _CHAT.split("stufeIstGeerbt", 1)[1]
        self.assertIn("text-violet-300/70", block)

    def test_ohne_vorgabe_steht_weiterhin_auto(self):
        """Ein Agent ohne gesetzte Vorgabe darf keine erfinden."""
        self.assertIn(': "Auto"', _CHAT)
        zeile = [z for z in _CHAT.splitlines() if "const stufeIstGeerbt" in z][0]
        self.assertIn("!!agentDefaultReasoning", zeile)

    def test_der_hinweistext_nennt_die_herkunft(self):
        self.assertIn("Vorgabe dieses Agenten", _CHAT)

    def test_das_auswahlmenue_nennt_die_vorgabe(self):
        """Wer „Auto" waehlen will, soll wissen, worauf das hinauslaeuft."""
        self.assertIn("— Vorgabe:", _CHAT)


class DerErklaertextTraegtEchteUmlauteTests(unittest.TestCase):
    """Harte Vorgabe fuer nutzersichtbaren deutschen Text — vom Nutzer mehrfach
    beanstandet. Der Text stand im Screenshot des Kunden mit ae/oe/ue."""

    def test_der_hinweis_unter_den_stufen(self):
        self.assertIn("Gilt für Aufgaben, Zeitpläne, delegierte Aufträge", _AGENT_SEITE)
        self.assertIn("Chats ohne gewählte Stufe", _AGENT_SEITE)
        self.assertIn("Vollständig wirksam", _AGENT_SEITE)

    def test_die_rueckmeldungen_beim_speichern(self):
        self.assertIn("gilt vollständig ab dem nächsten Neuerstellen", _AGENT_SEITE)
        self.assertIn("auf Auto zurückgesetzt", _AGENT_SEITE)

    def test_keine_ersatzschreibweise_mehr_im_sichtbaren_text(self):
        block = _AGENT_SEITE.split("{/* Standard-Denktiefe", 1)[1][:3500]
        sichtbar = re.findall(r'text: "([^"]+)"', block) + re.findall(r"^\s{10,}([A-ZÄÖÜ][^<>{}\n]{25,})$", block, re.M)
        # „Neuerstellen" enthaelt „uer", „neue" ein „ue" — nach echten
        # Ersatzschreibweisen wird deshalb woertlich gesucht, nicht per Muster.
        VERDAECHTIG = ("fuer", "Zeitplaene", "Auftraege", "gewaehlte",
                       "Vollstaendig", "naechsten", "zurueckgesetzt",
                       "vollstaendig", "koennen", "muessen")
        for t in sichtbar:
            for wort in VERDAECHTIG:
                self.assertNotIn(wort, t, f"Ersatzschreibweise {wort!r}: {t[:70]}")


if __name__ == "__main__":
    unittest.main()

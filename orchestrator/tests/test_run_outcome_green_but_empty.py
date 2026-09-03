"""Ein „fertig", hinter dem nichts steht, ist kein Erfolg.

Befund #680: Vom 27.08. bis 30.08.2026, 76 Stunden lang, hat kein einziger
Zeitplan-Lauf eines Agenten Arbeit geleistet. Alle 94 Laeufe standen auf
``completed``, ``error`` war leer. Im Ergebnisfeld stand:

    401 OAuth access token has expired. Re-authenticate to continue.   (71x)
    You've hit your limit - resets 1pm (Europe/Berlin)                 (23x)

Drei Tage ohne Podcast, ohne Tagesplan, ohne Morgencheck — und eine Oberflaeche
voller gruener Haken. Keine Ueberwachung schlug an, weil alle auf ``failed``
filtern.

Der zweite Teil des Befunds ist genauso wichtig: das bisherige Sicherheitsnetz,
ein Kontrolllauf um 08:00, lief im SELBEN Container mit derselben Anmeldung und
starb am selben 401. Ein Selbsttest kann einen Anmeldeausfall grundsaetzlich
nicht auffangen — deshalb liegt die Pruefung im Orchestrator.
"""

import unittest
from pathlib import Path

from app.core.run_outcome import (
    SERIE_MELDEN_AB,
    ist_zugangsproblem,
    serie_gebrochen,
    warum_kein_erfolg,
)

_ROUTER = (Path(__file__).resolve().parents[1] / "app" / "core" / "task_router.py").read_text()


class DieEchtenVorfaelleWerdenErkanntTests(unittest.TestCase):
    """Woertliche Ausgaben aus den 94 Laeufen vom 27.-30.08.2026."""

    def test_abgelaufener_zugang(self):
        self.assertEqual(
            warum_kein_erfolg(
                "Failed to authenticate. API Error: 401 OAuth access token has "
                "expired. Re-authenticate to continue.", 800),
            "Zugang abgelaufen")

    def test_erschoepftes_kontingent(self):
        self.assertEqual(
            warum_kein_erfolg("You've hit your limit · resets 1pm (Europe/Berlin)", 500),
            "Kontingent erschoepft")

    def test_verbrauchter_codex_token(self):
        self.assertEqual(warum_kein_erfolg("refresh_token_reused", 400), "Zugang abgelehnt")

    def test_leeres_ergebnis_in_sekundenbruchteilen(self):
        self.assertEqual(warum_kein_erfolg("", 300),
                         "Leeres Ergebnis nach weniger als 10 Sekunden")


class EchteArbeitBleibtGruenTests(unittest.TestCase):
    """Wichtiger als das Erkennen: NICHT falsch anschlagen. Ein Check, der
    gesunde Laeufe rot faerbt, wird abgeschaltet."""

    def test_ein_gewoehnlicher_bericht(self):
        self.assertIsNone(warum_kein_erfolg("Bericht fertig, 12 Punkte abgearbeitet.", 45000))

    def test_ein_langer_lauf_ohne_text(self):
        """Kann echte Arbeit gewesen sein — eine geschriebene Datei etwa."""
        self.assertIsNone(warum_kein_erfolg("", 900_000))

    def test_ein_bericht_UEBER_anmeldefehler_faerbt_nichts(self):
        """Der Agent, der Anmeldefehler AUSWERTET, darf nicht selbst rot werden.
        Deshalb sind die Signaturen eng an den echten Wortlaut gebunden."""
        self.assertIsNone(warum_kein_erfolg(
            "Im Log fanden sich mehrere Meldungen ueber abgelaufene Zugaenge; "
            "ich habe sie im Bericht zusammengefasst.", 60000))

    def test_eine_zahl_401_im_fliesstext(self):
        self.assertIsNone(warum_kein_erfolg("Datei mit 401 Zeilen verarbeitet.", 30000))

    def test_ohne_dauer_wird_leere_ausgabe_nicht_verurteilt(self):
        self.assertIsNone(warum_kein_erfolg("", None))


class DieSerienschwelleTests(unittest.TestCase):
    def test_ein_einzelner_ausrutscher_meldet_nicht(self):
        self.assertFalse(serie_gebrochen(["Zugang abgelaufen", None, None]))

    def test_drei_in_folge_melden(self):
        self.assertTrue(serie_gebrochen(
            ["Zugang abgelaufen", "Zugang abgelaufen", "Zugang abgelaufen"]))

    def test_ein_gelungener_lauf_dazwischen_bricht_die_serie(self):
        self.assertFalse(serie_gebrochen(
            ["Zugang abgelaufen", None, "Zugang abgelaufen", "Zugang abgelaufen"]))

    def test_zu_wenig_verlauf_meldet_nicht(self):
        self.assertFalse(serie_gebrochen(["Zugang abgelaufen"]))


class DerHinweisPasstZurUrsacheTests(unittest.TestCase):
    def test_ein_zugangsproblem_braucht_eine_anmeldung(self):
        self.assertTrue(ist_zugangsproblem("Zugang abgelaufen"))
        self.assertTrue(ist_zugangsproblem("Zugang abgelehnt"))

    def test_ein_kontingent_erledigt_sich_von_selbst(self):
        """Dort waere „bitte neu anmelden" ein falscher Rat."""
        self.assertFalse(ist_zugangsproblem("Kontingent erschoepft"))
        self.assertFalse(ist_zugangsproblem(None))


class DiePruefungHaengtImRichtigenPfadTests(unittest.TestCase):
    def test_sie_laeuft_vor_dem_setzen_des_status(self):
        block = _ROUTER.split("async def handle_task_completion", 1)[1][:3000]
        self.assertIn("warum_kein_erfolg(task.result", block)
        self.assertIn("task.status = TaskStatus.FAILED", block)

    def test_der_grund_landet_im_fehlerfeld(self):
        """Sonst bleibt der Lauf zwar rot, aber ohne Erklaerung."""
        block = _ROUTER.split("async def handle_task_completion", 1)[1][:3000]
        self.assertIn("task.error = grund", block)

    def test_nur_gemeldete_erfolge_werden_geprueft(self):
        """Ein bereits gemeldeter Fehlschlag braucht keine zweite Beurteilung."""
        block = _ROUTER.split("async def handle_task_completion", 1)[1][:3000]
        self.assertIn("if task.status == TaskStatus.COMPLETED:", block)

    def test_die_serie_wird_gemeldet(self):
        self.assertIn("async def _melde_ausfall_serie", _ROUTER)
        self.assertIn("_melde_ausfall_serie(agent_id, task, grund)", _ROUTER)

    def test_nur_beim_ueberschreiten_der_schwelle(self):
        """Sonst haette der Nutzer damals 69 Meldungen bekommen."""
        block = _ROUTER.split("async def _melde_ausfall_serie", 1)[1][:2500]
        self.assertIn("Schwelle war schon ueberschritten", block)

    def test_die_pruefung_liegt_nicht_im_agenten(self):
        """Das alte Sicherheitsnetz lief im selben Container und starb am
        selben 401 — ein Selbsttest kann das nicht auffangen."""
        agent = Path(__file__).resolve().parents[2] / "agent" / "app"
        treffer = [p.name for p in agent.rglob("*.py")
                   if "warum_kein_erfolg" in p.read_text()]
        self.assertEqual(treffer, [], f"Die Pruefung darf nicht im Agenten liegen: {treffer}")


if __name__ == "__main__":
    unittest.main()

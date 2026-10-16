"""Die Rechenregel des adaptiven Takts (#941) — ohne Datenbank, ohne Uhr.

Geprüft wird die Abnahme aus dem Issue wörtlich: Die Folge idle, idle, idle,
idle, idle, worked muss die Abstände 30 Min (Grundtakt) ×3, dann 2 h, 4 h, 8 h
und danach wieder 30 Min ergeben.

Dazu die beiden Gegenproben, ohne die der Rest nichts beweist:
  * **Ohne Feature bleibt der Takt fest** — derselbe Durchlauf an einem nicht
    adaptiven Zeitplan rührt das Intervall nicht an.
  * **Die Leiter endet** — der sechste und siebte leere Lauf bleiben bei 8 h,
    statt in Tage zu laufen.
"""

import unittest
from datetime import datetime, timedelta, timezone

from app.core.adaptiver_takt import (
    LEER_SCHWELLE_STANDARD,
    MAX_INTERVALL_S,
    STUFEN_STANDARD,
    basis_von,
    beschreibe,
    ist_leer,
    naechster_lauf,
    stufe_fuer,
    stufen_pruefen,
    stufen_von,
    takt_nach_meldung,
)
from app.services.takt_service import takt_anwenden


class _Zeitplan:
    """So viel Zeitplan, wie die Rechenregel anfasst."""

    def __init__(self, **kw):
        self.name = "Beobachtung"
        self.adaptive = True
        self.interval_seconds = 1800
        self.basis_intervall_s = 1800
        self.stufen = list(STUFEN_STANDARD)
        self.leer_schwelle = LEER_SCHWELLE_STANDARD
        self.leerlaeufe = 0
        self.stufe = 0
        self.cron_expression = None
        self.last_run_at = datetime(2026, 10, 15, 8, 0, tzinfo=timezone.utc)
        self.next_run_at = self.last_run_at + timedelta(seconds=1800)
        for k, v in kw.items():
            setattr(self, k, v)


#: Die Abnahme aus #941: fünf leere Läufe, dann Arbeit.
FOLGE = ["idle", "idle", "idle", "idle", "idle", "worked"]
#: Die Abstände, die danach gelten sollen (Sekunden).
ERWARTET = [1800, 1800, 7200, 14400, 28800, 1800]


class DieAbnahmeAusDemIssue(unittest.TestCase):
    def test_folge_idle5_dann_worked(self):
        plan = _Zeitplan()
        # Der Grundtakt gilt schon VOR der ersten Meldung — das ist das erste
        # der „30 Min ×3" aus dem Issue.
        self.assertEqual(plan.interval_seconds, 1800)

        gemessen = []
        jetzt = plan.last_run_at
        for ergebnis in FOLGE:
            # Was der Scheduler beim Feuern tut: Lauf vermerken, next_run_at
            # um den AKTUELLEN Takt weiterstellen.
            plan.last_run_at = jetzt
            plan.next_run_at = jetzt + timedelta(seconds=plan.interval_seconds)
            # Der Agent meldet zwei Minuten später, am Ende seines Laufs.
            takt_anwenden(plan, ergebnis, jetzt + timedelta(minutes=2))
            abstand = (plan.next_run_at - jetzt).total_seconds()
            gemessen.append(int(abstand))
            jetzt = plan.next_run_at

        self.assertEqual(gemessen, ERWARTET)

    def test_die_zaehler_stehen_richtig(self):
        plan = _Zeitplan()
        for n, ergebnis in enumerate(FOLGE[:5], start=1):
            takt_anwenden(plan, ergebnis)
            self.assertEqual(plan.leerlaeufe, n)
        self.assertEqual(plan.stufe, 3)
        takt_anwenden(plan, "worked")
        self.assertEqual((plan.leerlaeufe, plan.stufe, plan.interval_seconds), (0, 0, 1800))

    def test_warten_zaehlt_wie_leerlauf(self):
        """„Ich warte auf eine Entscheidung" ist kein erledigter Lauf: Häufiger
        nachzusehen bringt dann nichts."""
        plan = _Zeitplan()
        for _ in range(3):
            takt_anwenden(plan, "waiting")
        self.assertEqual(plan.interval_seconds, 7200)
        self.assertTrue(ist_leer("waiting"))
        self.assertFalse(ist_leer("worked"))


class GegenprobeOhneFeature(unittest.TestCase):
    """Ohne ``adaptive`` bleibt der Takt fest — sonst würde der Test oben auch
    grün bleiben, wenn die Taktrechnung einfach immer läuft."""

    def test_nicht_adaptiver_zeitplan_bleibt_bei_30_min(self):
        plan = _Zeitplan(adaptive=False)
        jetzt = plan.last_run_at
        gemessen = []
        for ergebnis in FOLGE:
            plan.last_run_at = jetzt
            plan.next_run_at = jetzt + timedelta(seconds=plan.interval_seconds)
            wechsel = takt_anwenden(plan, ergebnis, jetzt + timedelta(minutes=2))
            self.assertFalse(wechsel["geaendert"])
            self.assertFalse(wechsel["adaptive"])
            gemessen.append(int((plan.next_run_at - jetzt).total_seconds()))
            jetzt = plan.next_run_at
        self.assertEqual(gemessen, [1800] * 6)
        # Auch der Zustand bleibt unberührt: kein heimliches Mitzählen.
        self.assertEqual((plan.leerlaeufe, plan.stufe), (0, 0))

    def test_der_hinweis_sagt_was_zu_tun_ist(self):
        wechsel = takt_anwenden(_Zeitplan(adaptive=False), "idle")
        self.assertIn("nicht adaptiv", wechsel["hinweis"])
        self.assertIn("update_schedule", wechsel["hinweis"])

    def test_cron_zeitplan_wird_nicht_umgetaktet(self):
        """Adaptiv gilt nur für Intervall-Zeitpläne (#941, Abgrenzung)."""
        plan = _Zeitplan(cron_expression="0 * * * *", interval_seconds=0)
        wechsel = takt_anwenden(plan, "idle")
        self.assertFalse(wechsel["geaendert"])
        self.assertEqual(plan.interval_seconds, 0)
        self.assertIn("Cron", wechsel["hinweis"])


class DieLeiterEndet(unittest.TestCase):
    def test_nach_der_letzten_stufe_bleibt_es_bei_8_stunden(self):
        plan = _Zeitplan()
        for _ in range(12):
            takt_anwenden(plan, "idle")
        self.assertEqual(plan.interval_seconds, 28800)
        self.assertEqual(plan.stufe, len(STUFEN_STANDARD) - 1)

    def test_stufe_fuer_ist_monoton_und_gedeckelt(self):
        werte = [stufe_fuer(n, 3, 4) for n in range(0, 10)]
        self.assertEqual(werte, [0, 0, 0, 1, 2, 3, 3, 3, 3, 3])


class EigenerGrundtakt(unittest.TestCase):
    """Ein Zeitplan mit 15-Min-Grundtakt darf nach „worked" nicht auf 30 Min
    springen, bloß weil die Standardleiter dort anfängt."""

    def test_stufe_null_ist_immer_der_grundtakt(self):
        plan = _Zeitplan(interval_seconds=900, basis_intervall_s=900)
        # Ein 15-Min-Grundtakt BEHAELT die 30-Min-Stufe: sie ist langsamer als
        # er, also gehoert sie auf die Leiter. Deshalb sind es hier fuenf
        # Stufen statt vier, und die letzte wird einen leeren Lauf spaeter
        # erreicht.
        self.assertEqual(stufen_von(plan), [900, 1800, 7200, 14400, 28800])
        for _ in range(5):
            takt_anwenden(plan, "idle")
        self.assertEqual(plan.interval_seconds, 14400)
        takt_anwenden(plan, "idle")
        self.assertEqual(plan.interval_seconds, 28800)
        takt_anwenden(plan, "worked")
        self.assertEqual(plan.interval_seconds, 900)

    def test_basis_faellt_auf_das_aktuelle_intervall_zurueck(self):
        self.assertEqual(basis_von(_Zeitplan(basis_intervall_s=None, interval_seconds=600)), 600)

    def test_eigene_stufenleiter(self):
        plan = _Zeitplan(stufen=[600, 1200], basis_intervall_s=600, interval_seconds=600,
                         leer_schwelle=1)
        takt_anwenden(plan, "idle")
        self.assertEqual(plan.interval_seconds, 1200)
        takt_anwenden(plan, "idle")
        self.assertEqual(plan.interval_seconds, 1200)  # Leiter zu Ende


class FremdeEingabeWirdGeprueft(unittest.TestCase):
    """Die Stufenleiter kommt aus einem Werkzeugaufruf — also von außen."""

    def test_gueltige_leiter(self):
        self.assertEqual(stufen_pruefen([60, 120, 180]), [60, 120, 180])

    def test_abgelehnt_wird(self):
        for schlecht in (
            "1800",                 # Zeichenkette
            [1800, "7200"],         # Zeichenkette in der Liste
            [1800, 1.5],            # keine ganze Zahl
            [True, 7200],           # bool ist in Python ein int — hier ein Irrtum
            [],                     # leer
            [30],                   # unter einer Minute: feuert dauernd
            [MAX_INTERVALL_S + 1],  # über einer Woche: kommt nie wieder
            [7200, 1800],           # nicht aufsteigend
            [1800, 1800],           # doppelt (wäre eine Stufe ohne Wirkung)
            list(range(60, 60 + 9 * 60, 60)),  # zu viele Stufen
            {"a": 1},               # gar keine Liste
        ):
            with self.subTest(wert=schlecht):
                with self.assertRaises(ValueError):
                    stufen_pruefen(schlecht)

    def test_kaputtes_feld_in_der_datenbank_faellt_auf_den_standard(self):
        """Ein Zeitplan, der wegen eines verbogenen Feldes GAR NICHT mehr
        feuert, wäre der schlimmere Ausgang."""
        plan = _Zeitplan(stufen="unsinn")
        self.assertEqual(stufen_von(plan)[1:], list(STUFEN_STANDARD)[1:])


class DerNaechsteLauf(unittest.TestCase):
    def test_abstand_wird_vom_gemeldeten_lauf_gerechnet(self):
        lauf = datetime(2026, 10, 15, 8, 0, tzinfo=timezone.utc)
        self.assertEqual(
            naechster_lauf(lauf, lauf + timedelta(minutes=2), 28800),
            lauf + timedelta(hours=8),
        )

    def test_liegt_immer_in_der_zukunft(self):
        lauf = datetime(2026, 10, 15, 8, 0, tzinfo=timezone.utc)
        jetzt = lauf + timedelta(hours=9)   # Meldung kam spät (langer Lauf)
        self.assertGreater(naechster_lauf(lauf, jetzt, 1800), jetzt)

    def test_ohne_zeitzone_aus_sqlite(self):
        lauf = datetime(2026, 10, 15, 8, 0)  # tzinfo verloren
        jetzt = datetime(2026, 10, 15, 8, 2, tzinfo=timezone.utc)
        self.assertEqual(naechster_lauf(lauf, jetzt, 7200).tzinfo, timezone.utc)

    def test_phase_wandert_nicht(self):
        """Zwei Meldungen hintereinander dürfen den Takt nicht verschieben."""
        lauf = datetime(2026, 10, 15, 8, 0, tzinfo=timezone.utc)
        erste = naechster_lauf(lauf, lauf + timedelta(minutes=2), 1800)
        zweite = naechster_lauf(erste, erste + timedelta(minutes=7), 1800)
        self.assertEqual((zweite - erste).total_seconds(), 1800)


class DieTaktzeile(unittest.TestCase):
    def test_zeile_nennt_takt_leerlaeufe_und_grundtakt(self):
        plan = _Zeitplan(interval_seconds=14400, leerlaeufe=5, stufe=2)
        self.assertEqual(beschreibe(plan), "alle 4 Std (5 Leerläufe), Grundtakt alle 30 Min")

    def test_einzahl(self):
        plan = _Zeitplan(leerlaeufe=1)
        self.assertIn("1 Leerlauf)", beschreibe(plan))

    def test_nicht_adaptiv_bleibt_leer(self):
        self.assertEqual(beschreibe(_Zeitplan(adaptive=False)), "")

    def test_unbekanntes_ergebnis_wird_abgelehnt(self):
        with self.assertRaises(ValueError):
            takt_nach_meldung("fertig", 0, 3, 4)


class DieLeiterBleibtAufsteigend(unittest.TestCase):
    """Der Blocker aus dem Gegenlesen (#941): Bei einem Grundtakt ÜBER der
    zweiten Stufe entstand aus ``[1800, 7200, 14400, 28800]`` mit Grundtakt
    28800 die Leiter ``[28800, 7200, 14400, 28800]`` — und damit die Folge
    8 Std, 8 Std, 2 Std, 4 Std, 8 Std. Leerlauf machte den Beobachter also
    VIERMAL schneller statt langsamer, die exakte Umkehrung des Ziels.
    """

    def test_grundtakt_acht_stunden_wird_im_leerlauf_nie_schneller(self):
        plan = _Zeitplan(interval_seconds=28800, basis_intervall_s=28800)
        gemessen = []
        for ergebnis in FOLGE:
            takt_anwenden(plan, ergebnis)
            gemessen.append(plan.interval_seconds)
        self.assertEqual(gemessen, [28800] * 6)
        self.assertGreaterEqual(min(gemessen), 28800)

    def test_grundtakt_zwei_stunden_behaelt_nur_die_langsameren_stufen(self):
        plan = _Zeitplan(interval_seconds=7200, basis_intervall_s=7200)
        self.assertEqual(stufen_von(plan), [7200, 14400, 28800])

    def test_jede_leiter_ist_streng_aufsteigend(self):
        """Gegen jeden Grundtakt, den das Schema zulässt — nicht nur gegen den
        einen, der den Fehler gezeigt hat."""
        for grund in (60, 900, 1800, 3600, 7200, 14400, 28800, 86400, 604800):
            with self.subTest(grundtakt=grund):
                leiter = stufen_von(_Zeitplan(interval_seconds=grund, basis_intervall_s=grund))
                self.assertEqual(leiter[0], grund)
                self.assertEqual(min(leiter), grund)
                self.assertTrue(all(b > a for a, b in zip(leiter, leiter[1:])), leiter)

    def test_eine_stufe_langsamer_als_der_grundtakt_bleibt_erhalten(self):
        """Gegenprobe zum ersten Entwurf des Fixes: Der filterte ``werte[1:]``
        und warf damit die erste Stufe IMMER weg — auch wenn sie langsamer als
        der Grundtakt war. Ein 15-Min-Grundtakt verlor so die 30-Min-Stufe und
        sprang nach der Schwelle direkt auf 2 Std."""
        plan = _Zeitplan(interval_seconds=900, basis_intervall_s=900)
        self.assertEqual(stufen_von(plan), [900, 1800, 7200, 14400, 28800])

    def test_eine_eigene_leiter_mit_nur_einer_langsameren_stufe_bleibt_brauchbar(self):
        plan = _Zeitplan(stufen=[120], basis_intervall_s=60, interval_seconds=60,
                         leer_schwelle=1)
        self.assertEqual(stufen_von(plan), [60, 120])
        takt_anwenden(plan, "idle")
        self.assertEqual(plan.interval_seconds, 120)

    def test_eine_eigene_leiter_unter_dem_grundtakt_faellt_weg(self):
        plan = _Zeitplan(stufen=[600, 1200, 2400], basis_intervall_s=1800,
                         interval_seconds=1800, leer_schwelle=1)
        self.assertEqual(stufen_von(plan), [1800, 2400])
        takt_anwenden(plan, "idle")
        self.assertEqual(plan.interval_seconds, 2400)


if __name__ == "__main__":
    unittest.main()

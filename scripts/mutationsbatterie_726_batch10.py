#!/usr/bin/env python3
"""Mutationsbatterie fuer Batch 10 (#726): test_click_and_failure_reason.py und
test_screens_and_click_space.py.

Fuer jede Mutation am Produktionscode wird die Datei gesichert, mutiert, die
beiden Testmodule in einem frischen Prozess ausgefuehrt und die Datei per
try/finally wiederhergestellt. Nachgewiesen wird:
  1. jede Mutation faerbt mindestens einen Test rot,
  2. die Mengen der roten Tests sind paarweise verschieden,
  3. jeder umgebaute Test wird von mindestens einer Mutation getroffen.

Aufruf: python3 scripts/mutationsbatterie_726_batch10.py (aus dem Repo-Wurzelverzeichnis).
"""
import json
import os
import subprocess
import sys
from pathlib import Path

WT = Path(__file__).resolve().parents[1]
ORCH = WT / "orchestrator"
BRIDGE = WT / "computer-use-bridge/bridge.py"
VOICE = ORCH / "app/services/realtime_voice_session.py"
CUSTOM = WT / "agent/app/tools/api_client.py"
PY = "/workspace/.venv-orch/bin/python"
if not Path(PY).exists():
    PY = sys.executable
MODULE = ("tests.test_click_and_failure_reason", "tests.test_screens_and_click_space")

# Die umgebauten Tests (frueher Zeichenfenster) — jeder muss getroffen werden.
UMGEBAUT = {
    "test_click_and_failure_reason.AClickKnowsWhichScreenItMeansTests.test_a_named_display_sets_the_offset",
    "test_click_and_failure_reason.AClickKnowsWhichScreenItMeansTests.test_it_beats_the_last_screenshot",
    "test_click_and_failure_reason.AClickKnowsWhichScreenItMeansTests.test_without_a_display_nothing_changes",
    "test_click_and_failure_reason.AClickKnowsWhichScreenItMeansTests.test_the_early_exit_sits_at_the_top_of_the_function",
    "test_click_and_failure_reason.AClickKnowsWhichScreenItMeansTests.test_every_pointer_action_lands_on_the_named_screen",
    "test_click_and_failure_reason.TheModelIsToldNotToGuessTests.test_guessing_coordinates_is_forbidden_in_plain_words",
    "test_click_and_failure_reason.TheModelIsToldNotToGuessTests.test_it_says_where_valid_coordinates_come_from",
    "test_click_and_failure_reason.TheModelIsToldNotToGuessTests.test_the_two_screen_order_is_spelled_out",
    "test_click_and_failure_reason.AFailedAnalysisNamesItsReasonTests.test_the_reason_is_extracted_not_discarded",
    "test_click_and_failure_reason.AFailedAnalysisNamesItsReasonTests.test_the_model_is_told_to_pass_it_on",
    "test_click_and_failure_reason.AFailedAnalysisNamesItsReasonTests.test_it_no_longer_asks_the_user_what_he_sees_when_the_reason_is_known",
    "test_click_and_failure_reason.AFailedAnalysisNamesItsReasonTests.test_without_a_reason_the_old_wording_stays",
    "test_screens_and_click_space.TheBridgeSeesEveryScreenTests.test_the_primary_screen_is_number_one",
    "test_screens_and_click_space.TheBridgeSeesEveryScreenTests.test_an_unknown_screen_number_says_so",
    "test_screens_and_click_space.TheBridgeSeesEveryScreenTests.test_no_picture_is_taken_of_a_screen_that_does_not_exist",
    "test_screens_and_click_space.TheBridgeSeesEveryScreenTests.test_a_named_screen_is_the_one_that_is_captured",
    "test_screens_and_click_space.TheBridgeSeesEveryScreenTests.test_omitting_the_number_keeps_the_old_behaviour",
    "test_screens_and_click_space.ClicksLandOnTheRightScreenTests.test_the_offset_is_remembered_after_a_screenshot",
    "test_screens_and_click_space.ClicksLandOnTheRightScreenTests.test_the_offset_is_added_when_clicking",
    "test_screens_and_click_space.ClicksLandOnTheRightScreenTests.test_and_subtracted_on_the_way_back",
    "test_screens_and_click_space.EveryRuntimeIsToldTheSizeTests.test_the_voice_front_says_it",
    "test_screens_and_click_space.EveryRuntimeIsToldTheSizeTests.test_the_voice_front_stays_quiet_about_a_single_screen",
    "test_screens_and_click_space.EveryRuntimeIsToldTheSizeTests.test_the_custom_llm_runtime_says_it",
    "test_screens_and_click_space.TheScreenCanBeChosenEverywhereTests.test_the_voice_tool_takes_a_display",
}

LAEUFER = r"""
import json, sys, unittest
loader = unittest.TestLoader()
suite = unittest.TestSuite([loader.loadTestsFromName(m) for m in sys.argv[1:]])
class R(unittest.TextTestResult):
    def __init__(self, *a, **k):
        super().__init__(*a, **k); self.rot = set()
    def _id(self, test):
        tid = test.id()
        # Subtests: "mod.Klasse.test (antwort='')" -> Methodenname
        return tid.split(" (")[0].removeprefix("tests.")
    def addFailure(self, test, err):
        super().addFailure(test, err); self.rot.add(self._id(test))
    def addError(self, test, err):
        super().addError(test, err); self.rot.add(self._id(test))
    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err is not None: self.rot.add(self._id(test))
r = unittest.TextTestRunner(stream=open("/dev/null", "w"), resultclass=R, verbosity=0).run(suite)
print("ROT=" + json.dumps(sorted(r.rot)))
print("N=" + str(r.testsRun))
"""


def tests_laufen() -> tuple[set, int]:
    p = subprocess.run([PY, "-c", LAEUFER, *MODULE], cwd=ORCH, capture_output=True, text=True)
    rot, n = None, None
    for zeile in p.stdout.splitlines():
        if zeile.startswith("ROT="):
            rot = set(json.loads(zeile[4:]))
        elif zeile.startswith("N="):
            n = int(zeile[2:])
    if rot is None:
        raise RuntimeError(f"Laeufer ohne Ergebnis:\n{p.stdout}\n{p.stderr}")
    return rot, n


def ersetze(text: str, alt: str, neu: str, anzahl: int = 1) -> str:
    n = text.count(alt)
    assert n == anzahl, f"{alt!r}: {n}x gefunden, erwartet {anzahl}"
    return text.replace(alt, neu)


# ── Mutationen ───────────────────────────────────────────────────────────────
# (kennung, datei, funktion(text) -> mutierter text, beschreibung)

def m_a_list_displays_auskommentiert(t):
    # (a) Der Aufruf list_displays() in _display_offset auskommentiert — der
    # Text "list_displays()" steht weiterhin im Funktionsblock (als Kommentar).
    return ersetze(t,
        '        for b in list_displays():\n'
        '            if b.get("number") == nummer:\n'
        '                return int(b.get("x", 0)), int(b.get("y", 0))\n',
        '        # for b in list_displays():\n'
        '        #     if b.get("number") == nummer:\n'
        '        #         return int(b.get("x", 0)), int(b.get("y", 0))\n')


def m_b_nur_coord_offset(t):
    # (b) ausdruecklich genannter Bildschirm wird ignoriert
    return ersetze(t, "ox, oy = self._display_offset(display) or self._coord_offset",
                   "ox, oy = self._coord_offset")


def m_e1_fruehausstieg_auskommentiert(t):
    # (e) `if not display: return None` entfernt (auskommentiert, Text bleibt)
    return ersetze(t,
        '        if not display:\n            return None\n        try:\n            nummer = int(display)',
        '        # if not display:\n        #     return None\n        try:\n            nummer = int(display)')


def m_e2_fruehausstieg_verschachtelt(t):
    # (e) `if not display: return None` in den try-Zweig verschoben — Verhalten
    # (fast) gleich, aber nicht mehr auf Top-Level der Funktion.
    return ersetze(t,
        '        if not display:\n            return None\n        try:\n            nummer = int(display)',
        '        try:\n            if not display:\n                return None\n            nummer = int(display)')


def m_bridge_plus_statt_minus(t):
    return ersetze(t, "x, y = float(x) - ox, float(y) - oy", "x, y = float(x) + ox, float(y) + oy")


def m_bridge_minus_statt_plus(t):
    return ersetze(t, "return round(float(x) * sx) + ox, round(float(y) * sy) + oy",
                   "return round(float(x) * sx) - ox, round(float(y) * sy) - oy")


def m_bridge_ohne_maszstab(t):
    return ersetze(t, "return round(float(x) * sx) + ox, round(float(y) * sy) + oy",
                   "return round(float(x)) + ox, round(float(y)) + oy")


def m_bridge_haupt_nicht_zuerst(t):
    return ersetze(t, "sortiert = sorted(kennungen, key=lambda d: (d != haupt, d))",
                   "sortiert = sorted(kennungen)")


def m_bridge_unbekannter_bildschirm_stumm(t):
    return ersetze(t,
        '        if gewaehlt is None:\n            raise ValueError(\n'
        '                f"Bildschirm {display} gibt es nicht — verfuegbar sind "\n'
        '                f"1 bis {len(bildschirme)}."\n            )\n',
        '        # if gewaehlt is None:\n        #     raise ValueError("Bildschirm gibt es nicht")\n')


def m_bridge_immer_hauptbildschirm_aufnehmen(t):
    return ersetze(t, '_capture_macos_inprocess(gewaehlt.get("id") if gewaehlt else None)',
                   '_capture_macos_inprocess(None)')


def m_bridge_if_display_immer_wahr(t):
    return ersetze(t, "    if display:\n        gewaehlt = next(", "    if True:\n        gewaehlt = next(")


def m_bridge_versatz_nicht_gemerkt(t):
    return ersetze(t,
        '                    self._coord_offset = (\n'
        '                        (gewaehlt.get("x", 0), gewaehlt.get("y", 0)) if gewaehlt else (0, 0)\n'
        '                    )\n',
        '                    self._coord_offset = (0, 0)\n')


def m_c_sag_ihm_den_grund_weg(t):
    # (c) der Satz mit dem Grund auskommentiert (Zeichenketten-Fortsetzung)
    return ersetze(t,
        '                "Sag ihm diesen Grund kurz und in eigenen Worten — erfinde nichts "\n',
        '                # "Sag ihm diesen Grund kurz und in eigenen Worten — erfinde nichts "\n')


def m_voice_grund_roh(t):
    return ersetze(t, 'grund = answer[8:].rstrip("]").strip() if answer.startswith("[Fehler") else ""',
                   'grund = answer.rstrip("]").strip() if answer.startswith("[Fehler") else ""')


def m_voice_fragt_wieder(t):
    return ersetze(t, '"dazu und frage nicht, was er sieht."', '"dazu."')


def m_voice_zweige_vertauscht(t):
    return ersetze(t, "                if grund else\n", "                if not grund else\n")


def m_voice_liste_schon_bei_einem(t):
    return ersetze(t, "    if len(bildschirme) > 1:\n        aktuell = result.get(\"display\")",
                   "    if len(bildschirme) >= 1:\n        aktuell = result.get(\"display\")")


def m_voice_ursprung_verschwiegen(t):
    return ersetze(t,
        '"Klickkoordinaten muessen INNERHALB dieser Groesse liegen, (0,0) ist oben links."',
        '"Klickkoordinaten muessen INNERHALB dieser Groesse liegen."')


def m_voice_display_feld_umbenannt(t):
    return ersetze(t, '                "display": {"type": "number", "description": (\n',
                   '                "screen": {"type": "number", "description": (\n')


def m_voice_verbot_im_falschen_absatz(t):
    # (d)-Variante fuer den Prompt: das Verbot steht weiter im Text (alte
    # Existenzpruefung bestuende), aber nicht mehr in der click-Erklaerung.
    t = ersetze(t, '"mitgeben. KOORDINATEN NIEMALS RATEN: nimm ausschliesslich Werte, die "',
                '"mitgeben. Nimm ausschliesslich Werte, die "')
    return ersetze(t, "\"action='type' — tippt text.\\n\"",
                    "\"action='type' — tippt text. KOORDINATEN NIEMALS RATEN.\\n\"")


def m_voice_ohne_bezugsquelle(t):
    t = ersetze(t, '"dir gerade `find` geliefert hat oder die du in EINEM Screenshot "',
                '"dir gerade die Suche geliefert hat oder die du in EINEM Screenshot "')
    return ersetze(t, '"geratener Klick in einem fremden Fenster. Im Zweifel erst `find`. "',
                    '"geratener Klick in einem fremden Fenster. Im Zweifel erst suchen. "')


def m_voice_reihenfolge_ohne_display_n(t):
    t = ersetze(t, '"MEHRERE BILDSCHIRME: erst `screenshot` MIT display=N, dann `click` mit "',
                '"MEHRERE BILDSCHIRME: erst `screenshot` MIT Bildschirmnummer, dann `click` mit "')
    return ersetze(t, '"demselben display=N. Die Koordinaten gelten immer fuer den Bildschirm, "',
                    '"derselben Nummer. Die Koordinaten gelten immer fuer den Bildschirm, "')


def m_d_custom_kommentar_ausserhalb(t):
    # (d) image_size im Block entfernt, der gesuchte Text als Kommentar
    # AUSSERHALB des if-Blocks eingefuegt -> Test muss rot bleiben.
    t = ersetze(t, '                groesse = payload.get("image_size") or {}\n',
                '                groesse = {}\n')
    return ersetze(t, '        if action == "screenshot" and isinstance(payload, dict):\n',
                    '        # payload.get("image_size")\n'
                    '        if action == "screenshot" and isinstance(payload, dict):\n')


def m_d_custom_kommentar_innerhalb(t):
    # (d) der Ursprungs-Hinweis im Block auskommentiert — "top left" steht
    # weiterhin als Kommentar IM Block (alte Existenzpruefung bestuende).
    return ersetze(t, '                        "coordinates must be inside that, (0,0) is top left."\n',
                    '                        # "coordinates must be inside that, (0,0) is top left."\n')


def k_bridge_nummernpruefung_nach_aufnahme(t):
    # Gegenprobe des Gegenlesers: die Nummernpruefung erst NACH der Aufnahme —
    # die Meldung kommt noch, aber vorher entsteht ein Bild vom falschen Monitor.
    alt = ('    if display:\n        gewaehlt = next((b for b in bildschirme if b["number"] == int(display)), None)\n'
           '        if gewaehlt is None:\n            raise ValueError(\n'
           '                f"Bildschirm {display} gibt es nicht — verfuegbar sind "\n'
           '                f"1 bis {len(bildschirme)}."\n            )\n\n'
           '    img = (\n        _capture_macos_inprocess(gewaehlt.get("id") if gewaehlt else None)\n'
           '        if sys.platform == "darwin" else None\n    )\n')
    neu = ('    if display:\n        gewaehlt = next((b for b in bildschirme if b["number"] == int(display)), None)\n'
           '    img = (\n        _capture_macos_inprocess(gewaehlt.get("id") if gewaehlt else None)\n'
           '        if sys.platform == "darwin" else None\n    )\n'
           '    if display and gewaehlt is None:\n        raise ValueError(\n'
           '            f"Bildschirm {display} gibt es nicht — verfuegbar sind "\n'
           '            f"1 bis {len(bildschirme)}."\n        )\n')
    return ersetze(t, alt, neu)


def k_bridge_groesse_aus_liste_statt_alter_weg(t):
    # Gegenprobe des Gegenlesers: ohne Bildschirmangabe die Groesse aus der
    # Liste statt wie bisher von _logical_screen_size.
    return ersetze(t, "        logical_w, logical_h = _logical_screen_size()\n",
                   '        _h = next(b for b in bildschirme if b["primary"])\n'
                   '        logical_w, logical_h = _h["width"], _h["height"]\n')


# Kontrollmutationen: muessen rot werden, zaehlen aber NICHT fuer das Kriterium
# „paarweise verschieden" (sie treffen absichtlich dieselbe Zusicherung wie
# eine Hauptmutation aus einer anderen Richtung).
KONTROLLEN = [
    ("k_bridge_nummernpruefung_nach_aufnahme", BRIDGE, k_bridge_nummernpruefung_nach_aufnahme),
    ("k_bridge_groesse_aus_liste_statt_alter_weg", BRIDGE, k_bridge_groesse_aus_liste_statt_alter_weg),
]

MUTATIONEN = [
    ("a_list_displays_auskommentiert", BRIDGE, m_a_list_displays_auskommentiert),
    ("b_nur_coord_offset", BRIDGE, m_b_nur_coord_offset),
    ("e1_fruehausstieg_auskommentiert", BRIDGE, m_e1_fruehausstieg_auskommentiert),
    ("e2_fruehausstieg_verschachtelt", BRIDGE, m_e2_fruehausstieg_verschachtelt),
    ("bridge_image_space_plus_statt_minus", BRIDGE, m_bridge_plus_statt_minus),
    ("bridge_click_space_minus_statt_plus", BRIDGE, m_bridge_minus_statt_plus),
    ("bridge_click_space_ohne_maszstab", BRIDGE, m_bridge_ohne_maszstab),
    ("bridge_haupt_nicht_nummer_eins", BRIDGE, m_bridge_haupt_nicht_zuerst),
    ("bridge_unbekannter_bildschirm_stumm", BRIDGE, m_bridge_unbekannter_bildschirm_stumm),
    ("bridge_immer_hauptbildschirm_aufnehmen", BRIDGE, m_bridge_immer_hauptbildschirm_aufnehmen),
    ("bridge_if_display_immer_wahr", BRIDGE, m_bridge_if_display_immer_wahr),
    ("bridge_versatz_nach_screenshot_nicht_gemerkt", BRIDGE, m_bridge_versatz_nicht_gemerkt),
    ("c_sag_ihm_den_grund_auskommentiert", VOICE, m_c_sag_ihm_den_grund_weg),
    ("voice_grund_roh_mit_klammer", VOICE, m_voice_grund_roh),
    ("voice_fragt_wieder_was_er_sieht", VOICE, m_voice_fragt_wieder),
    ("voice_zweige_vertauscht", VOICE, m_voice_zweige_vertauscht),
    ("voice_bildschirmliste_schon_bei_einem", VOICE, m_voice_liste_schon_bei_einem),
    ("voice_ursprung_verschwiegen", VOICE, m_voice_ursprung_verschwiegen),
    ("voice_display_feld_umbenannt", VOICE, m_voice_display_feld_umbenannt),
    ("d_voice_verbot_im_falschen_absatz", VOICE, m_voice_verbot_im_falschen_absatz),
    ("voice_ohne_bezugsquelle_find", VOICE, m_voice_ohne_bezugsquelle),
    ("voice_reihenfolge_ohne_display_n", VOICE, m_voice_reihenfolge_ohne_display_n),
    ("d_custom_kommentar_ausserhalb_des_blocks", CUSTOM, m_d_custom_kommentar_ausserhalb),
    ("d_custom_kommentar_innerhalb_des_blocks", CUSTOM, m_d_custom_kommentar_innerhalb),
]


def main() -> int:
    print(f"Interpreter: {PY}")
    basis_rot, n = tests_laufen()
    print(f"Basislinie: {n} Tests, rot={sorted(basis_rot)}")
    if basis_rot:
        print("ABBRUCH: Basislinie nicht gruen")
        return 2

    def _mutiere(kennung, datei, mut):
        original = datei.read_text()
        try:
            datei.write_text(mut(original))
            rot, _ = tests_laufen()
        finally:
            datei.write_text(original)
        assert datei.read_text() == original, f"{datei} nicht wiederhergestellt!"
        status = "ROT" if rot else "GRUEN (Mutation ueberlebt!)"
        print(f"\n[{kennung}] -> {status}")
        for t in sorted(rot):
            print(f"    x {t}")
        return rot

    ergebnisse = {k: _mutiere(k, d, m) for k, d, m in MUTATIONEN}
    print('\n── Kontrollmutationen (nur rot, nicht im Paarvergleich) ──────────')
    kontrollen = {k: _mutiere(k, d, m) for k, d, m in KONTROLLEN}

    print("\n══ Auswertung ═══════════════════════════════════════════════════")
    alle_rot = all(ergebnisse.values())
    print(f"Mutationen: {len(ergebnisse)}, alle rot: {alle_rot}")

    paare_gleich = [
        (a, b) for i, a in enumerate(ergebnisse) for b in list(ergebnisse)[i + 1:]
        if ergebnisse[a] == ergebnisse[b]
    ]
    print(f"paarweise verschieden: {not paare_gleich}"
          + (f"  GLEICH: {paare_gleich}" if paare_gleich else ""))

    getroffen = set().union(*ergebnisse.values())
    nicht_getroffen = sorted(UMGEBAUT - getroffen)
    print(f"umgebaute Tests: {len(UMGEBAUT)}, getroffen: {len(UMGEBAUT & getroffen)}, "
          f"nicht getroffen: {nicht_getroffen or 'keine'}")
    unbekannt = sorted(t for t in getroffen if t not in UMGEBAUT)
    if unbekannt:
        print(f"zusaetzlich rot (nicht umgebaut, aber mitgetroffen): {unbekannt}")

    kontrollen_rot = all(kontrollen.values())
    print(f"Kontrollmutationen: {len(kontrollen)}, alle rot: {kontrollen_rot}")
    ok = alle_rot and not paare_gleich and not nicht_getroffen and kontrollen_rot
    print("\nGESAMT:", "BESTANDEN" if ok else "NICHT BESTANDEN")
    return 0 if ok else 1


if __name__ == "__main__":
    os.chdir(ORCH)
    sys.exit(main())

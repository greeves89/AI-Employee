"""#961: AST-Inventur statt Handliste.

``adaptiver_takt.FREMDGETAKTETE_PRAEFIXE`` wird in ``test_report_run_zeitplan``
gegen die KONKRETEN Konstanten gehalten, die der Autor des jeweiligen
Erzeugers zufaellig dort einsammelt — Issue #961 zeigte, dass das eine von
Hand gepflegte Liste ist: drei Erzeuger fehlten (``projekt_termine.PRAEFIX``,
das ``"[Meeting] "``-Literal, und ``sekretariat.PRAEFIX`` — Letzteres stand
nicht einmal im Issue-Text und kam erst durch DIESEN Scan ans Licht).

Dieser Test zaehlt die Menge stattdessen aus der Quelle: er sammelt per ``ast``
ALLE ``Schedule(...)``-Konstruktionen unter ``app/core``, ``app/services`` und
``app/api`` ein, loest ihr ``name=``-Argument so weit auf, bis ein fester
literaler Anfang feststeht, und verlangt exakte Mengenmitgliedschaft des
darin erkennbaren ``[...]``-Praefixes in ``FREMDGETAKTETE_PRAEFIXE`` — nicht
``startswith`` (das haette ``"[Projekt-Termin]"`` faelschlich als durch
``"[Projekt]"`` abgedeckt gelten lassen, obwohl ``"[Projekt]".startswith``
bei ``"[Projekt-Termin] ..."`` am 9. Zeichen scheitert).

Reine ``ast``-Mustersuche, kein ORM, keine Datenbank, kein Import der
gescannten Module (das wuerde Produktionsabhaengigkeiten in diesen Test
ziehen). Ein Erzeuger, dessen ``name=``-Ausdruck sich NICHT sicher auf ein
Literal zurueckfuehren laesst, ist ein FEHLER dieses Tests — nicht ein
stillschweigend uebersprungener Fall (vgl. Skill
``handliste-im-test-per-ast-wache-gegenrechnen``, Schritt 3.2: "lieber laut
als blind").
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

from app.core.adaptiver_takt import FREMDGETAKTETE_PRAEFIXE

APP_ROOT = Path(__file__).resolve().parents[1] / "app"

#: Ordner, in denen Schedule-Zeilen tatsaechlich angelegt werden (#961, Tabelle
#: im Issue-Text: core/*, services/scheduler_service.py, api/meeting_rooms.py
#: und api/schedules.py).
GESCANNTE_ORDNER = ("core", "services", "api")

#: ``api/schedules.py`` legt den Namen FREI nach Nutzerwunsch an — kein fester
#: Praefix-Erzeuger. Die Reservierung laeuft dort serverseitig am Eingang
#: (``fremdgetaktet(data.name)`` weist JEDEN reservierten Namen zurueck,
#: unabhaengig von ``adaptive``) statt ueber einen literalen Praefix; siehe
#: ``test_freie_erzeugung_weist_reservierte_praefixe_am_eingang_ab`` unten.
FREI_BENANNTE_ERZEUGER = {"api/schedules.py"}

#: Untergrenze gefundener Erzeuger JE Ordner (#961-Gegenlesen). Schuetzt
#: dagegen, dass ein Scan-Ausfall in einem Ordner durch einen Zugewinn in einem
#: anderen verdeckt wird — die Summe allein merkt das nicht.
MINDESTENS_JE_ORDNER = {"core": 6, "services": 1, "api": 1}

BRACKET_RE = re.compile(r"^(\[[^\]]+\])")

#: Module, aus denen ``Schedule`` bezogen werden kann. ``app.models`` steht
#: hier, weil ``app/models/__init__.py`` das Modell re-exportiert — ein
#: ``from app.models import Schedule`` ist also dieselbe Klasse (#961).
SCHEDULE_HERKUNFT = ("app.models.schedule", "app.models")


class NichtAufloesbar(Exception):
    """Der ``name=``-Ausdruck einer ``Schedule(...)``-Konstruktion liess sich
    nicht auf ein festes Literal zurueckfuehren."""


class _SchleifenVariable(Exception):
    """``name=`` referenziert eine Schleifenvariable; der Aufrufer muss sie
    ueber die aktiven Schleifenbindungen aufloesen."""

    def __init__(self, name: str):
        super().__init__(name)
        self.name = name


class _Modul:
    """Lazy geparste Datei: AST-Baum, Top-Level-String-Konstanten und
    Import-Aliasse — genug, um ``Name``- und ``Attribute``-Ausdruecke
    aufzuloesen, die auf eine ANDERE Datei verweisen (z. B.
    ``plan_rhythm.SCHEDULE_PREFIX`` innerhalb von ``eigeninitiative.py``)."""

    _cache: dict[Path, "_Modul"] = {}

    def __init__(self, pfad: Path):
        self.pfad = pfad
        quelle = pfad.read_text(encoding="utf-8")
        self.baum = ast.parse(quelle, filename=str(pfad))
        self.konstanten: dict[str, ast.expr] = {}
        self.modul_importe: dict[str, str] = {}
        self.symbol_importe: dict[str, tuple[str, str]] = {}

        # Konstanten NUR auf Modulebene -- so werden sie tatsaechlich benutzt
        # (``PRAEFIX = "..."`` am Dateianfang).
        for node in self.baum.body:
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)):
                self.konstanten[node.targets[0].id] = node.value
            elif (isinstance(node, ast.AnnAssign)
                    and isinstance(node.target, ast.Name) and node.value is not None):
                self.konstanten[node.target.id] = node.value

        # Importe dagegen ueberall im Modul einsammeln: ``sekretariat.py`` und
        # ``meeting_rooms.py`` importieren ``Schedule`` erst INNERHALB der
        # Funktion, die es konstruiert -- ein reiner Modulebene-Scan haette
        # beide Erzeuger fuer die Inventur unsichtbar gemacht.
        for node in ast.walk(self.baum):
            if isinstance(node, ast.ImportFrom):
                # #961-Gegenlesen: ``node.level == 0`` hatte relative Importe
                # (``from ..models.schedule import Schedule``) still verworfen —
                # die Datei waere damit komplett stumm uebersprungen worden.
                quelle_dotted = self._importfrom_dotted(node)
                if quelle_dotted is None:
                    continue
                for alias in node.names:
                    lokal = alias.asname or alias.name
                    self.modul_importe[lokal] = f"{quelle_dotted}.{alias.name}"
                    self.symbol_importe[lokal] = (quelle_dotted, alias.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    lokal = alias.asname or alias.name.split(".")[0]
                    self.modul_importe[lokal] = alias.name

        # #961-Gegenlesen, Befund 1: hier stand ein einzelner Name mit
        # ``break``. Zwei Folgen, beide still gruen:
        #   * ein ZWEITER Alias in derselben Datei fiel unter den Tisch
        #     (``api/schedules.py`` hat heute schon ``Schedule`` UND
        #     ``_ScheduleModel``),
        #   * ``from app.models import Schedule`` (das Paket re-exportiert es in
        #     ``app/models/__init__.py``) ergab ``dotted == "app.models"`` und
        #     damit GAR keinen Namen -> die ganze Datei wurde uebersprungen.
        # Darum jetzt eine Menge, und beide Herkunftsmodule gelten.
        self.schedule_lokalnamen: set[str] = {
            lokal for lokal, (dotted, original) in self.symbol_importe.items()
            if original == "Schedule" and dotted in SCHEDULE_HERKUNFT
        }
        # Namen, ueber die ``Schedule`` als ATTRIBUT erreichbar ist
        # (``import app.models.schedule`` -> ``app.models.schedule.Schedule(...)``,
        # ``import app.models.schedule as sched`` -> ``sched.Schedule(...)``).
        self.schedule_modulnamen: set[str] = {
            lokal for lokal, dotted in self.modul_importe.items()
            if dotted in SCHEDULE_HERKUNFT
        }

    def _importfrom_dotted(self, node: ast.ImportFrom) -> str | None:
        """Gepunkteter Modulname eines ``from ... import ...`` — auch relativ."""
        if node.level == 0:
            return node.module
        try:
            eigene = self.pfad.resolve().relative_to(APP_ROOT.resolve())
        except ValueError:
            return None
        paket = ["app", *eigene.parts[:-1]]
        if node.level - 1 > len(paket):
            return None
        basis = paket[:len(paket) - (node.level - 1)]
        if not basis:
            return None
        return ".".join(basis + ([node.module] if node.module else []))

    @classmethod
    def laden(cls, pfad: Path) -> "_Modul":
        pfad = pfad.resolve()
        if pfad not in cls._cache:
            cls._cache[pfad] = cls(pfad)
        return cls._cache[pfad]


def _dotted_zu_datei(dotted: str) -> Path:
    if not dotted.startswith("app."):
        raise NichtAufloesbar(f"Modul ausserhalb von app/: {dotted!r}")
    pfad = APP_ROOT.joinpath(*dotted.split(".")[1:]).with_suffix(".py")
    if not pfad.is_file():
        raise NichtAufloesbar(f"kein Modul unter {pfad}")
    return pfad


def _fuehrendes_literal(expr: ast.expr, modul: _Modul, tiefe: int = 0) -> str:
    """Loest ``expr`` so weit auf, bis ein fester literaler Textanfang
    feststeht — genug, um ein fuehrendes ``[...]``-Praefix zu erkennen.
    Schlaegt LAUT fehl (``NichtAufloesbar`` / ``_SchleifenVariable``), wenn
    das nicht sicher geht."""
    if tiefe > 6:
        raise NichtAufloesbar("zu tiefe Verschachtelung beim Aufloesen")

    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return expr.value

    if isinstance(expr, ast.JoinedStr):
        if not expr.values:
            raise NichtAufloesbar("leerer f-String")
        erstes = expr.values[0]
        if isinstance(erstes, ast.Constant) and isinstance(erstes.value, str):
            return erstes.value
        if isinstance(erstes, ast.FormattedValue):
            return _fuehrendes_literal(erstes.value, modul, tiefe + 1)
        raise NichtAufloesbar(f"f-String-Segment nicht lesbar: {ast.dump(erstes)}")

    if isinstance(expr, ast.Name):
        if expr.id in modul.konstanten:
            return _fuehrendes_literal(modul.konstanten[expr.id], modul, tiefe + 1)
        if expr.id in modul.symbol_importe:
            dotted, original = modul.symbol_importe[expr.id]
            fremdmodul = _Modul.laden(_dotted_zu_datei(dotted))
            if original not in fremdmodul.konstanten:
                raise NichtAufloesbar(f"{original!r} nicht als Konstante in {dotted} gefunden")
            return _fuehrendes_literal(fremdmodul.konstanten[original], fremdmodul, tiefe + 1)
        raise _SchleifenVariable(expr.id)

    if isinstance(expr, ast.Attribute) and isinstance(expr.value, ast.Name):
        modulname = expr.value.id
        dotted = modul.modul_importe.get(modulname)
        if dotted is None:
            raise NichtAufloesbar(f"{modulname!r} ist kein bekannter Modul-Import")
        fremdmodul = _Modul.laden(_dotted_zu_datei(dotted))
        if expr.attr not in fremdmodul.konstanten:
            raise NichtAufloesbar(f"{expr.attr!r} nicht als Konstante in {dotted} gefunden")
        return _fuehrendes_literal(fremdmodul.konstanten[expr.attr], fremdmodul, tiefe + 1)

    if isinstance(expr, ast.Subscript):
        # z. B. ``f"{PRAEFIX}{team.name}"[:200]`` — das Abschneiden aendert
        # nichts am fuehrenden Praefix, solange es lang genug ist (es ist es:
        # jedes Praefix hier ist deutlich kuerzer als 200 Zeichen).
        return _fuehrendes_literal(expr.value, modul, tiefe + 1)

    raise NichtAufloesbar(f"Ausdrucksform nicht unterstuetzt: {ast.dump(expr)}")


def _schleifenbindung(target: ast.expr, iter_node: ast.expr) -> dict[str, list[ast.expr]]:
    """``for name, cron in ((lit_a, c1), (lit_b, c2)):`` -> ``{"name": [lit_a,
    lit_b], "cron": [c1, c2]}``. Nicht sicher aufschluesselbare Schleifen
    (z. B. ``for x in irgendeine_funktion():``) liefern ``{}`` -- die
    Variable bleibt dann ungebunden und loest bei Benutzung laut auf."""
    if isinstance(target, ast.Name):
        if not isinstance(iter_node, (ast.Tuple, ast.List)):
            return {}
        return {target.id: list(iter_node.elts)}
    if isinstance(target, ast.Tuple) and all(isinstance(e, ast.Name) for e in target.elts):
        if not isinstance(iter_node, (ast.Tuple, ast.List)):
            return {}
        namen = [e.id for e in target.elts]
        bindung: dict[str, list[ast.expr]] = {n: [] for n in namen}
        for element in iter_node.elts:
            if not isinstance(element, (ast.Tuple, ast.List)) or len(element.elts) != len(namen):
                return {}
            for n, wert in zip(namen, element.elts):
                bindung[n].append(wert)
        return bindung
    return {}


def _punktname(expr: ast.expr) -> str | None:
    """``app.models.schedule`` aus einer Attribute-/Name-Kette."""
    teile: list[str] = []
    while isinstance(expr, ast.Attribute):
        teile.append(expr.attr)
        expr = expr.value
    if not isinstance(expr, ast.Name):
        return None
    teile.append(expr.id)
    return ".".join(reversed(teile))


def _ist_schedule_konstruktor(node: ast.Call, modul: _Modul) -> bool:
    if isinstance(node.func, ast.Name):
        return node.func.id in modul.schedule_lokalnamen
    # #961-Gegenlesen, Befund 1: ``ast.Attribute`` wurde nie geprueft, also
    # blieb ``app.models.schedule.Schedule(...)`` unsichtbar.
    if isinstance(node.func, ast.Attribute) and node.func.attr == "Schedule":
        basis = _punktname(node.func.value)
        if basis is None:
            return False
        if basis in modul.schedule_modulnamen or basis in SCHEDULE_HERKUNFT:
            return True
        # ``import app`` + ``app.models.schedule.Schedule(...)``
        wurzel = basis.split(".")[0]
        if modul.modul_importe.get(wurzel) == wurzel and basis in SCHEDULE_HERKUNFT:
            return True
    return False


def _loese_mit_schleifen(expr: ast.expr, modul: _Modul,
                          aktive_schleifen: dict[str, list[ast.expr]]) -> str:
    try:
        return _fuehrendes_literal(expr, modul)
    except _SchleifenVariable as schleife:
        kandidaten = aktive_schleifen.get(schleife.name)
        if not kandidaten:
            raise NichtAufloesbar(f"Schleifenvariable {schleife.name!r} nicht gebunden") from schleife
        # #961-Gegenlesen, Befund 2: hier wurden die VOLLEN Literale
        # verglichen. Eine voellig legitime Schleife
        #   for nm in ("[Projekt] Daily", "[Projekt] Weekly")
        # galt damit als "uneinheitlich" und faerbte die Wache rot, obwohl
        # beide dasselbe Praefix tragen. Verglichen wird das ``[..]``-Praefix.
        praefixe: set[str] = set()
        for kand in kandidaten:
            literal = _fuehrendes_literal(kand, modul).strip()
            treffer = BRACKET_RE.match(literal)
            if not treffer:
                raise NichtAufloesbar(
                    f"Schleife ueber {schleife.name!r}: kein fuehrendes "
                    f"[..]-Praefix erkennbar in {literal!r}"
                ) from schleife
            praefixe.add(treffer.group(1))
        if len(praefixe) != 1:
            raise NichtAufloesbar(
                f"Schleife ueber {schleife.name!r} liefert uneinheitliche "
                f"Praefixe: {sorted(praefixe)}"
            ) from schleife
        return next(iter(praefixe))


def _inventur() -> tuple[list[tuple[str, str]], list[str]]:
    """Gibt (gefundene (stelle, praefix)-Paare, Fehlermeldungen) zurueck."""
    gefunden: list[tuple[str, str]] = []
    fehler: list[str] = []

    def _besuche(baum, modul: _Modul, relpfad: str, aktive_schleifen: dict[str, list[ast.expr]]):
        for node in ast.iter_child_nodes(baum):
            schleifen_fuer_kinder = aktive_schleifen
            if isinstance(node, ast.For):
                neue_bindung = _schleifenbindung(node.target, node.iter)
                if neue_bindung:
                    schleifen_fuer_kinder = {**aktive_schleifen, **neue_bindung}
            if isinstance(node, ast.Call) and _ist_schedule_konstruktor(node, modul):
                name_kw = next((kw for kw in node.keywords if kw.arg == "name"), None)
                stelle = f"{relpfad}:{node.lineno}"
                if name_kw is None:
                    fehler.append(f"{stelle}: Schedule(...) ohne name=-Argument")
                else:
                    try:
                        literal = _loese_mit_schleifen(name_kw.value, modul, aktive_schleifen)
                    except (NichtAufloesbar, _SchleifenVariable) as fe:
                        fehler.append(f"{stelle}: {fe}")
                    else:
                        match = BRACKET_RE.match(literal.strip())
                        if not match:
                            fehler.append(
                                f"{stelle}: kein fuehrendes [..]-Praefix erkennbar in {literal!r}"
                            )
                        else:
                            gefunden.append((stelle, match.group(1)))
            _besuche(node, modul, relpfad, schleifen_fuer_kinder)

    for ordner in GESCANNTE_ORDNER:
        wurzel = APP_ROOT / ordner
        if not wurzel.is_dir():
            continue
        for pfad in sorted(wurzel.rglob("*.py")):
            relpfad = str(pfad.relative_to(APP_ROOT))
            if relpfad in FREI_BENANNTE_ERZEUGER:
                continue
            modul = _Modul.laden(pfad)
            # Kein Vorab-Ausschluss mehr anhand eines einzelnen Importnamens
            # (#961-Gegenlesen, Befund 1): genau dieses ``continue`` hat eine
            # Datei mit ``from app.models import Schedule`` komplett stumm
            # uebersprungen. Ueber Erzeuger entscheidet jetzt allein
            # ``_ist_schedule_konstruktor``.
            _besuche(modul.baum, modul, relpfad, {})

    return gefunden, fehler


class SchedulePraefixInventur(unittest.TestCase):

    def test_alle_schedule_erzeuger_sind_in_der_sperrliste_erfasst(self):
        gefunden, fehler = _inventur()

        self.assertEqual(
            fehler, [],
            "Nicht lesbare Schedule(...)-Erzeugung(en) — die Wache kann hier "
            "keine Aussage treffen, das zaehlt als Fehler, nicht als OK: "
            + "; ".join(fehler),
        )
        # Vorbedingung: der Scan selbst muss etwas finden, sonst waere ein
        # blind gruener Test (z. B. durch einen Pfadfehler) nicht von einer
        # echten Vollstaendigkeit zu unterscheiden.
        self.assertGreaterEqual(
            len(gefunden), 8,
            f"Nur {len(gefunden)} Schedule(...)-Erzeuger gefunden — Scan vermutlich kaputt: {gefunden}",
        )
        # #961-Gegenlesen, Befund 1: der Gesamtboden allein genuegt nicht. Faellt
        # der Scan fuer EINEN Ordner aus, waehrend ein anderer dazugewinnt,
        # bleibt die Summe stehen und der Test still gruen. Darum je Ordner ein
        # eigener Boden — er darf nur sinken, wenn jemand wirklich einen
        # Erzeuger entfernt, und dann soll er ihn hier bewusst nachziehen.
        je_ordner: dict[str, int] = {}
        for stelle, _praefix in gefunden:
            je_ordner[stelle.split("/")[0]] = je_ordner.get(stelle.split("/")[0], 0) + 1
        for ordner, boden in MINDESTENS_JE_ORDNER.items():
            self.assertGreaterEqual(
                je_ordner.get(ordner, 0), boden,
                f"Nur {je_ordner.get(ordner, 0)} Erzeuger unter {ordner}/ gefunden "
                f"(erwartet mindestens {boden}) — der Scan sieht diesen Ordner "
                f"vermutlich nicht mehr: {sorted(je_ordner.items())}",
            )

        gesperrt = {p.strip() for p in FREMDGETAKTETE_PRAEFIXE}
        fehlend = sorted(
            f"{stelle} -> {praefix}" for stelle, praefix in gefunden if praefix not in gesperrt
        )
        self.assertEqual(
            fehlend, [],
            "Diese Schedule(...)-Erzeuger fuehren ein Praefix, das "
            "FREMDGETAKTETE_PRAEFIXE nicht kennt (#961): " + "; ".join(fehlend),
        )

    def test_freie_erzeugung_weist_reservierte_praefixe_am_eingang_ab(self):
        """``api/schedules.py`` ist bewusst von der Inventur ausgenommen
        (``FREI_BENANNTE_ERZEUGER``) — die Gegenprobe dafuer: der Endpunkt
        muss reservierte Namen selbst zurueckweisen, sonst waere die
        Ausnahme ein Schlupfloch statt einer bewussten Entscheidung.

        #961-Gegenlesen, Befund 3: vorher stand hier ein Vorkommens-Grep
        ("irgendwo in der Datei wird ``fremdgetaktet`` aufgerufen"). Der blieb
        GRUEN, wenn man die Sperre im Anlege-Pfad loescht — die drei Aufrufe in
        den anderen Endpunkten hielten ihn am Leben. Geprueft wird deshalb JE
        ENDPUNKT, in dessen Rumpf die Sperre stehen muss."""
        quelltext = (APP_ROOT / "api" / "schedules.py").read_text(encoding="utf-8")
        baum = ast.parse(quelltext)
        funktionen = {
            node.name: node for node in ast.walk(baum)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

        def _sperrformen(fn) -> set[tuple[int, bool]]:
            """Beschreibt jede ``if``-Sperre, die ``fremdgetaktet`` befragt, als
            (Anzahl der Aufrufe in der Bedingung, haengt sie an ``adaptive``).

            Eine reine Zaehlung der Aufrufe im Funktionsrumpf reicht NICHT: in
            ``create_schedule`` stehen zwei Sperren mit verschiedener Aufgabe
            (eine nur fuer ``adaptive``, eine unabhaengig davon). Loescht man die
            zweite, bleibt die erste und eine Existenzpruefung waere weiter
            gruen — genau das ist in der Gegenprobe passiert."""
            formen: set[tuple[int, bool]] = set()
            for knoten in ast.walk(fn):
                if not isinstance(knoten, ast.If):
                    continue
                aufrufe = [
                    k for k in ast.walk(knoten.test)
                    if isinstance(k, ast.Call) and isinstance(k.func, ast.Name)
                    and k.func.id == "fremdgetaktet"
                ]
                if not aufrufe:
                    continue
                adaptiv = any(
                    isinstance(k, ast.Attribute) and k.attr == "adaptive"
                    for k in ast.walk(knoten.test)
                )
                formen.add((len(aufrufe), adaptiv))
            return formen

        #: Je Endpunkt die Sperren, die dort stehen MUESSEN — als Form, damit
        #: jede einzeln geloeschte Sperre diesen Test rot faerbt.
        erwartet = {
            "create_schedule": {
                # „Praefix ist reserviert, unabhaengig von adaptive"
                (1, False),
                # „adaptiver Takt nicht auf fremdgetakteten Namen"
                (1, True),
            },
            "update_schedule": {
                # Umbenennen: alter ODER neuer Name reserviert -> zwei Aufrufe
                (2, False),
                (1, True),
            },
        }
        for name, formen in erwartet.items():
            with self.subTest(endpunkt=name):
                self.assertIn(
                    name, funktionen,
                    f"Endpunkt {name} existiert nicht mehr — diese Gegenprobe "
                    "muss dann auf den Nachfolger gezogen werden",
                )
                vorhanden = _sperrformen(funktionen[name])
                self.assertTrue(
                    formen <= vorhanden,
                    f"{name}(): Sperre(n) fehlen. Erwartet (Aufrufe, an adaptive "
                    f"gebunden): {sorted(formen)} — gefunden: {sorted(vorhanden)}. "
                    "Ohne sie nimmt die freie Zeitplan-Erzeugung wieder "
                    "reservierte Namen an.",
                )

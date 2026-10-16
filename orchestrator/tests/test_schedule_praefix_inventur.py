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

BRACKET_RE = re.compile(r"^(\[[^\]]+\])")


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
            if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                for alias in node.names:
                    lokal = alias.asname or alias.name
                    self.modul_importe[lokal] = f"{node.module}.{alias.name}"
                    self.symbol_importe[lokal] = (node.module, alias.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    lokal = alias.asname or alias.name.split(".")[0]
                    self.modul_importe[lokal] = alias.name

        self.schedule_lokalname: str | None = None
        for lokal, (dotted, original) in self.symbol_importe.items():
            if dotted == "app.models.schedule" and original == "Schedule":
                self.schedule_lokalname = lokal
                break

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


def _ist_schedule_konstruktor(node: ast.Call, modul: _Modul) -> bool:
    return (modul.schedule_lokalname is not None
            and isinstance(node.func, ast.Name)
            and node.func.id == modul.schedule_lokalname)


def _loese_mit_schleifen(expr: ast.expr, modul: _Modul,
                          aktive_schleifen: dict[str, list[ast.expr]]) -> str:
    try:
        return _fuehrendes_literal(expr, modul)
    except _SchleifenVariable as schleife:
        kandidaten = aktive_schleifen.get(schleife.name)
        if not kandidaten:
            raise NichtAufloesbar(f"Schleifenvariable {schleife.name!r} nicht gebunden") from schleife
        literale = {_fuehrendes_literal(kand, modul).strip() for kand in kandidaten}
        if len(literale) != 1:
            raise NichtAufloesbar(
                f"Schleife ueber {schleife.name!r} liefert uneinheitliche Praefixe: {literale}"
            ) from schleife
        return next(iter(literale))


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
            if modul.schedule_lokalname is None:
                continue
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
        Ausnahme ein Schlupfloch statt einer bewussten Entscheidung."""
        quelltext = (APP_ROOT / "api" / "schedules.py").read_text(encoding="utf-8")
        baum = ast.parse(quelltext)
        aufrufe = [
            node for node in ast.walk(baum)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "fremdgetaktet"
        ]
        self.assertTrue(
            aufrufe,
            "api/schedules.py ruft fremdgetaktet() nicht mehr auf — die freie "
            "Zeitplan-Erzeugung koennte wieder reservierte Namen annehmen",
        )

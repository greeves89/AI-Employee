"""Wache fuer die von Hand gepflegten Markerlisten des Postgres-Migrationstests.

``test_migrationen_gegen_echtes_postgres.py`` baut den Zustand einer
GEWACHSENEN Anlage nach: Schema aus den Modellen anlegen, dann genau das
wieder entfernen, was eine Migration NACH der Marke erst anlegt. Welche
Tabellen und Spalten das sind, steht dort in zwei Listen von Hand.

**Die Fehlerklasse, die diese Wache schliesst:** Wer eine Migration ergaenzt
und den Eintrag vergisst, bekommt keinen roten Test. Die Migrationen benutzen
``ADD COLUMN IF NOT EXISTS`` — die Spalte steht aus ``create_all`` schon da,
das ``ADD COLUMN`` ist ein folgenloser Treffer, und der Test sagt ueber diese
Spalte nichts aus. Genau so lagen am 16.10.2026 SECHS Spalten aus drei
Migrationen unbemerkt ausserhalb der Pruefung (Issue #949) — die aelteste
seit zwei Wochen.

**Warum ohne Datenbank und ohne SQLAlchemy:** Der Fehler ist im Postgres-Lauf
unsichtbar (dort bleibt alles gruen), und ein Test, der nur in der CI laufen
kann, meldet ihn erst Stunden spaeter. Hier wird deshalb rein ueber den
Quelltext geurteilt — das laeuft auf jedem Rechner in Millisekunden.

**Lieber laut als blind:** Trifft die Wache eine Schreibweise, die sie nicht
sicher lesen kann (ein ``ADD``-Ausdruck ohne erkennbaren Spaltennamen, ein
``add_column`` ohne zuordenbare Tabelle), meldet sie DAS als Fehler, statt die
Stelle stumm zu ueberspringen. Ein uebersehener Eintrag ist genau die
Fehlerklasse, die es hier zu verhindern gilt.
"""

import ast
import os
import re
import unittest

# Die Parser fuer f-Strings in Literal-Schleifen und fuer ``op.add_column``
# stehen schon in der Nachbarwache und sind dort an echten Migrationen
# gewachsen (ohne sie "fehlen acht Spalten"). Zweimal denselben Parser zu
# pflegen heisst, dass einer von beiden hinterherhinkt.
from tests.test_migration_kollidiert_nicht_mit_startpfad import (
    _CREATE_TABLE,
    _fstring_varianten,
    _harte_add_columns,
    _schleifenwerte,
)

_HIER = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.dirname(_HIER)                      # orchestrator/
_VERSIONEN = os.path.join(_APP_DIR, "alembic", "versions")
_MODELLE = os.path.join(_APP_DIR, "app", "models")
_TESTDATEI = os.path.join(_HIER, "test_migrationen_gegen_echtes_postgres.py")

#: Spalten, die der Postgres-Test NICHT ueber ``_SPALTEN_NACH_DER_MARKE``,
#: sondern mit einer eigenen Zeile entfernt — sie ist der Anlass des Tests.
_EIGENE_ZEILE_IM_TEST = {("agents", "access_policy")}

_RE_ALTER = re.compile(r"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(\w+)", re.I)
_RE_ADD_SPALTE = re.compile(r"ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)", re.I)
#: ``ADD`` in einer Tabellenaenderung, das KEINE Spalte anlegt.
_RE_ADD_SONST = re.compile(
    r"ADD\s+(?:COLUMN\b|CONSTRAINT\b|PRIMARY\b|FOREIGN\b|UNIQUE\b|CHECK\b|EXCLUDE\b|GENERATED\b)",
    re.I)
#: Platzhalter, die ``_fstring_varianten`` fuer einen unbekannten Ausdruck
#: einsetzt. Ein SQL-Text, der an der Stelle des Tabellen- oder Spaltennamens
#: einen solchen traegt, ist NICHT lesbar — und wird gemeldet, nicht verworfen.
_RE_UNBEKANNTE_TABELLE = re.compile(r"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?\?", re.I)
_RE_UNBEKANNTE_SPALTE = re.compile(r"ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?\?", re.I)


def _modul(pfad):
    with open(pfad, encoding="utf-8") as f:
        return ast.parse(f.read(), filename=pfad)


def _namen(knoten):
    """Zuweisungsziele eines ``Assign``/``AnnAssign`` als Namensliste.

    Beide Formen kommen vor: die Vorlage ``alembic/script.py.mako`` erzeugt
    ``revision: str = "..."`` (annotiert), aeltere Revisionen und die
    Testdatei schreiben ``revision = "..."``. Wer nur ``Assign`` liest, sieht
    ein Drittel der Kette nicht — und merkt es nicht.
    """
    if isinstance(knoten, ast.AnnAssign):
        return [knoten.target.id] if isinstance(knoten.target, ast.Name) else []
    if isinstance(knoten, ast.Assign):
        return [z.id for z in knoten.targets if isinstance(z, ast.Name)]
    return []


def _konstanten_des_postgres_tests():
    """Liest die drei Vorgaben aus der Testdatei, ohne sie zu importieren.

    Importieren wuerde SQLAlchemy voraussetzen; dann liefe diese Wache nur
    dort, wo auch der Postgres-Test laeuft — und waere nutzlos.
    """
    pflicht = {"_REVISION_DAVOR", "_TABELLEN_NACH_DER_MARKE", "_SPALTEN_NACH_DER_MARKE"}
    gefunden = {}
    for knoten in _modul(_TESTDATEI).body:
        for name in _namen(knoten):
            if name in pflicht:
                gefunden[name] = ast.literal_eval(knoten.value)
    fehlt = pflicht - set(gefunden)
    if fehlt:
        raise AssertionError(
            f"{os.path.basename(_TESTDATEI)}: {sorted(fehlt)} nicht mehr auf Modulebene "
            "gefunden. Umbenannt oder verschoben? Dann gehoert diese Wache mit "
            "angepasst — ohne sie prueft niemand mehr, ob die Listen vollstaendig sind."
        )
    return gefunden


def _migrationsdateien():
    return sorted(n for n in os.listdir(_VERSIONEN)
                  if n.endswith(".py") and not n.startswith("__"))


def _revisionen():
    """revision -> (datei, Liste der Vorgaenger)."""
    aus = {}
    for name in _migrationsdateien():
        rev = vorher = None
        for knoten in _modul(os.path.join(_VERSIONEN, name)).body:
            ziele = _namen(knoten)
            if "revision" in ziele:
                rev = ast.literal_eval(knoten.value)
            elif "down_revision" in ziele:
                vorher = ast.literal_eval(knoten.value)
        if rev is None:
            continue
        if vorher is None:
            vorher = []
        elif isinstance(vorher, str):
            vorher = [vorher]
        aus[rev] = (name, list(vorher))
    return aus


def _verwandte(revisionen, start, rueckwaerts=False):
    """Alle Revisionen, die (transitiv) nach ``start`` kommen — oder davor."""
    kanten = {}
    for rev, (_, vorgaenger) in revisionen.items():
        for v in vorgaenger:
            if rueckwaerts:
                kanten.setdefault(rev, []).append(v)
            else:
                kanten.setdefault(v, []).append(rev)
    gesehen, offen = set(), [start]
    while offen:
        for nachbar in kanten.get(offen.pop(), []):
            if nachbar not in gesehen:
                gesehen.add(nachbar)
                offen.append(nachbar)
    return gesehen


def _was_die_migration_anlegt(pfad):
    """(Spaltenpaare, Tabellen, unlesbare Stellen), die ``upgrade()`` anlegt.

    Nur ``upgrade()``: ein ``ADD COLUMN`` im ``downgrade()`` legt beim
    Vorwaertslauf nichts an und gehoert in keine Liste.
    """
    with open(pfad, encoding="utf-8") as f:
        quelle = f.read()
    baum = ast.parse(quelle, filename=pfad)
    schleifen = _schleifenwerte(baum)
    spalten, tabellen, unlesbar = set(), set(), []

    def _sammle_sql(text):
        # Ein f-String, dessen Platzhalter die Wache nicht ausmultiplizieren
        # kann (z.B. Schleife ueber eine Modulkonstante), landet hier mit "?"
        # an der Stelle des Namens. Stillschweigend fallen lassen hiesse: die
        # Spalte ist unsichtbar — also laut melden.
        for treffer in (_RE_UNBEKANNTE_TABELLE.search(text),
                        _RE_UNBEKANNTE_SPALTE.search(text)):
            if treffer:
                unlesbar.append(
                    "Tabellen- oder Spaltenname aus einem nicht auflösbaren "
                    f"Ausdruck: …{text[max(0, treffer.start() - 20):treffer.end() + 20].strip()}…")
        for tabelle, rest in _teile_nach_alter_table(text):
            treffer = _RE_ADD_SPALTE.findall(rest)
            for spalte in treffer:
                spalten.add((tabelle, spalte.lower()))
            # Ein ``ADD``, das die Wache nicht einordnen kann, wird gemeldet —
            # nicht uebersprungen.
            for stueck in re.finditer(r"\bADD\b", rest, re.I):
                if not _RE_ADD_SONST.match(rest[stueck.start():]):
                    unlesbar.append(
                        f"nicht lesbare Tabellenaenderung an {tabelle}: "
                        f"…{rest[stueck.start():stueck.start() + 60].strip()}…")
        for tabelle in _CREATE_TABLE.findall(text):
            tabellen.add(tabelle.lower())

    def _gehe(knoten, bindungen, in_batch=None):
        if isinstance(knoten, ast.Constant) and isinstance(knoten.value, str):
            _sammle_sql(knoten.value)
            return
        if isinstance(knoten, ast.JoinedStr):
            for variante in _fstring_varianten(knoten, bindungen):
                _sammle_sql(variante)
            return
        if isinstance(knoten, ast.For) and id(knoten) in schleifen:
            bindungen = {**bindungen, **schleifen[id(knoten)]}
        if isinstance(knoten, ast.With):
            in_batch = _batch_tabelle(knoten) or in_batch
        if isinstance(knoten, ast.Call) and isinstance(knoten.func, ast.Attribute):
            if knoten.func.attr == "create_table" and knoten.args:
                erst = knoten.args[0]
                if isinstance(erst, ast.Constant) and isinstance(erst.value, str):
                    tabellen.add(erst.value.lower())
            if knoten.func.attr == "add_column" and len(knoten.args) == 1:
                # ``with op.batch_alter_table("t") as b: b.add_column(sa.Column("s"))``
                spalte = _spaltenname(knoten.args[0])
                if in_batch and spalte:
                    spalten.add((in_batch, spalte))
                else:
                    unlesbar.append(
                        "add_column ohne zuordenbare Tabelle "
                        f"(Zeile {getattr(knoten, 'lineno', '?')})")
        for kind in ast.iter_child_nodes(knoten):
            _gehe(kind, bindungen, in_batch)

    for fn in (k for k in ast.walk(baum)
               if isinstance(k, ast.FunctionDef) and k.name == "upgrade"):
        _gehe(fn, {})
    spalten |= _harte_add_columns(quelle)      # op.add_column("t", sa.Column("s", ..))
    return spalten, tabellen, unlesbar


def _teile_nach_alter_table(text):
    """(tabelle, Rest der Anweisung) je ``ALTER TABLE`` in ``text``.

    Ein ``ALTER TABLE`` darf mehrere ``ADD COLUMN`` tragen; der Rest wird
    deshalb bis zum Anweisungsende (``;``) oder zum naechsten ``ALTER TABLE``
    mitgegeben, nicht nur das erste Vorkommen gelesen.
    """
    aus = []
    treffer = list(_RE_ALTER.finditer(text))
    for i, t in enumerate(treffer):
        ende = treffer[i + 1].start() if i + 1 < len(treffer) else len(text)
        rest = text[t.end():ende].split(";")[0]
        aus.append((t.group(1).lower(), rest))
    return aus


def _batch_tabelle(knoten):
    """Tabellenname eines ``with op.batch_alter_table("t") …``."""
    for eintrag in knoten.items:
        ausdruck = eintrag.context_expr
        if (isinstance(ausdruck, ast.Call) and isinstance(ausdruck.func, ast.Attribute)
                and ausdruck.func.attr == "batch_alter_table" and ausdruck.args
                and isinstance(ausdruck.args[0], ast.Constant)
                and isinstance(ausdruck.args[0].value, str)):
            return ausdruck.args[0].value.lower()
    return None


def _spaltenname(knoten):
    if isinstance(knoten, ast.Call) and knoten.args:
        erst = knoten.args[0]
        if isinstance(erst, ast.Constant) and isinstance(erst.value, str):
            return erst.value.lower()
    return None


def _ist_spalte(func):
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
    return name in ("mapped_column", "Column")


def _modelltabellen():
    """Tabellenname -> Menge der Spaltennamen, aus den Modellen gelesen.

    Spalten erbender Mixins (``TimestampMixin``) kommen mit; sonst wuerde eine
    Migration an ``created_at`` fuer eine Luecke gehalten.
    """
    klassen = {}   # Klassenname -> (tabellenname|None, {spalten}, [basisnamen])
    for name in sorted(os.listdir(_MODELLE)):
        if not name.endswith(".py"):
            continue
        for knoten in _modul(os.path.join(_MODELLE, name)).body:
            if not isinstance(knoten, ast.ClassDef):
                continue
            tabelle, spalten = None, set()
            for eintrag in knoten.body:
                ziele = _namen(eintrag)
                if "__tablename__" in ziele:
                    # Ein berechneter Name waere kein Fehler dieser Wache —
                    # sie darf daran nur nicht abstuerzen.
                    if isinstance(eintrag.value, ast.Constant) \
                            and isinstance(eintrag.value.value, str):
                        tabelle = eintrag.value.value
                if not ziele or ziele[0].startswith("__"):
                    continue
                wert = eintrag.value
                if not (isinstance(wert, ast.Call) and _ist_spalte(wert.func)):
                    continue
                # ``mapped_column("metadata", …)`` benennt die Spalte anders
                # als das Attribut.
                name_aus_aufruf = _spaltenname(wert) if wert.args else None
                spalten.add(name_aus_aufruf or ziele[0].rstrip("_").lower())
            basen = [b.id for b in knoten.bases if isinstance(b, ast.Name)]
            klassen[knoten.name] = (tabelle, spalten, basen)

    def alle_spalten(klassenname, tiefe=0):
        if klassenname not in klassen or tiefe > 10:
            return set()
        _, spalten, basen = klassen[klassenname]
        aus = set(spalten)
        for b in basen:
            aus |= alle_spalten(b, tiefe + 1)
        return aus

    return {t: alle_spalten(k) for k, (t, _, _) in klassen.items() if t}


class MarkerlistenSindVollstaendig(unittest.TestCase):
    """Jede Spalte/Tabelle, die eine Migration nach der Marke anlegt und die in
    den Modellen steht, muss in der passenden Liste stehen."""

    @classmethod
    def setUpClass(cls):
        cls.vorgaben = _konstanten_des_postgres_tests()
        cls.revisionen = _revisionen()
        cls.marke = cls.vorgaben["_REVISION_DAVOR"]
        cls.spaet = _verwandte(cls.revisionen, cls.marke)
        cls.modelle = _modelltabellen()

    # --- Vorbedingungen: kann diese Messung den Fehler ueberhaupt zeigen? ---

    def test_jede_migrationsdatei_wurde_gelesen(self):
        """Ohne diese Zahl bleibt eine Parser-Luecke unsichtbar.

        Die Kette reisst an der ersten Revision, die die Wache nicht liest —
        und ALLES dahinter wird dann fuer „vor der Marke" gehalten. Genau so
        war sie zuerst blind fuer die annotierte Form der Alembic-Vorlage.
        """
        dateien = _migrationsdateien()
        self.assertEqual(
            len(self.revisionen), len(dateien),
            f"{len(dateien) - len(self.revisionen)} Migrationsdatei(en) ohne "
            "erkannte 'revision'-Zuweisung — die Wache sieht die Kette "
            "dahinter nicht mehr.")

    def test_die_marke_liegt_auf_der_hauptlinie(self):
        """Der Postgres-Test stempelt auf die Marke und fahrt ``upgrade heads``.

        Damit laeuft dort alles, was NICHT Vorfahre der Marke ist. Diese Wache
        sieht die Nachfahren. Beides deckt sich nur, solange die Marke auf
        einer einzigen Linie sitzt — sonst liefen Migrationen ungeprueft mit.
        """
        self.assertIn(self.marke, self.revisionen,
                      "_REVISION_DAVOR zeigt auf keine vorhandene Migration — der "
                      "Postgres-Test stempelt auf eine Revision, die es nicht gibt.")
        vorfahren = _verwandte(self.revisionen, self.marke, rueckwaerts=True)
        unbeteiligt = set(self.revisionen) - vorfahren - self.spaet - {self.marke}
        self.assertEqual(
            set(), unbeteiligt,
            "Diese Revisionen sind weder Vorfahre noch Nachfahre der Marke. Der "
            "Postgres-Test fuehrt sie aus, diese Wache prueft sie nicht: "
            f"{sorted(unbeteiligt)}")
        self.assertGreater(len(self.spaet), 0, "Keine Migration nach der Marke.")

    def test_modelle_werden_gelesen(self):
        for pflicht in ("tasks", "teams", "users", "agents"):
            self.assertIn(pflicht, self.modelle,
                          "Modelle nicht gelesen — die Wache wuerde jede fehlende "
                          "Spalte fuer 'nicht im Modell' halten.")
        self.assertIn("farbe", self.modelle["teams"],
                      "Spalten der Modelle nicht erkannt (teams.farbe fehlt).")
        self.assertIn("metadata", self.modelle["tasks"],
                      "Ausdruecklich benannte Spalten nicht erkannt "
                      "(tasks.metadata_ heisst in der Datenbank 'metadata').")

    def test_keine_unlesbare_stelle_in_den_migrationen(self):
        """Lieber laut als blind — siehe Modulkopf."""
        funde = []
        for rev in sorted(self.spaet):
            datei, _ = self.revisionen[rev]
            *_, unlesbar = _was_die_migration_anlegt(os.path.join(_VERSIONEN, datei))
            funde += [f"{datei}: {u}" for u in unlesbar]
        self.assertEqual([], funde,
                         "Diese Stellen kann die Wache nicht sicher lesen; sie "
                         "koennte eine Spalte uebersehen:\n  " + "\n  ".join(funde))

    # --- Die eigentliche Pruefung ------------------------------------------

    def test_jede_spaeter_angelegte_spalte_steht_in_der_liste(self):
        eingetragen = {(t.lower(), s.lower())
                       for t, s in self.vorgaben["_SPALTEN_NACH_DER_MARKE"]}
        eingetragen |= _EIGENE_ZEILE_IM_TEST
        spaete_tabellen = {t.lower() for t in self.vorgaben["_TABELLEN_NACH_DER_MARKE"]}
        fehlt = []
        for rev in sorted(self.spaet):
            datei, _ = self.revisionen[rev]
            spalten, *_ = _was_die_migration_anlegt(os.path.join(_VERSIONEN, datei))
            for tabelle, spalte in sorted(spalten):
                if tabelle in spaete_tabellen:
                    continue          # Tabelle kommt selbst erst spaeter, mit ihren Spalten
                if spalte not in self.modelle.get(tabelle, set()):
                    continue          # create_all legt sie nicht an (Funktion entfernt)
                if (tabelle, spalte) not in eingetragen:
                    fehlt.append(f"{tabelle}.{spalte} (aus {datei})")
        self.assertEqual(
            [], fehlt,
            "Diese Spalten legt eine Migration nach der Marke an, aber der "
            "Postgres-Test dreht sie nicht zurueck — fuer sie laeuft die "
            "Migration dort nie wirklich:\n  " + "\n  ".join(fehlt) +
            "\n\nEintragen in _SPALTEN_NACH_DER_MARKE in "
            f"{os.path.basename(_TESTDATEI)}.")

    def test_jede_spaeter_angelegte_tabelle_steht_in_der_liste(self):
        eingetragen = {t.lower() for t in self.vorgaben["_TABELLEN_NACH_DER_MARKE"]}
        fehlt = []
        for rev in sorted(self.spaet):
            datei, _ = self.revisionen[rev]
            _, tabellen, _ = _was_die_migration_anlegt(os.path.join(_VERSIONEN, datei))
            for tabelle in sorted(tabellen):
                if tabelle not in self.modelle:
                    continue          # Tabelle gehoert zu einer entfernten Funktion
                if tabelle not in eingetragen:
                    fehlt.append(f"{tabelle} (aus {datei})")
        self.assertEqual(
            [], fehlt,
            "Diese Tabellen legt eine Migration nach der Marke an; ohne Eintrag "
            "scheitert ihr Vorwaertslauf im Postgres-Test an DuplicateTable:\n  "
            + "\n  ".join(fehlt) + "\n\nEintragen in _TABELLEN_NACH_DER_MARKE in "
            f"{os.path.basename(_TESTDATEI)}.")

    def test_kein_eintrag_ohne_migration(self):
        """Gegenrichtung: ein Eintrag, zu dem keine Migration (mehr) gehoert,
        entfernt im Postgres-Test eine Spalte, die danach niemand wieder
        anlegt — der Lauf scheitert dann an der Schlusspruefung."""
        angelegt = set()
        for rev in self.spaet:
            datei, _ = self.revisionen[rev]
            spalten, *_ = _was_die_migration_anlegt(os.path.join(_VERSIONEN, datei))
            angelegt |= spalten
        verwaist = [f"{t}.{s}" for t, s in self.vorgaben["_SPALTEN_NACH_DER_MARKE"]
                    if (t.lower(), s.lower()) not in angelegt]
        self.assertEqual(
            [], verwaist,
            "Zu diesen Eintraegen legt keine Migration nach der Marke eine Spalte "
            "an:\n  " + "\n  ".join(verwaist))


if __name__ == "__main__":
    unittest.main()

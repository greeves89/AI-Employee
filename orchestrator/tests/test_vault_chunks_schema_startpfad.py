"""#834: vault_chunks wird bei JEDEM Start vollstaendig hergestellt.

Der gemeldete Zustand: ``vault_chunks`` ohne ``embedding`` und ``ts``, ohne
``DEFAULT now()`` auf ``updated_at`` und ohne die drei Indizes. Beide
Einfuegewege des Indexers scheitern dann dauerhaft, waehrend der Crawler
endlos weiterlaeuft -- ein Kern auf Anschlag, null Zeilen, nichts im Log.

Geprueft wird dreierlei, weil jedes fuer sich luegen kann:
1. Die Anweisungsliste deckt alle vier Maengel ab (Inhalt).
2. Ein Fehlschlag in der Mitte stoppt die uebrigen NICHT (Verhalten, mit
   einer Attrappe, die auch falsch antworten kann).
3. Der Aufruf haengt im UNBEDINGTEN Startpfad -- nicht im Migrations-
   Rueckfall, der auf einer bereits beschaedigten Anlage nie wieder laeuft.
   Ohne (3) waere die Reparatur vorhanden und trotzdem wirkungslos.
"""

import ast
import asyncio
import unittest
from pathlib import Path

from app.core.vault_chunks_schema import (
    ENSURE_STATEMENTS,
    _ZEITGRENZEN,
    ensure_vault_chunks_schema,
)

_ORCH = Path(__file__).resolve().parents[1]
_MAIN = _ORCH / "app" / "main.py"


class _Verbindung:
    def __init__(self, protokoll, fehler_bei):
        self._protokoll = protokoll
        self._fehler_bei = fehler_bei

    async def execute(self, anweisung):
        text = str(anweisung)
        self._protokoll.append(text)
        if self._fehler_bei and self._fehler_bei in text:
            raise RuntimeError('type "vector" does not exist')


class _Transaktion:
    def __init__(self, protokoll, fehler_bei):
        self._protokoll = protokoll
        self._fehler_bei = fehler_bei

    async def __aenter__(self):
        return _Verbindung(self._protokoll, self._fehler_bei)

    async def __aexit__(self, *_):
        return False


class _Attrappe:
    """Attrappe, die auch falsch antworten KANN: ``fehler_bei`` laesst genau
    die Anweisung scheitern, die den Teilstring enthaelt."""

    def __init__(self, fehler_bei: str | None = None):
        self.protokoll: list[str] = []
        self._fehler_bei = fehler_bei

    def begin(self):
        return _Transaktion(self.protokoll, self._fehler_bei)

    @property
    def ddl(self) -> list[str]:
        """Nur die Schema-Anweisungen, ohne die Zeitgrenzen je Transaktion."""
        return [a for a in self.protokoll if not a.startswith("SET LOCAL")]


class AnweisungslisteTests(unittest.TestCase):
    def test_deckt_alle_vier_gemeldeten_maengel_ab(self):
        alles = " ".join(" ".join(a.split()) for a in ENSURE_STATEMENTS).lower()
        self.assertIn("add column if not exists embedding vector(1024)", alles)
        self.assertIn("add column if not exists ts tsvector generated always as", alles)
        self.assertIn("alter column updated_at set default now()", alles)
        for index in ("ix_vault_chunks_brain_path", "ix_vault_chunks_ts", "ix_vault_chunks_embedding"):
            self.assertIn(index, alles)

    def test_jede_anweisung_ist_fuer_sich_idempotent(self):
        """Der Startpfad laeuft bei jedem Start -- eine Anweisung ohne
        IF NOT EXISTS/SET DEFAULT wuerde beim zweiten Start rot."""
        for anweisung in ENSURE_STATEMENTS:
            eine = " ".join(anweisung.split()).lower()
            self.assertTrue(
                "if not exists" in eine or "set default" in eine,
                f"nicht wiederholbar: {eine[:70]}",
            )

    def test_spalte_kommt_vor_ihrem_index(self):
        reihe = [" ".join(a.split()).lower() for a in ENSURE_STATEMENTS]
        def pos(teil):
            return next(i for i, a in enumerate(reihe) if teil in a)
        self.assertLess(pos("add column if not exists embedding"), pos("ix_vault_chunks_embedding"))
        self.assertLess(pos("add column if not exists ts tsvector"), pos("ix_vault_chunks_ts"))


class EinFehlschlagStopptDieReiheNichtTests(unittest.TestCase):
    def test_alle_anweisungen_laufen_wenn_nichts_scheitert(self):
        attrappe = _Attrappe()
        gescheitert = asyncio.run(ensure_vault_chunks_schema(attrappe))
        self.assertEqual(gescheitert, [])
        self.assertEqual(len(attrappe.ddl), len(ENSURE_STATEMENTS))

    def test_ein_fehlschlag_in_der_MITTE_laesst_die_uebrigen_laufen(self):
        """Der Fehler muss MITTEN in der Reihe sitzen, sonst beweist der Test
        nichts: bei einem Abbruch an der letzten Anweisung sieht ein
        ``break`` genauso aus wie ein Weiterlaufen.

        Lage: pgvector fehlt -> die Vektor-Anweisungen sterben, die
        Volltextsuche (ts + GIN-Index) muss trotzdem entstehen."""
        fehlerhaft = "ADD COLUMN IF NOT EXISTS embedding"
        reihe = [" ".join(a.split()) for a in ENSURE_STATEMENTS]
        stelle = next(i for i, a in enumerate(reihe) if fehlerhaft in a)
        self.assertLess(stelle, len(reihe) - 1, "Vorbedingung: nicht die letzte Anweisung")

        attrappe = _Attrappe(fehler_bei=fehlerhaft)
        gescheitert = asyncio.run(ensure_vault_chunks_schema(attrappe))
        self.assertEqual(len(gescheitert), 1)
        self.assertIn("embedding", gescheitert[0])
        self.assertEqual(len(attrappe.ddl), len(ENSURE_STATEMENTS))
        self.assertTrue(any("ix_vault_chunks_ts" in a for a in attrappe.ddl))
        self.assertTrue(any("hnsw" in a for a in attrappe.ddl))

    def test_die_attrappe_kann_wirklich_falsch_antworten(self):
        """Gegenprobe zur Attrappe selbst: ohne sie bewiese Test 2 nichts."""
        attrappe = _Attrappe(fehler_bei="CREATE EXTENSION")
        gescheitert = asyncio.run(ensure_vault_chunks_schema(attrappe))
        self.assertEqual(len(gescheitert), 1)
        self.assertIn("EXTENSION", gescheitert[0])


class ReparaturUndMigrationLaufenNichtAuseinanderTests(unittest.TestCase):
    """Die Reparatur traegt eine zweite Kopie der Tabellendefinition. Waechst
    die Migration um eine Spalte und diese hier nicht, entstuende auf einer
    frischen Anlage im Rueckfall genau wieder eine Teilform (#834)."""

    def test_gleiche_spaltenmenge_wie_die_revision(self):
        from tests.test_migration_kollidiert_nicht_mit_startpfad import (
            _spalten_aus_create_table,
            _startpfad_spalten,
        )

        revision = (_ORCH / "alembic" / "versions"
                    / "v7h1b2r3d4s5_vault_chunks_hybrid_search.py").read_text()
        aus_migration = _spalten_aus_create_table(revision)["vault_chunks"]
        # Die Reparatur verteilt dieselben Spalten auf CREATE TABLE UND
        # eigene ADD-COLUMN-Anweisungen (die Vektorspalte muss heraus, damit
        # ein fehlendes pgvector nicht die ganze Tabelle verhindert).
        text = "\n".join(ENSURE_STATEMENTS)
        aus_reparatur = _spalten_aus_create_table(text)["vault_chunks"]
        aus_reparatur |= {spalte for tabelle, spalte
                          in _startpfad_spalten(f'x = """{text}"""')
                          if tabelle == "vault_chunks"}
        self.assertEqual(aus_migration, aus_reparatur)

    def test_gleiche_indizes_wie_die_revision(self):
        revision = (_ORCH / "alembic" / "versions"
                    / "v7h1b2r3d4s5_vault_chunks_hybrid_search.py").read_text().lower()
        for index in ("ix_vault_chunks_brain_path", "ix_vault_chunks_ts",
                      "ix_vault_chunks_embedding"):
            self.assertIn(index, revision)
            self.assertIn(index, " ".join(ENSURE_STATEMENTS).lower())


class DasModellErzeugtDieRichtigeFormTests(unittest.TestCase):
    """Die Quellform: was ``create_all`` im Rueckfall baut, muss zur Migration
    passen. Geprueft am wirklich erzeugten DDL, nicht am Modelltext."""

    def _ddl(self) -> str:
        from sqlalchemy.dialects import postgresql
        from sqlalchemy.schema import CreateTable

        from app.models import Base

        return str(CreateTable(Base.metadata.tables["vault_chunks"])
                   .compile(dialect=postgresql.dialect())).lower()

    def test_updated_at_traegt_den_vorgabewert_in_der_datenbank(self):
        """Der Indexer fuegt per rohem SQL ein und setzt updated_at nicht --
        ohne DEFAULT scheitert dieser Weg am NOT NULL (#834)."""
        self.assertRegex(self._ddl(), r"updated_at[^,]*default now\(\)")

    def test_id_ist_bigserial_wie_in_der_migration(self):
        self.assertRegex(self._ddl(), r"id bigserial")


class JedeTransaktionBegrenztDieWartezeitTests(unittest.TestCase):
    """Ohne Zeitgrenze wartet der Hochlauf unbegrenzt auf den
    ACCESS-EXCLUSIVE-Lock, den ``ALTER TABLE`` auch dann nimmt, wenn dank
    IF NOT EXISTS nichts zu tun ist -- und hinter ihm staut sich jede Abfrage
    auf die Tabelle. Eine Reparatur darf die Anlage nicht schlimmer
    festsetzen als der Mangel, den sie behebt."""

    def test_vor_jeder_anweisung_steht_lock_timeout(self):
        attrappe = _Attrappe()
        asyncio.run(ensure_vault_chunks_schema(attrappe))
        ddl = attrappe.ddl
        self.assertEqual(len(ddl), len(ENSURE_STATEMENTS))
        # In JEDER Transaktion: die Grenzen zuerst, dann die Anweisung.
        for i, eintrag in enumerate(attrappe.protokoll):
            if eintrag in ddl:
                vorher = attrappe.protokoll[max(0, i - 2):i]
                self.assertTrue(any("lock_timeout" in v for v in vorher), eintrag[:60])
                self.assertTrue(any("statement_timeout" in v for v in vorher), eintrag[:60])

    def test_grenzen_sind_transaktionslokal(self):
        """``SET LOCAL`` und nicht ``SET``: ein globales SET bliebe auf der
        Verbindung stehen und begrenzte spaeter fremde Abfragen."""
        for grenze in _ZEITGRENZEN:
            self.assertTrue(grenze.startswith("SET LOCAL "), grenze)


class DerAufrufHaengtImUnbedingtenStartpfadTests(unittest.TestCase):
    def _umschliessende_funktionen(self) -> set[str]:
        baum = ast.parse(_MAIN.read_text())
        treffer = set()
        for fn in ast.walk(baum):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for knoten in ast.walk(fn):
                if (isinstance(knoten, ast.Call) and isinstance(knoten.func, ast.Name)
                        and knoten.func.id == "ensure_vault_chunks_schema"):
                    treffer.add(fn.name)
        return treffer

    def test_wird_in_lifespan_aufgerufen(self):
        self.assertIn("lifespan", self._umschliessende_funktionen())

    def test_der_aufruf_steht_nicht_hinter_einer_bedingung(self):
        """Ein Aufruf im Quelltext beweist nichts, wenn eine Verzweigung ihn
        nie erreicht (``if False:``, ``if einstellung:``). Erlaubt ist nur
        der ``try``, der Ausnahmen faengt -- keine Bedingung."""
        baum = ast.parse(_MAIN.read_text())
        eltern = {}
        for knoten in ast.walk(baum):
            for kind in ast.iter_child_nodes(knoten):
                eltern[kind] = knoten
        treffer = [k for k in ast.walk(baum)
                   if isinstance(k, ast.Call) and isinstance(k.func, ast.Name)
                   and k.func.id == "ensure_vault_chunks_schema"]
        self.assertEqual(len(treffer), 1)
        knoten = treffer[0]
        while knoten in eltern:
            knoten = eltern[knoten]
            if isinstance(knoten, (ast.FunctionDef, ast.AsyncFunctionDef)):
                break
            self.assertNotIsInstance(knoten, (ast.If, ast.While),
                                     "Aufruf haengt hinter einer Bedingung")

    def test_nicht_nur_im_migrations_rueckfall(self):
        """``_init_db_from_models`` laeuft NUR, wenn eine Migration scheitert.
        Auf einer bereits beschaedigten Anlage gelingt ``upgrade head`` --
        eine Reparatur allein dort heilt den Bestand nie."""
        self.assertNotEqual(self._umschliessende_funktionen(), {"_init_db_from_models"})


if __name__ == "__main__":
    unittest.main()


def _spaltendefinitionen(text: str) -> tuple[dict[str, str], set[str]]:
    """Aus einem rohen ``CREATE TABLE``: Spalte -> volle Definition, plus die
    Tabellenbedingungen.

    Die Spaltenmengen-Probe oben vergleicht nur NAMEN. Genau daran rutscht die
    Abweichungsklasse von #834 wieder durch: ``content TEXT NOT NULL`` gegen
    ``content TEXT``, ``chunk_idx INTEGER`` gegen ``VARCHAR`` oder ein anders
    benanntes ``CONSTRAINT`` haben identische Namen und ein anderes Schema.
    """
    from tests.test_migration_kollidiert_nicht_mit_startpfad import (
        _CREATE_TABLE,
        _KEINE_SPALTE,
    )

    treffer = _CREATE_TABLE.search(text)
    assert treffer, "kein CREATE TABLE gefunden"
    tiefe, i = 1, treffer.end()
    while i < len(text) and tiefe:
        tiefe += (text[i] == "(") - (text[i] == ")")
        i += 1
    rumpf, teil, tiefe2, teile = text[treffer.end():i - 1], "", 0, []
    for zeichen in rumpf:
        tiefe2 += (zeichen == "(") - (zeichen == ")")
        if zeichen == "," and tiefe2 == 0:
            teile.append(teil)
            teil = ""
        else:
            teil += zeichen
    teile.append(teil)

    spalten: dict[str, str] = {}
    bedingungen: set[str] = set()
    for eintrag in teile:
        worte = " ".join(eintrag.split())
        if not worte:
            continue
        erstes = worte.split()[0].strip('"').lower()
        if erstes in _KEINE_SPALTE:
            bedingungen.add(worte.lower())
        else:
            spalten[erstes] = " ".join(worte.split()[1:]).lower()
    return spalten, bedingungen


class ReparaturUndMigrationStimmenAuchInDerFormUeberein(unittest.TestCase):
    """Zweite Kopie einer Tabellendefinition heisst: sie kann auseinanderlaufen.
    Nicht nur in den Spalten (oben geprueft), sondern in Typ, ``NOT NULL``,
    Vorgabewert und Bedingungsnamen -- und eine so entstandene Teilform waere
    wieder genau der stille Zustand aus #834."""

    def _beide(self):
        revision = (_ORCH / "alembic" / "versions"
                    / "v7h1b2r3d4s5_vault_chunks_hybrid_search.py").read_text()
        reparatur = next(a for a in ENSURE_STATEMENTS if "CREATE TABLE" in a)
        return _spaltendefinitionen(revision), _spaltendefinitionen(reparatur)

    def test_gemeinsame_spalten_sind_zeichengleich_definiert(self):
        (m_spalten, _), (r_spalten, _) = self._beide()
        gemeinsam = set(m_spalten) & set(r_spalten)
        # Vorbedingung: ohne sie prueft die Schleife unten NICHTS.
        self.assertGreaterEqual(len(gemeinsam), 8, gemeinsam)
        for spalte in sorted(gemeinsam):
            self.assertEqual(m_spalten[spalte], r_spalten[spalte], spalte)

    def test_nur_die_vektorspalte_fehlt_im_reparatur_create(self):
        """Sie wird absichtlich einzeln nachgetragen (fehlendes pgvector darf
        nicht die ganze Tabelle verhindern) -- aber mit demselben Typ."""
        (m_spalten, _), (r_spalten, _) = self._beide()
        self.assertEqual(set(m_spalten) - set(r_spalten), {"embedding"})
        typ = m_spalten["embedding"]
        nachtrag = next(a for a in ENSURE_STATEMENTS
                        if "ADD COLUMN IF NOT EXISTS embedding" in a)
        self.assertIn(typ, " ".join(nachtrag.split()).lower())

    def test_gleiche_tabellenbedingungen(self):
        (_, m_bed), (_, r_bed) = self._beide()
        self.assertTrue(m_bed, "Vorbedingung: die Migration hat Bedingungen")
        self.assertEqual(m_bed, r_bed)

    def test_die_pruefung_erkennt_eine_erfundene_abweichung(self):
        """Gegenprobe: eine Probe, die nicht fehlschlagen KANN, beweist nichts."""
        echt = next(a for a in ENSURE_STATEMENTS if "CREATE TABLE" in a)
        for kaputt, erwartet in (
            (echt.replace("content      TEXT NOT NULL", "content      TEXT"), "content"),
            (echt.replace("chunk_idx    INTEGER", "chunk_idx    VARCHAR"), "chunk_idx"),
        ):
            spalten, _ = _spaltendefinitionen(kaputt)
            revision = (_ORCH / "alembic" / "versions"
                        / "v7h1b2r3d4s5_vault_chunks_hybrid_search.py").read_text()
            m_spalten, _ = _spaltendefinitionen(revision)
            self.assertNotEqual(m_spalten[erwartet], spalten[erwartet], erwartet)
        umbenannt = echt.replace("uq_vault_chunk", "uq_vault_chunk_neu")
        _, bedingungen = _spaltendefinitionen(umbenannt)
        _, m_bed = _spaltendefinitionen(
            (_ORCH / "alembic" / "versions"
             / "v7h1b2r3d4s5_vault_chunks_hybrid_search.py").read_text())
        self.assertNotEqual(m_bed, bedingungen)


class DieReparaturMeldetIhrEigenesScheiternTests(unittest.TestCase):
    """Eine Reparatur, die still scheitert, ist so schlimm wie der Mangel: die
    Anlage laeuft weiter, die Tabelle bleibt unbrauchbar, im Log steht nichts.
    Deshalb VERHALTEN pruefen, nicht den Quelltext."""

    def test_jede_gescheiterte_anweisung_wird_gemeldet(self):
        attrappe = _Attrappe(fehler_bei="hnsw")
        with self.assertLogs("app.core.vault_chunks_schema", level="WARNING") as protokoll:
            fehlt = asyncio.run(ensure_vault_chunks_schema(attrappe))
        self.assertEqual(len(fehlt), 1)
        # Genau auf WARNING pruefen, nicht "irgendwas ab WARNING": die
        # Zustandsmeldung darunter ist ein FEHLER und wuerde eine auf
        # ``debug`` herabgestufte Einzelmeldung sonst mit abdecken.
        warnungen = [s.getMessage() for s in protokoll.records
                     if s.levelname == "WARNING"]
        self.assertTrue(warnungen, protokoll.output)
        self.assertIn("hnsw", "\n".join(warnungen).lower())

    def test_der_zustand_unvollstaendig_wird_als_fehler_gemeldet(self):
        attrappe = _Attrappe(fehler_bei="hnsw")
        with self.assertLogs("app.core.vault_chunks_schema", level="ERROR") as protokoll:
            asyncio.run(ensure_vault_chunks_schema(attrappe))
        gesamt = "\n".join(protokoll.output).lower()
        self.assertIn("unvollstaendig", gesamt)

    def test_ein_sauberer_lauf_meldet_weder_warnung_noch_fehler(self):
        """Gegenprobe: eine Meldung bei JEDEM Start waere nach einer Woche
        Rauschen -- und Rauschen sieht niemand mehr an."""
        import logging

        attrappe = _Attrappe()
        with self.assertNoLogs("app.core.vault_chunks_schema", level=logging.WARNING):
            self.assertEqual(asyncio.run(ensure_vault_chunks_schema(attrappe)), [])


class FehlendesTsSperrtDieSchreibfreigabeTests(unittest.TestCase):
    """``ALTER COLUMN updated_at SET DEFAULT`` macht den vektorlosen
    Einfuegeweg wieder gangbar -- ab da fuellt sich die Tabelle. Fehlt ``ts``
    noch, braucht das spaetere ``ADD COLUMN .. GENERATED`` einen vollen
    Table-Rewrite; aus einem reparierbaren Zustand wuerde ein dauerhafter."""

    def test_die_beiden_anweisungen_gibt_es_genau_einmal_und_in_dieser_reihenfolge(self):
        """Vorbedingung: ohne sie prueft der Test darunter am Ziel vorbei."""
        from app.core.vault_chunks_schema import _SCHREIBFREIGABE, _TS_SPALTE

        ts = [i for i, a in enumerate(ENSURE_STATEMENTS) if _TS_SPALTE in a]
        freigabe = [i for i, a in enumerate(ENSURE_STATEMENTS) if _SCHREIBFREIGABE in a]
        self.assertEqual(len(ts), 1, ts)
        self.assertEqual(len(freigabe), 1, freigabe)
        self.assertLess(ts[0], freigabe[0])

    def test_scheitert_ts_bleibt_die_schreibfreigabe_aus(self):
        from app.core.vault_chunks_schema import _SCHREIBFREIGABE

        attrappe = _Attrappe(fehler_bei="ADD COLUMN IF NOT EXISTS ts")
        fehlt = asyncio.run(ensure_vault_chunks_schema(attrappe))
        self.assertFalse([a for a in attrappe.ddl if _SCHREIBFREIGABE in a], attrappe.ddl)
        # Beide stehen im Befund: die gescheiterte UND die ausgelassene.
        self.assertEqual(len(fehlt), 2, fehlt)
        # Die Reihe laeuft trotzdem zu Ende -- die Indizes danach kommen noch.
        self.assertTrue([a for a in attrappe.ddl if "hnsw" in a.lower()])

    def test_steht_ts_laeuft_die_schreibfreigabe(self):
        """Gegenprobe: die Sperre darf nicht im Normalfall zugreifen."""
        from app.core.vault_chunks_schema import _SCHREIBFREIGABE

        attrappe = _Attrappe()
        self.assertEqual(asyncio.run(ensure_vault_chunks_schema(attrappe)), [])
        self.assertTrue([a for a in attrappe.ddl if _SCHREIBFREIGABE in a], attrappe.ddl)

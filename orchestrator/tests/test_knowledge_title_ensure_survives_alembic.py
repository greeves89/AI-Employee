"""Die mandantenweise Titel-Eindeutigkeit haelt auch ohne Alembic.

Die Migration b7c1e93a5f20 loest die GLOBALE Eindeutigkeit auf
`knowledge_entries.title` auf. Scheitert `alembic upgrade head` aber — ein im
Code selbst dokumentierter Fall (mehrere heads) —, greift der Rueckfall
`_init_db_from_models()`, und der legt aus dem Modell wieder einen globalen
Unique-Index an. Auf so einer Anlage braechen Reflexionslaeufe weiter an einem
Titel ab, den ein ANDERER Mandant schon hat (Issue #655), obwohl die Migration
im Repo liegt.

Deshalb wird dieselbe Zusicherung zusaetzlich bei jedem Start ausgefuehrt.
"""

import unittest
from pathlib import Path

_MAIN = (Path(__file__).resolve().parents[1] / "app" / "main.py").read_text()
_MIGRATION = (
    Path(__file__).resolve().parents[1] / "alembic" / "versions"
    / "b7c1e93a5f20_knowledge_title_unique_per_tenant.py"
).read_text()


def _ensure_block() -> str:
    start = _MAIN.index("knowledge_entries title uniqueness")
    # rueckwaerts bis zum try, vorwaerts bis zum naechsten ensure
    vor = _MAIN.rindex("try:", 0, start)
    return _MAIN[vor:start + 400]


class DerStartLegtDieIndizesSelbstAnTests(unittest.TestCase):
    def test_es_gibt_ueberhaupt_ein_ensure(self):
        self.assertIn("knowledge_entries title uniqueness ensured", _MAIN)

    def test_der_alte_globale_index_wird_entfernt(self):
        self.assertIn("DROP INDEX IF EXISTS ix_knowledge_entries_title", _ensure_block())

    def test_die_suche_bleibt_indiziert(self):
        """Ersatzlos entfernen wuerde jede Titelsuche zum Full-Scan machen."""
        self.assertIn("CREATE INDEX IF NOT EXISTS ix_knowledge_entries_title", _ensure_block())

    def test_beide_partiellen_indizes_werden_angelegt(self):
        block = _ensure_block()
        self.assertIn("uq_knowledge_entries_title_global", block)
        self.assertIn("WHERE user_id IS NULL", block)
        self.assertIn("uq_knowledge_entries_title_per_user", block)
        self.assertIn("WHERE user_id IS NOT NULL", block)

    def test_es_ist_wiederholbar(self):
        """Laeuft bei JEDEM Start — ohne IF NOT EXISTS bricht der zweite Start."""
        block = _ensure_block()
        self.assertEqual(block.count("IF NOT EXISTS"), 3)

    def test_ein_fehler_hier_haelt_den_start_nicht_auf(self):
        self.assertIn("Could not ensure knowledge_entries title indexes", _MAIN)


class EnsureUndMigrationStimmenUeberEinTests(unittest.TestCase):
    def test_dieselben_indexnamen(self):
        for name in ("ix_knowledge_entries_title",
                     "uq_knowledge_entries_title_global",
                     "uq_knowledge_entries_title_per_user"):
            self.assertIn(name, _MIGRATION, f"{name} fehlt in der Migration")
            self.assertIn(name, _MAIN, f"{name} fehlt im Startup-Ensure")

    def test_dieselben_bedingungen(self):
        """Waeren die WHERE-Klauseln verschieden, haetten zwei Anlagen zwei
        verschiedene Regeln — genau die Art Abweichung, die niemand bemerkt."""
        for bedingung in ("WHERE user_id IS NULL", "WHERE user_id IS NOT NULL"):
            self.assertIn(bedingung, _MIGRATION)
            self.assertIn(bedingung, _ensure_block())


if __name__ == "__main__":
    unittest.main()

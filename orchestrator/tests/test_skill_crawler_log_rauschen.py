"""Ein erwarteter Ausgang darf das Fehlerlog nicht fluten (#933).

Der Crawler trifft bei jedem Lauf auf Namen, die bereits einem selbst
angelegten Skill gehören. Dass er sie stehen lässt, ist die gewollte
Entscheidung — und kein Fehler. Vor dem Fix schrieb er dafür je Skill und
je Lauf eine WARNING; auf der Anlage waren das 936 von 1107 WARNING/ERROR-
Kopfzeilen des Fehlerlogs in 8,3 Tagen, also 85 % Rauschen in genau dem
Kanal, in dem man echte Störungen sucht.

Der Test prüft beides: dass die Entscheidung unverändert fällt (der eigene
Skill wird NICHT überschrieben) und dass sie nur noch als Sammelzeile auf
INFO erscheint, nicht als WARNING.
"""
import asyncio
import logging
import sys
import types
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

ORCH = Path(__file__).resolve().parents[1]
if str(ORCH) not in sys.path:
    sys.path.insert(0, str(ORCH))

from app.services import skill_crawler as sc  # noqa: E402


class _FakeResult:
    def __init__(self, obj):
        self._obj = obj

    def scalar_one_or_none(self):
        return self._obj


class _FakeDB:
    def __init__(self, existing):
        self._existing = existing
        self.added = []
        self.commits = 0

    async def execute(self, _stmt):
        return _FakeResult(self._existing)

    async def scalar(self, _stmt):
        # lokal_geaendert(): keine Schnappschuesse — der Skill wurde hier nie korrigiert.
        return False

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1


def _eigener_skill():
    """Ein Skill, den der Nutzer selbst angelegt hat (kein 'import:'-Präfix)."""
    return types.SimpleNamespace(
        id=1,
        name="triage",
        content="EIGENER INHALT",
        description="eigene Beschreibung",
        created_by="user:42",
        source_repo=None,
    )


class TestCrawlerLogRauschen(unittest.TestCase):
    def _lauf(self, existing):
        """Ruft _sync_to_db mit einer Fake-Sitzung und fängt die Logs ein."""
        db = _FakeDB(existing)

        @asynccontextmanager
        async def _fake_session():
            yield db

        gecrawlt = [{
            "name": "triage",
            "content": "# Crawl-Inhalt\nharmloser Text",
            "description": "aus der Quelle",
            "created_by": "import:github",
            "source_repo": "beispiel/quelle",
            "source_url": "https://example.com/quelle",
        }]

        service = sc.SkillCrawlerService.__new__(sc.SkillCrawlerService)
        # Eigener Handler statt assertLogs: der Aufruf darf auch GAR NICHTS
        # loggen, ohne dass der Testaufbau deswegen scheitert.
        records: list[logging.LogRecord] = []

        class _Sammler(logging.Handler):
            def emit(self, record):
                records.append(record)

        sammler = _Sammler(level=logging.DEBUG)
        alter_level = sc.logger.level
        sc.logger.addHandler(sammler)
        sc.logger.setLevel(logging.DEBUG)
        try:
            with patch("app.db.session.resilient_session", _fake_session):
                asyncio.run(service._sync_to_db(gecrawlt))
        finally:
            sc.logger.removeHandler(sammler)
            sc.logger.setLevel(alter_level)
        return db, records

    def test_eigener_skill_wird_nicht_ueberschrieben(self):
        """Die fachliche Entscheidung bleibt unveraendert — nur das Logging aendert sich."""
        existing = _eigener_skill()
        db, _records = self._lauf(existing)
        self.assertEqual(
            existing.content, "EIGENER INHALT",
            "Ein selbst angelegter Skill darf nie mit Crawl-Inhalt ueberschrieben werden",
        )
        self.assertEqual(existing.description, "eigene Beschreibung")
        self.assertEqual(db.added, [], "kein zweiter Skill gleichen Namens")
        # Zeuge fuer den Pfadeintritt: ohne diese Zusicherung wuerde der Test
        # auch dann gruen bleiben, wenn der Aufruf in seinem except-Rahmen
        # gescheitert waere und ueberhaupt nichts getan haette.
        self.assertEqual(db.commits, 1, "der Durchlauf muss die Sitzung wirklich erreicht haben")

    def test_erwarteter_ausgang_erzeugt_keine_warnung(self):
        existing = _eigener_skill()
        db, records = self._lauf(existing)

        # Vorbedingung: der Zweig wurde wirklich betreten. Ohne diese Zusicherung
        # wäre ein grüner Lauf wertlos (ein kaputter Aufbau würde auch "keine
        # Warnung" liefern).
        sammelzeilen = [
            r.getMessage() for r in records
            if r.levelno == logging.INFO and "skipped" in r.getMessage()
        ]
        self.assertEqual(
            len(sammelzeilen), 1,
            "Der Lauf muss GENAU EINE INFO-Sammelzeile mit der Zahl der "
            f"übersprungenen Namen schreiben. Gesehen: "
            f"{[(r.levelname, r.getMessage()) for r in records]}",
        )
        self.assertIn("1 skipped", sammelzeilen[0])

        # Und sie steht nicht mehr im Fehlerkanal.
        laut = [
            (r.levelname, r.getMessage()) for r in records
            if r.levelno >= logging.WARNING
        ]
        self.assertEqual(
            laut, [],
            "Ein erwarteter Ausgang (Name gehört einem eigenen Skill) darf "
            f"keine WARNING/ERROR erzeugen, sonst flutet er das Fehlerlog: {laut}",
        )

    def test_import_skill_wird_weiter_aktualisiert(self):
        """Gegenstück: ein zuvor importierter Skill wird weiterhin nachgezogen."""
        existing = _eigener_skill()
        existing.created_by = "import:github"
        db, records = self._lauf(existing)

        self.assertEqual(existing.content, "# Crawl-Inhalt\nharmloser Text")
        laut = [
            (r.levelname, r.getMessage()) for r in records
            if r.levelno >= logging.WARNING
        ]
        self.assertEqual(laut, [], f"unerwartete Warnungen: {laut}")


class TestMitgelieferteSkillsLogRauschen(unittest.TestCase):
    """Geschwisterstelle derselben Fehlerklasse (#933).

    `mitgelieferte_skills.synchronisieren` laesst einen gleichnamigen Skill
    fremder Herkunft bewusst stehen — laut Modulkopf die gewollte Entscheidung.
    Auch dieser erwartete Ausgang gehoert nicht ins Fehlerlog. Gemessen hat er
    auf der Anlage bisher 0 Zeilen erzeugt (er feuert nur beim Containerstart),
    die Fehlerklasse ist aber dieselbe und waechst mit jedem Produkt-Skill.
    """

    def test_fremd_belegter_name_erzeugt_keine_warnung(self):
        from app.core import mitgelieferte_skills as ms

        vorhanden = types.SimpleNamespace(
            name="angebot", content="EIGENER INHALT", description="eigene",
            created_by="user:42", source_repo=None,
        )
        db = _FakeDB(vorhanden)

        @asynccontextmanager
        async def _fake_session():
            yield db

        mitgeliefert = [{
            "name": "angebot",
            "description": "aus dem Produkt",
            "content": "# Produkt-Inhalt\nharmloser Text",
            "category": "ROUTINE",
            "created_by": "produkt",
        }]

        records: list[logging.LogRecord] = []

        class _Sammler(logging.Handler):
            def emit(self, record):
                records.append(record)

        sammler = _Sammler(level=logging.DEBUG)
        alter_level = ms.logger.level
        ms.logger.addHandler(sammler)
        ms.logger.setLevel(logging.DEBUG)
        try:
            with patch.object(ms, "laden", lambda: mitgeliefert), \
                 patch("app.db.session.resilient_session", _fake_session):
                neu, aktualisiert = asyncio.run(ms.synchronisieren())
        finally:
            ms.logger.removeHandler(sammler)
            ms.logger.setLevel(alter_level)

        # Vorbedingung: der Zweig wurde betreten (nichts angelegt, nichts
        # aktualisiert, Sitzung erreicht) und die Sammelzeile zaehlt ihn.
        self.assertEqual((neu, aktualisiert), (0, 0))
        self.assertEqual(db.added, [])
        self.assertEqual(db.commits, 1)
        sammelzeilen = [
            r.getMessage() for r in records
            if r.levelno == logging.INFO and "anderer Herkunft" in r.getMessage()
        ]
        self.assertEqual(
            len(sammelzeilen), 1,
            f"genau eine INFO-Sammelzeile erwartet, gesehen: "
            f"{[(r.levelname, r.getMessage()) for r in records]}",
        )
        self.assertIn("1 Namen", sammelzeilen[0])

        # Der fremde Skill bleibt unberuehrt ...
        self.assertEqual(vorhanden.content, "EIGENER INHALT")
        # ... und der erwartete Ausgang steht nicht im Fehlerkanal.
        laut = [
            (r.levelname, r.getMessage()) for r in records
            if r.levelno >= logging.WARNING
        ]
        self.assertEqual(laut, [], f"erwarteter Ausgang darf nicht warnen: {laut}")


if __name__ == "__main__":
    unittest.main()

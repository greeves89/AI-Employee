"""Ein Neustart von Redis darf die Agenten nicht aussperren.

Beobachtet am 03.09.2026 nach einem Host-Neustart: Zwei von drei Agenten hingen
in einer Neustartschleife mit

    redis.exceptions.AuthenticationError: invalid username-password pair
    or user is disabled

Der dritte lief nur, weil er zufaellig danach neu erstellt worden war.

Ursache: Redis haelt seine ACL-Nutzer nur im Speicher — in der Dienstdefinition
ist keine ``aclfile`` gesetzt. Nach einem Neustart sind sie alle weg. Angelegt
wurden sie bis dahin ausschliesslich beim Erstellen oder Aktualisieren eines
Agenten (``agent_manager`` -> ``ensure_agent_acl_user``); danach stellte sie
niemand wieder her.

Bei eingeschalteter ACL (``REDIS_ACL_ENABLED=true``, auf der betroffenen Anlage
der Fall) ist das ein Totalausfall nach jedem Neustart, aus dem die Anlage von
allein nicht mehr herausfindet — jeder Agent muesste einzeln neu erstellt
werden.

Das Passwort ist aus ``api_secret_key`` und Agenten-Kennung ableitbar; der
Docstring von ``agent_acl_password`` nennt genau diesen Zweck („reconnect after
a Redis restart"). Es fehlte allein der Aufruf beim Start.
"""

import ast
import contextlib
import hashlib
import hmac
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_MAIN = (Path(__file__).resolve().parents[1] / "app" / "main.py").read_text()
_REDIS = (Path(__file__).resolve().parents[1] / "app" / "services"
          / "redis_service.py").read_text()


def _ohne_kommentare(block: str) -> str:
    """Kommentare aus einem Quelltextblock tilgen.

    `ast.get_source_segment` liefert den Block MIT Kommentaren — ein
    auskommentierter Aufruf stuende also weiterhin drin und bestuende jedes
    `assertIn`. Genau das ist die Blindstelle aus #726; deshalb werden die
    COMMENT-Token hier ausgeblendet, bevor der Block geprueft wird."""
    import io
    import textwrap
    import tokenize

    text = textwrap.dedent(block)
    zeilen = text.splitlines(keepends=True)
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT:
                (zeile, von), (_, bis) = tok.start, tok.end
                zeilen[zeile - 1] = zeilen[zeile - 1][:von] + zeilen[zeile - 1][bis:]
    except tokenize.TokenError as e:  # unvollstaendiger Block — lieber laut
        raise AssertionError(f"Block nicht tokenisierbar: {e}")
    return "".join(zeilen)


def _block() -> str:
    """Der `if settings.redis_acl_enabled:`-Zweig HINTER dem Kommentar — als
    syntaktischer Block, nicht als 2200 Zeichen ab dem Kommentar. Ein laengerer
    Kommentar oder ein weiterer Import davor verschiebt das Fenster; den
    if-Knoten verschiebt er nicht."""
    zeilen = _MAIN.splitlines()
    ab = next(i for i, z in enumerate(zeilen, 1)
              if "Redis-ACL-Nutzer der Agenten wiederherstellen" in z)
    for knoten in ast.walk(ast.parse(_MAIN)):
        if (isinstance(knoten, ast.If) and knoten.lineno > ab
                and ast.get_source_segment(_MAIN, knoten.test) == "settings.redis_acl_enabled"):
            return _ohne_kommentare(ast.get_source_segment(_MAIN, knoten) or "")
    raise AssertionError("if settings.redis_acl_enabled: nach dem Kommentar nicht gefunden")


class DieAclWirdBeimStartWiederhergestelltTests(unittest.TestCase):
    def test_es_gibt_den_aufruf_ueberhaupt(self):
        # Im kommentarfreien if-Block, nicht in der ganzen Datei: dort bestuende
        # auch ein auskommentierter Aufruf.
        self.assertIn("await app.state.redis.ensure_agent_acl_user(_aid)", _block())

    def test_er_laeuft_nach_der_redis_verbindung(self):
        """Vorher gibt es keine Verbindung, ueber die man Regeln setzen koennte."""
        verbinden = _MAIN.index("await app.state.redis.connect()")
        setzen = _MAIN.index("ensure_agent_acl_user(_aid)")
        self.assertLess(verbinden, setzen)

    def test_er_gilt_fuer_ALLE_agenten(self):
        """Nur die laufenden zu behandeln waere zu wenig: ein spaeter
        gestarteter Agent traefe wieder auf einen fehlenden Nutzer."""
        self.assertIn("_sel_acl(_AgentACL.id)", _block())

    def test_er_laeuft_nur_bei_eingeschalteter_acl(self):
        """Ohne ACL gibt es keine Nutzer, und der Aufruf wuerde nur Fehler
        erzeugen."""
        self.assertIn("if settings.redis_acl_enabled:", _MAIN)
        vor = _MAIN.index("if settings.redis_acl_enabled:")
        setzen = _MAIN.index("ensure_agent_acl_user(_aid)")
        self.assertLess(vor, setzen)

    def test_ein_einzelner_fehlschlag_stoppt_die_uebrigen_nicht(self):
        """Sonst sperrte ein einziger kaputter Datensatz alle anderen aus."""
        block = _block()
        self.assertIn("Redis-ACL fuer %s nicht gesetzt", block)
        self.assertEqual(block.count("except Exception"), 2)

    def test_ein_fehler_verhindert_den_start_nicht(self):
        self.assertIn("Redis-ACL-Wiederherstellung uebersprungen", _block())

    def test_das_ergebnis_steht_im_protokoll(self):
        """Sonst weiss niemand, ob es gelaufen ist."""
        self.assertIn("Redis-ACL fuer %s von %s Agenten sichergestellt", _block())


class DasPasswortIstAbleitbarTests(unittest.TestCase):
    """Die Vorbedingung dafuer, dass sich das ueberhaupt wiederherstellen
    laesst — ohne sie muesste jeder Agent neu erstellt werden."""

    def test_es_haengt_am_serverschluessel_und_der_kennung(self):
        """Die Funktion wirklich rechnen lassen: gleiche Kennung + gleicher
        Schluessel ergeben dasselbe Passwort (sonst waere nichts wiederherstellbar),
        jede Aenderung an einem der beiden ergibt ein anderes."""
        from app.services import redis_service as rs

        with patch.object(rs.settings, "api_secret_key", "schluessel-eins"):
            a = rs.agent_acl_password("agent-a")
            nochmal = rs.agent_acl_password("agent-a")
            b = rs.agent_acl_password("agent-b")
            erwartet = hmac.new(b"schluessel-eins", b"redis-acl:agent-a",
                                hashlib.sha256).hexdigest()
        with patch.object(rs.settings, "api_secret_key", "schluessel-zwei"):
            a_anderer_schluessel = rs.agent_acl_password("agent-a")

        self.assertEqual(a, nochmal)
        self.assertEqual(a, erwartet, "Domaenen-Praefix `redis-acl:` fehlt oder Verfahren geaendert")
        self.assertNotEqual(a, b)
        self.assertNotEqual(a, a_anderer_schluessel)

    def test_das_setzen_ist_wiederholbar(self):
        """`ACL SETUSER` ersetzt die Regeln vollstaendig — sonst waere ein
        zweiter Start ein Problem."""
        self.assertIn("Idempotent (ACL SETUSER replaces the user's rules wholesale",
                      _REDIS)


if __name__ == "__main__":
    unittest.main()


class AuchEinReinerRedisNeustartWirdGeheiltTests(unittest.TestCase):
    """Die Luecke, die der Startup-Fix offen liess.

    Beim Start des Orchestrators werden die Zugaenge gesetzt — das deckt den
    Neustart des ganzen Hosts ab. Startet aber NUR Redis neu (Aktualisierung,
    Absturz, `docker restart`), laeuft der Orchestrator weiter, und niemand
    merkt, dass die Regeln weg sind. Genau so nachgestellt am 03.09.2026: nach
    einem `docker restart` von Redis blieb von acht Zugaengen nur `default`.
    """

    SCHED = (Path(__file__).resolve().parents[1] / "app" / "services"
             / "scheduler_service.py").read_text()

    # Den Takt WIRKLICH laufen lassen, mit einem Redis-Doppel, das eine
    # waehlbare Nutzerliste zurueckgibt, und einer Datenbank, die feste
    # Agenten-Kennungen liefert. Vorher stand hier ein 2600-Zeichen-Fenster,
    # das auch dann gruen blieb, wenn der Aufruf im Fenster auskommentiert war.
    class _Client:
        def __init__(self, nutzer, fehler=None):
            self.nutzer, self.fehler, self.aufrufe = nutzer, fehler, []

        async def execute_command(self, *args):
            self.aufrufe.append(args)
            if self.fehler:
                raise self.fehler
            return list(self.nutzer)

    class _Redis:
        def __init__(self, client):
            self.client, self.gesetzt = client, []

        async def ensure_agent_acl_user(self, agent_id):
            self.gesetzt.append(agent_id)

    def _vorbereitet(self, *, ids, vorhanden, acl_an=True, fehler=None):
        from app.services import scheduler_service as ss
        from app.services.redis_service import agent_acl_username

        redis = self._Redis(self._Client([agent_acl_username(a) for a in vorhanden], fehler))
        planer = ss.SchedulerService.__new__(ss.SchedulerService)
        planer.redis = redis
        planer._ids, planer._acl_an = list(ids), acl_an
        return planer, redis

    def _run(self, planer):
        import asyncio

        import app.config
        from app.services import scheduler_service as ss

        class _Ergebnis:
            def scalars(self_):
                return self_

            def all(self_):
                return list(planer._ids)

        class _Db:
            async def execute(self_, *_):
                return _Ergebnis()

        @contextlib.asynccontextmanager
        async def _session():
            yield _Db()

        with patch.object(app.config.settings, "redis_acl_enabled", planer._acl_an), \
                patch.object(ss, "resilient_session", _session):
            asyncio.run(planer._tick_redis_acl())

    def test_der_takt_prueft_es_mit(self):
        self.assertIn("await self._tick_redis_acl()", self.SCHED)

    def test_er_prueft_erst_und_setzt_dann(self):
        """Nur die FEHLENDEN Zugaenge werden gesetzt — blind jede Regel neu zu
        setzen waere jede Runde unnoetige Last."""
        planer, redis = self._vorbereitet(ids=["a", "b", "c"], vorhanden=["a", "c"])
        with self.assertLogs("app.services.scheduler_service", level="WARNING") as log:
            self._run(planer)
        self.assertEqual(redis.client.aufrufe, [("ACL", "USERS")])
        self.assertEqual(redis.gesetzt, ["b"])
        self.assertTrue(any("Redis hat sie verloren" in z for z in log.output),
                        "Stilles Reparieren verdeckt, dass Redis Zugaenge verliert")

    def test_er_laeuft_nicht_bei_jedem_takt(self):
        """Der Planer tickt alle 30 Sekunden — der zweite Aufruf innerhalb der
        Frist darf Redis nicht einmal fragen."""
        sek = int(re.search(r"_ACL_PRUEFUNG_ALLE_SEKUNDEN = (\d+)", self.SCHED).group(1))
        self.assertGreaterEqual(sek, 60)
        planer, redis = self._vorbereitet(ids=["a"], vorhanden=[])
        with self.assertLogs("app.services.scheduler_service", level="WARNING"):
            self._run(planer)
        self._run(planer)
        self.assertEqual(len(redis.client.aufrufe), 1)
        self.assertEqual(redis.gesetzt, ["a"])

    def test_er_ist_still_wenn_alles_stimmt(self):
        planer, redis = self._vorbereitet(ids=["a", "b"], vorhanden=["a", "b"])
        with self.assertNoLogs("app.services.scheduler_service", level="WARNING"):
            self._run(planer)
        self.assertEqual(redis.gesetzt, [])

    def test_ohne_eingeschaltete_acl_tut_er_nichts(self):
        planer, redis = self._vorbereitet(ids=["a"], vorhanden=[], acl_an=False)
        self._run(planer)
        self.assertEqual(redis.client.aufrufe, [])
        self.assertEqual(redis.gesetzt, [])

    def test_eine_unlesbare_liste_setzt_nichts_blind(self):
        planer, redis = self._vorbereitet(ids=["a"], vorhanden=[], fehler=RuntimeError("weg"))
        self._run(planer)
        self.assertEqual(redis.gesetzt, [])

    def test_ein_fehler_stoppt_den_planer_nicht(self):
        self.assertIn("[Scheduler] RedisACL error", self.SCHED)

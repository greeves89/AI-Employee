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

import unittest
from pathlib import Path

_MAIN = (Path(__file__).resolve().parents[1] / "app" / "main.py").read_text()
_REDIS = (Path(__file__).resolve().parents[1] / "app" / "services"
          / "redis_service.py").read_text()


def _block() -> str:
    return _MAIN.split("Redis-ACL-Nutzer der Agenten wiederherstellen", 1)[1][:2200]


class DieAclWirdBeimStartWiederhergestelltTests(unittest.TestCase):
    def test_es_gibt_den_aufruf_ueberhaupt(self):
        self.assertIn("ensure_agent_acl_user(_aid)", _MAIN)

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
        block = _REDIS.split("def agent_acl_password", 1)[1][:700]
        self.assertIn("settings.api_secret_key.encode()", block)
        self.assertIn('f"redis-acl:{agent_id}"', block)

    def test_das_setzen_ist_wiederholbar(self):
        """`ACL SETUSER` ersetzt die Regeln vollstaendig — sonst waere ein
        zweiter Start ein Problem."""
        self.assertIn("Idempotent (ACL SETUSER replaces the user's rules wholesale",
                      _REDIS)


if __name__ == "__main__":
    unittest.main()

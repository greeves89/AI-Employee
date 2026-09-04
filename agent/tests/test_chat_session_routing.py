"""Jedes Gespraech braucht seinen eigenen Faden.

Aus dem Betrieb gemeldet: "Er hat die Chats miteinander vermischt." Ursache war
der Wegweiser fuer eingehende Nachrichten: Fuer die Handy-App gab er den blossen
Kanalnamen zurueck, ohne die Sitzung. Alle Gespraeche eines Handys landeten damit
beim selben Bearbeiter — und der haelt den Gespraechsverlauf. Wer zwischen zwei
Chats wechselte, bekam den Verlauf des anderen mit.

Historisch war das richtig: Als der Wegweiser entstand, hatte die App genau ein
Gespraech. Sie verwaltet laengst Sitzungen und sendet die Kennung auch mit — hier
wurde sie nur verworfen.

Der Test prueft die Eigenschaft, nicht die Schreibweise: Zwei verschiedene
Sitzungen desselben Kanals muessen zu verschiedenen Schluesseln fuehren.
"""

import unittest

from app.chat_consumer import ChatConsumer


def _wegweiser():
    c = ChatConsumer.__new__(ChatConsumer)
    return c._source_key


class GespraechsfaedenTests(unittest.TestCase):
    def setUp(self):
        self.key = _wegweiser()

    def test_handy_trennt_zwei_gespraeche(self):
        """Der gemeldete Fehler."""
        a = self.key("ios", "sitzung-a", None)
        b = self.key("ios", "sitzung-b", None)
        self.assertNotEqual(a, b)

    def test_handy_bleibt_beim_selben_gespraech_stabil(self):
        """Sonst verlieren Wiederverbindungen den Faden."""
        self.assertEqual(
            self.key("ios", "sitzung-a", None),
            self.key("iphone", "sitzung-a", None),
        )

    def test_alle_kanaele_trennen_nach_sitzung(self):
        """Damit kein Kanal die Trennung erneut vergisst."""
        for kanal in ("ios", "iphone", "ipad", "webapp", "voice", "webapp_voice"):
            with self.subTest(kanal=kanal):
                self.assertNotEqual(
                    self.key(kanal, "eins", None),
                    self.key(kanal, "zwei", None),
                    f"{kanal} wirft die Sitzung weg — Gespraeche vermischen sich",
                )

    def test_kanaele_kommen_sich_nicht_ins_gehege(self):
        """Gleiche Sitzungskennung, anderer Kanal: getrennte Faeden."""
        schluessel = {
            self.key(k, "gleiche-sitzung", None)
            for k in ("ios", "webapp", "voice")
        }
        self.assertEqual(3, len(schluessel))

    def test_ohne_sitzung_bleibt_es_benutzbar(self):
        """Aeltere Aufrufer ohne Sitzungskennung duerfen nicht ins Leere laufen."""
        for kanal in ("ios", "webapp", "voice"):
            with self.subTest(kanal=kanal):
                self.assertTrue(self.key(kanal, None, None))

    def test_telegram_trennt_nach_unterhaltung(self):
        a = self.key("telegram", None, {"chat_id": 111})
        b = self.key("telegram", None, {"chat_id": 222})
        self.assertNotEqual(a, b)


if __name__ == "__main__":
    unittest.main()

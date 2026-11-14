"""Gezielter Stopp je Gespraech (chat_consumer._stopp_sitzung)."""
import unittest

from app.chat_consumer import _stopp_sitzung


class StoppSitzungTests(unittest.TestCase):
    def test_alter_stopp_haelt_alles_an(self):
        self.assertIsNone(_stopp_sitzung("stop"))
        self.assertIsNone(_stopp_sitzung(None))

    def test_gezielt(self):
        self.assertEqual(_stopp_sitzung('{"sitzung": "s1"}'), "s1")

    def test_kaputt_heisst_alles(self):
        for roh in ("{kaputt", '{"sitzung": 5}', '{"x": 1}', '{"sitzung": ""}'):
            self.assertIsNone(_stopp_sitzung(roh), roh)


if __name__ == "__main__":
    unittest.main()

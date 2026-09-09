"""Issue #706: ``POST /memory/save`` quittierte zu lange Feldwerte mit einem
nackten 500 statt mit einem sprechenden 422.

``MemorySave`` deklarierte ``room``, ``source`` und ``tags`` ohne Laengengrenze,
die zugehoerigen Spalten in ``models/memory.py`` haben aber welche
(``room`` 500, ``source`` 30, ``AgentMemoryTag.tag`` 100). Der zu lange Wert kam
so ungeprueft bis ins INSERT, Postgres warf ``value too long for type character
varying(...)``, und weil ``save_memory_core`` nur ``IntegrityError`` abfaengt,
blieb die ``DataError`` unbehandelt. Der Aufrufer sah nur "Internal Server
Error" ohne Hinweis auf das schuldige Feld und hielt das fuer einen Ausfall des
Gedaechtnisses.

Der Fehler faellt lokal nicht auf, weil er erst an der DB-Grenze entsteht — die
Modellgrenzen sind deshalb der einzige Ort, an dem er hier ueberhaupt sichtbar
wird. Der Test haelt die Grenzen an den Spalten fest: laeuft eine Spalte
irgendwann auseinander, bricht er, statt dass es wieder ein 500 im Betrieb wird.
"""

import unittest

from app.api.memory import MemorySave
from app.models.memory import AgentMemory, AgentMemoryTag
from pydantic import ValidationError


def _column_length(model, attr: str) -> int:
    return model.__table__.columns[attr].type.length


BASE = {"agent_id": "a1", "category": "learning", "key": "k", "content": "c"}


class MemorySaveFieldLengths(unittest.TestCase):
    def test_limits_match_the_db_columns(self):
        """Die Pydantic-Grenzen muessen den Spalten folgen, sonst schlaegt es
        wieder erst in der Datenbank fehl."""
        self.assertEqual(_column_length(AgentMemory, "room"), 500)
        self.assertEqual(_column_length(AgentMemory, "source"), 30)
        self.assertEqual(_column_length(AgentMemoryTag, "tag"), 100)

    def test_values_at_the_limit_are_accepted(self):
        """Gegenprobe nach unten: genau auf der Grenze darf nichts abgelehnt
        werden, sonst waere die Korrektur zu streng."""
        body = MemorySave(
            **BASE,
            room="r" * 500,
            source="s" * 30,
            tags=["t" * 100],
        )
        self.assertEqual(len(body.room), 500)
        self.assertEqual(len(body.source), 30)
        self.assertEqual(len(body.tags[0]), 100)

    def test_over_long_room_is_rejected(self):
        with self.assertRaises(ValidationError):
            MemorySave(**BASE, room="r" * 501)

    def test_over_long_source_is_rejected(self):
        with self.assertRaises(ValidationError):
            MemorySave(**BASE, source="s" * 31)

    def test_over_long_tag_is_rejected(self):
        """Tags liegen in einer eigenen Tabelle: eine Korrektur, die nur
        ``room``/``source`` begrenzt, laesst genau diesen Pfad offen."""
        with self.assertRaises(ValidationError):
            MemorySave(**BASE, tags=["t" * 101])

    def test_over_long_tag_is_rejected_even_beside_valid_tags(self):
        with self.assertRaises(ValidationError):
            MemorySave(**BASE, tags=["ok", "t" * 101])


if __name__ == "__main__":
    unittest.main()

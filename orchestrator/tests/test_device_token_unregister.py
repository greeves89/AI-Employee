"""Abmelden eines Geraets — damit Meldungen nicht beim Vorbesitzer bleiben.

Meldet sich jemand in der App ab, muss der Geraete-Schluessel weg. Sonst
schickt der Server die Meldungen des abgemeldeten Nutzers weiter an dieses
Geraet: Titel und Vorschautext stehen dann auf dem Sperrbildschirm, obwohl
dort niemand mehr angemeldet ist — und wer das Geraet als naechstes benutzt,
liest sie mit. Die Plattform ist nutzerbezogen; das waere ein Bruch dieser
Trennung.

Geprueft wird deshalb beides: dass ein eigener Schluessel wirklich verschwindet
UND dass die Abfrage nach dem Besitzer filtert. Ohne den zweiten Teil pruefte
der erste nur die Attrappe — eine Abfrage ohne Besitzerfilter wuerde jedem
erlauben, fremden Geraeten die Zustellung abzudrehen.
"""

import unittest

from app.api.notifications import DeviceRegister, unregister_device


class _Ergebnis:
    def __init__(self, treffer):
        self._treffer = treffer

    def scalar_one_or_none(self):
        return self._treffer


class _FakeDB:
    """Nur so viel Datenbank, wie der Endpunkt anfasst."""

    def __init__(self, treffer=None):
        self.treffer = treffer
        self.abfragen = []
        self.geloescht = []
        self.commits = 0

    async def execute(self, stmt):
        self.abfragen.append(stmt)
        return _Ergebnis(self.treffer)

    async def delete(self, obj):
        self.geloescht.append(obj)

    async def commit(self):
        self.commits += 1


class _Nutzer:
    def __init__(self, id_):
        self.id = id_


class GeraetAbmeldenTests(unittest.IsolatedAsyncioTestCase):
    async def test_eigener_schluessel_wird_geloescht(self):
        eintrag = object()
        db = _FakeDB(treffer=eintrag)
        antwort = await unregister_device(
            DeviceRegister(token="abc", platform="ios"),
            user=_Nutzer("u1"), db=db,
        )
        self.assertEqual([eintrag], db.geloescht)
        self.assertEqual(1, db.commits)
        self.assertEqual({"status": "unregistered"}, antwort)

    async def test_ohne_treffer_wird_nichts_geloescht(self):
        db = _FakeDB(treffer=None)
        antwort = await unregister_device(
            DeviceRegister(token="fremd", platform="ios"),
            user=_Nutzer("u2"), db=db,
        )
        self.assertEqual([], db.geloescht)
        self.assertEqual(0, db.commits)
        # Gleiche Antwort wie im Trefferfall: Der Aufrufer soll nicht ablesen
        # koennen, welche Schluessel es gibt.
        self.assertEqual({"status": "unregistered"}, antwort)

    async def test_abfrage_filtert_nach_besitzer(self):
        """Der eigentliche Schutz — ohne diesen Filter waere alles darueber Theater."""
        db = _FakeDB(treffer=None)
        await unregister_device(
            DeviceRegister(token="abc", platform="ios"),
            user=_Nutzer("u1"), db=db,
        )
        self.assertEqual(1, len(db.abfragen))
        sql = str(db.abfragen[0]).lower()
        self.assertIn("device_tokens.token", sql)
        self.assertIn("device_tokens.user_id", sql)
        # Beide Bedingungen muessen UND-verknuepft sein; ein ODER wuerde den
        # Besitzerfilter wirkungslos machen.
        bedingung = sql.split("where", 1)[1]
        self.assertIn("and", bedingung)
        self.assertNotIn(" or ", bedingung)


if __name__ == "__main__":
    unittest.main()

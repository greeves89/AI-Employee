"""``GET /health/auto-metrics`` zeigt nur die Agenten des Aufrufers.

Bis 1.350.1 lud der Endpunkt ALLE Agenten der Anlage: jeder angemeldete Nutzer
bekam Namen, Kosten und Fehlertexte fremder Agenten. Geprueft wird die Abfrage,
die wirklich an die Datenbank geht — nicht der Quelltext.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.api import health
from app.models.agent import Agent
from app.models.user import UserRole


class _Db:
    """Merkt sich die Agenten-Abfrage und liefert, was sie dem Filter nach liefern darf."""

    def __init__(self, agenten):
        self.agenten = agenten
        self.agenten_abfragen = []

    async def execute(self, stmt):
        sql = str(stmt)
        if "FROM agents" in sql and "FROM tasks" not in sql:
            self.agenten_abfragen.append(stmt)
            erlaubt = None
            for wert in stmt.compile().params.values():
                if isinstance(wert, (list, tuple, set)):
                    erlaubt = set(wert)
            treffer = [a for a in self.agenten if erlaubt is None or a.id in erlaubt]
            return MagicMock(scalars=lambda: MagicMock(all=lambda: treffer))
        return MagicMock(scalars=lambda: MagicMock(all=lambda: []))


AGENTEN = [Agent(id="a1", name="Agent von A", user_id="ua"), Agent(id="b1", name="Agent von B", user_id="ub")]


class AutoMetrics(unittest.IsolatedAsyncioTestCase):
    async def _abfrage(self, sichtbar):
        db = _Db(AGENTEN)
        with patch("app.core.ownership.visible_agent_ids", AsyncMock(return_value=sichtbar)):
            await health.get_auto_metrics(days=7, user=SimpleNamespace(id="ua", role=UserRole.MEMBER), db=db)
        return db

    def _ids(self, db):
        """Welche Agenten die Abfrage laut ihrem Filter liefert."""
        stmt = db.agenten_abfragen[0]
        erlaubt = None
        for wert in stmt.compile().params.values():
            if isinstance(wert, (list, tuple, set)):
                erlaubt = set(wert)
        return {a.id for a in AGENTEN if erlaubt is None or a.id in erlaubt}

    async def test_user_a_sieht_b_nicht(self):
        db = await self._abfrage({"a1"})
        self.assertEqual(self._ids(db), {"a1"})

    async def test_nutzer_ohne_agenten_sieht_nichts(self):
        """Eine leere Menge heisst „nichts" — nie „alles"."""
        db = await self._abfrage(set())
        self.assertIn(" IN ", str(db.agenten_abfragen[0]), "Die leere Menge muss als Filter ankommen.")
        self.assertEqual(self._ids(db), set())

    async def test_administrator_sieht_alle(self):
        db = await self._abfrage(None)
        self.assertEqual(self._ids(db), {"a1", "b1"})


if __name__ == "__main__":
    unittest.main()

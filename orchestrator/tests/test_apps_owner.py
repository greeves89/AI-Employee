"""Wer hat die App gebaut? — Besitzer in der Apps-Übersicht.

Die Frage kam vom Kunden und ist bei freigegebenen Apps die eigentliche: in der
Liste steht plötzlich etwas Fremdes, und bisher stand daneben nur der Name des
*Agenten*. Der sagt nichts darüber, von wem die App stammt.

Getestet wird gegen echtes SQL (in-memory SQLite), nicht gegen einen Stub — die
Auflösung ist eine ``IN``-Abfrage über die Nutzer, und ein Stub würde genau die
wegtesten. Mit geprüft: dass dabei die Mailadresse NICHT mitkommt. Wem eine App
freigegeben wurde, der soll den Namen sehen, nicht das Postfach.
"""

import unittest

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.apps_overview import _owner_names
from app.models.user import User, UserRole


def _user(uid: str, name: str, email: str) -> User:
    return User(id=uid, email=email, name=name, role=UserRole.MEMBER, password_hash="x")


class _Agent:
    """Nur die zwei Felder, die die Auflösung anfasst."""

    def __init__(self, agent_id: str, user_id):
        self.id = agent_id
        self.user_id = user_id


class OwnerNameTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(User.metadata.create_all, tables=[User.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        self.db = self.Session()
        self.db.add_all([
            _user("u-anna", "Anna Beck", "anna@example.com"),
            _user("u-bodo", "Bodo Klein", "bodo@example.com"),
        ])
        await self.db.commit()

    async def asyncTearDown(self):
        await self.db.close()
        await self.engine.dispose()

    async def test_it_resolves_the_names(self):
        got = await _owner_names([_Agent("a1", "u-anna"), _Agent("a2", "u-bodo")], self.db)
        self.assertEqual(got, {"u-anna": "Anna Beck", "u-bodo": "Bodo Klein"})

    async def test_one_query_for_many_agents(self):
        """Zehn Agenten desselben Nutzers dürfen keine zehn Abfragen werden."""
        agents = [_Agent(f"a{i}", "u-anna") for i in range(10)]
        got = await _owner_names(agents, self.db)
        self.assertEqual(got, {"u-anna": "Anna Beck"})

    async def test_no_mail_address_comes_along(self):
        got = await _owner_names([_Agent("a1", "u-anna")], self.db)
        self.assertNotIn("anna@example.com", str(got))

    async def test_an_agent_without_an_owner_is_not_an_error(self):
        """Systemagenten und Altbestände haben keine Zuordnung — die Liste soll
        dann einen leeren Besitzer zeigen, nicht abstürzen."""
        got = await _owner_names([_Agent("a1", None), _Agent("a2", "")], self.db)
        self.assertEqual(got, {})

    async def test_a_deleted_owner_simply_has_no_name(self):
        got = await _owner_names([_Agent("a1", "u-weg")], self.db)
        self.assertEqual(got, {})

    async def test_nothing_in_means_no_query_and_no_crash(self):
        self.assertEqual(await _owner_names([], self.db), {})


if __name__ == "__main__":
    unittest.main()

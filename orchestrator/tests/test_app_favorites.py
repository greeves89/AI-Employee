"""App-Favoriten: Anpinnen in der Uebersicht — und die Mandantentrennung dabei.

Der Schwerpunkt liegt nicht auf dem Anpinnen selbst (das ist ein Einfuegen),
sondern darauf, dass der Endpunkt kein Orakel wird: Wer beliebige
Projektnamen anpinnen koennte, koennte durch Ausprobieren herausfinden, welche
fremden Apps auf der Plattform existieren. Deshalb prueft er gegen dieselbe
Sichtbarkeitsliste, die auch die Uebersicht erzeugt.

Gegen ECHTES SQL (in-memory SQLite) wie bei den Freigaben: Die Eindeutigkeit
(user_id, project) und die Filterung passieren in der Datenbank, ein
Fake-Stub wuerde genau das wegtesten.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.app_favorite import AppFavorite


def _user(uid: str):
    return SimpleNamespace(id=uid, email=f"{uid}@example.invalid", role="MEMBER")


class AppFavoritenTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            # Nur diese eine Tabelle: create_all zieht sonst Modelle mit
            # PostgreSQL-eigenen Typen (JSONB) mit, die SQLite nicht kennt.
            await conn.run_sync(
                AppFavorite.metadata.create_all, tables=[AppFavorite.__table__]
            )
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    # --- Mandantentrennung: der eigentliche Punkt ---------------------------

    async def test_fremde_app_laesst_sich_nicht_anpinnen(self):
        """Wer die App nicht sieht, darf sie nicht anpinnen — und erfaehrt
        auch nicht, dass es sie gibt (404, nicht 403)."""
        from app.api.apps_overview import set_app_favorite

        async with self.Session() as db:
            with patch("app.api.apps_overview._sichtbare_projekte",
                       AsyncMock(return_value={"agent-aaaa1111-meine"})):
                with self.assertRaises(HTTPException) as ctx:
                    await set_app_favorite(
                        project="agent-bbbb2222-fremde",
                        favorite=True,
                        user=_user("u1"),
                        db=db,
                        docker=None,
                    )
        self.assertEqual(ctx.exception.status_code, 404)

        # Und es darf auch nichts geschrieben worden sein.
        async with self.Session() as db:
            treffer = (await db.execute(select(AppFavorite))).scalars().all()
        self.assertEqual(treffer, [])

    async def test_eigene_app_laesst_sich_anpinnen(self):
        from app.api.apps_overview import set_app_favorite

        async with self.Session() as db:
            with patch("app.api.apps_overview._sichtbare_projekte",
                       AsyncMock(return_value={"agent-aaaa1111-meine"})):
                r = await set_app_favorite(
                    project="agent-aaaa1111-meine", favorite=True,
                    user=_user("u1"), db=db, docker=None,
                )
        self.assertTrue(r["favorite"])

        async with self.Session() as db:
            treffer = (await db.execute(select(AppFavorite))).scalars().all()
        self.assertEqual(len(treffer), 1)
        self.assertEqual(treffer[0].user_id, "u1")

    async def test_freigegebene_app_darf_angepinnt_werden(self):
        """Eine mir freigegebene App steht in meiner Uebersicht — also darf ich
        sie auch anpinnen. Anpinnen verleiht keinen Zugriff, es merkt sich eine
        Vorliebe."""
        from app.api.apps_overview import set_app_favorite

        async with self.Session() as db:
            with patch("app.api.apps_overview._sichtbare_projekte",
                       AsyncMock(return_value={"agent-bbbb2222-geteilte"})):
                r = await set_app_favorite(
                    project="agent-bbbb2222-geteilte", favorite=True,
                    user=_user("u1"), db=db, docker=None,
                )
        self.assertTrue(r["favorite"])

    async def test_favoriten_sind_je_nutzer_getrennt(self):
        from app.api.apps_overview import set_app_favorite

        for uid in ("u1", "u2"):
            async with self.Session() as db:
                with patch("app.api.apps_overview._sichtbare_projekte",
                           AsyncMock(return_value={"agent-aaaa1111-app"})):
                    await set_app_favorite(
                        project="agent-aaaa1111-app", favorite=True,
                        user=_user(uid), db=db, docker=None,
                    )

        async with self.Session() as db:
            u1 = (await db.execute(
                select(AppFavorite).where(AppFavorite.user_id == "u1")
            )).scalars().all()
            u2 = (await db.execute(
                select(AppFavorite).where(AppFavorite.user_id == "u2")
            )).scalars().all()
        self.assertEqual(len(u1), 1)
        self.assertEqual(len(u2), 1)

    # --- Verhalten ---------------------------------------------------------

    async def test_zweimal_anpinnen_erzeugt_keine_dublette(self):
        from app.api.apps_overview import set_app_favorite

        for _ in range(2):
            async with self.Session() as db:
                with patch("app.api.apps_overview._sichtbare_projekte",
                           AsyncMock(return_value={"agent-aaaa1111-app"})):
                    await set_app_favorite(
                        project="agent-aaaa1111-app", favorite=True,
                        user=_user("u1"), db=db, docker=None,
                    )

        async with self.Session() as db:
            treffer = (await db.execute(select(AppFavorite))).scalars().all()
        self.assertEqual(len(treffer), 1)

    async def test_loesen_entfernt_den_eintrag(self):
        from app.api.apps_overview import set_app_favorite

        async with self.Session() as db:
            with patch("app.api.apps_overview._sichtbare_projekte",
                       AsyncMock(return_value={"agent-aaaa1111-app"})):
                await set_app_favorite(
                    project="agent-aaaa1111-app", favorite=True,
                    user=_user("u1"), db=db, docker=None,
                )
                await set_app_favorite(
                    project="agent-aaaa1111-app", favorite=False,
                    user=_user("u1"), db=db, docker=None,
                )

        async with self.Session() as db:
            treffer = (await db.execute(select(AppFavorite))).scalars().all()
        self.assertEqual(treffer, [])

    async def test_loesen_ohne_vorhandenen_eintrag_ist_harmlos(self):
        from app.api.apps_overview import set_app_favorite

        async with self.Session() as db:
            with patch("app.api.apps_overview._sichtbare_projekte",
                       AsyncMock(return_value={"agent-aaaa1111-app"})):
                r = await set_app_favorite(
                    project="agent-aaaa1111-app", favorite=False,
                    user=_user("u1"), db=db, docker=None,
                )
        self.assertFalse(r["favorite"])


if __name__ == "__main__":
    unittest.main()

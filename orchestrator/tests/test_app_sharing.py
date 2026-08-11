"""App-Freigaben (#467): die Zugriffs-Matrix des Freigabe-Kerns.

Hier hängt die komplette Autorisierung der App-Freigabe dran — der Proxy fragt
für JEDEN Request genau diese Funktionen. Entsprechend systematisch getestet:
Besitzer / namentlich / alle-Eingeloggten / öffentlicher Link, jeweils gekreuzt
mit anonym vs. eingeloggt, abgelaufen vs. gültig, richtiger vs. falscher Token,
richtiges vs. fremdes Projekt.

Gegen ECHTES SQL (in-memory SQLite) statt gegen einen Mock: die Projekt- und
Scope-Filterung passiert in der Query, ein handgestrickter Fake-DB-Stub würde
genau die Filter wegtesten, die hier den Schutz ausmachen.
"""

import unittest
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.dependencies  # noqa: F401 — makes `app.dependencies.…` patchable below

from app.core.app_sharing import (
    ACCESS_AUTHENTICATED,
    ACCESS_OWNER,
    ACCESS_PUBLIC,
    ACCESS_USER,
    agent_has_active_shares,
    is_app_owner,
    resolve_app_access,
    shared_projects_for_user,
)
from app.models.app_share import AppShare, hash_share_token

AGENT_ID = "020ea0d1cafe"
PROJECT = f"agent-{AGENT_ID[:8]}-demo-app"
OTHER_PROJECT = f"agent-{AGENT_ID[:8]}-other-app"

OWNER = SimpleNamespace(id="user-owner", role=None)
GUEST = SimpleNamespace(id="user-guest", role=None)
STRANGER = SimpleNamespace(id="user-stranger", role=None)


def _share(scope, **over):
    data = dict(
        id=f"aps_{uuid.uuid4().hex[:12]}",
        project=PROJECT,
        agent_id=AGENT_ID,
        scope=scope,
        user_id=None,
        token_hash=None,
        token_enc=None,
        expires_at=None,
        created_by=OWNER.id,
        created_at=datetime.now(timezone.utc),
    )
    data.update(over)
    return AppShare(**data)


def _denied():
    """Ownership check that always says no — the normal case for a guest."""
    return patch("app.dependencies.require_agent_access",
                 new=AsyncMock(side_effect=HTTPException(status_code=403, detail="Access denied")))


def _allowed():
    return patch("app.dependencies.require_agent_access", new=AsyncMock(return_value=None))


class AppSharingTestBase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(AppShare.metadata.create_all, tables=[AppShare.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        self.db = self.Session()

    async def asyncTearDown(self):
        await self.db.close()
        await self.engine.dispose()

    async def add(self, *shares):
        for s in shares:
            self.db.add(s)
        await self.db.commit()


class ResolveAccessTests(AppSharingTestBase):
    # ── Besitzer ──────────────────────────────────────────────────────────────

    async def test_owner_passes_without_any_share(self):
        with _allowed():
            got = await resolve_app_access(PROJECT, AGENT_ID, OWNER, None, self.db)
        self.assertEqual(got, ACCESS_OWNER)

    # ── Default deny ──────────────────────────────────────────────────────────

    async def test_authenticated_stranger_without_share_is_403(self):
        with _denied(), self.assertRaises(HTTPException) as cm:
            await resolve_app_access(PROJECT, AGENT_ID, STRANGER, None, self.db)
        self.assertEqual(cm.exception.status_code, 403)

    async def test_anonymous_without_share_is_401(self):
        """401 statt 403, damit das Frontend zur Anmeldung schickt."""
        with _denied(), self.assertRaises(HTTPException) as cm:
            await resolve_app_access(PROJECT, AGENT_ID, None, None, self.db)
        self.assertEqual(cm.exception.status_code, 401)

    # ── scope=user ────────────────────────────────────────────────────────────

    async def test_named_grantee_passes(self):
        await self.add(_share(ACCESS_USER, user_id=GUEST.id))
        with _denied():
            got = await resolve_app_access(PROJECT, AGENT_ID, GUEST, None, self.db)
        self.assertEqual(got, ACCESS_USER)

    async def test_share_for_someone_else_does_not_let_me_in(self):
        """Der eigentliche IDOR: eine Freigabe an A darf B nicht durchlassen."""
        await self.add(_share(ACCESS_USER, user_id=GUEST.id))
        with _denied(), self.assertRaises(HTTPException) as cm:
            await resolve_app_access(PROJECT, AGENT_ID, STRANGER, None, self.db)
        self.assertEqual(cm.exception.status_code, 403)

    async def test_named_share_does_not_admit_anonymous(self):
        await self.add(_share(ACCESS_USER, user_id=GUEST.id))
        with _denied(), self.assertRaises(HTTPException) as cm:
            await resolve_app_access(PROJECT, AGENT_ID, None, None, self.db)
        self.assertEqual(cm.exception.status_code, 401)

    async def test_expired_named_share_is_dead(self):
        await self.add(_share(
            ACCESS_USER, user_id=GUEST.id,
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        ))
        with _denied(), self.assertRaises(HTTPException) as cm:
            await resolve_app_access(PROJECT, AGENT_ID, GUEST, None, self.db)
        self.assertEqual(cm.exception.status_code, 403)

    # ── scope=authenticated ───────────────────────────────────────────────────

    async def test_any_logged_in_user_passes(self):
        await self.add(_share(ACCESS_AUTHENTICATED))
        with _denied():
            got = await resolve_app_access(PROJECT, AGENT_ID, STRANGER, None, self.db)
        self.assertEqual(got, ACCESS_AUTHENTICATED)

    async def test_authenticated_share_still_requires_a_login(self):
        """'Alle Eingeloggten' heißt eingeloggt — anonym bleibt draußen."""
        await self.add(_share(ACCESS_AUTHENTICATED))
        with _denied(), self.assertRaises(HTTPException) as cm:
            await resolve_app_access(PROJECT, AGENT_ID, None, None, self.db)
        self.assertEqual(cm.exception.status_code, 401)

    # ── scope=public ──────────────────────────────────────────────────────────

    async def test_public_token_admits_anonymous(self):
        await self.add(_share(
            ACCESS_PUBLIC, token_hash=hash_share_token("tok-good"),
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        ))
        with _denied():
            got = await resolve_app_access(PROJECT, AGENT_ID, None, "tok-good", self.db)
        self.assertEqual(got, ACCESS_PUBLIC)

    async def test_wrong_token_is_rejected(self):
        await self.add(_share(
            ACCESS_PUBLIC, token_hash=hash_share_token("tok-good"),
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        ))
        with _denied(), self.assertRaises(HTTPException) as cm:
            await resolve_app_access(PROJECT, AGENT_ID, None, "tok-guessed", self.db)
        self.assertEqual(cm.exception.status_code, 401)

    async def test_public_share_without_token_stays_closed(self):
        """Der Link IST das Geheimnis — ohne ihn bleibt die App zu."""
        await self.add(_share(
            ACCESS_PUBLIC, token_hash=hash_share_token("tok-good"),
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        ))
        with _denied(), self.assertRaises(HTTPException) as cm:
            await resolve_app_access(PROJECT, AGENT_ID, None, None, self.db)
        self.assertEqual(cm.exception.status_code, 401)

    async def test_expired_public_token_is_dead(self):
        await self.add(_share(
            ACCESS_PUBLIC, token_hash=hash_share_token("tok-old"),
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        ))
        with _denied(), self.assertRaises(HTTPException) as cm:
            await resolve_app_access(PROJECT, AGENT_ID, None, "tok-old", self.db)
        self.assertEqual(cm.exception.status_code, 401)

    async def test_token_of_another_project_does_not_transfer(self):
        """Ein gültiger Token für App A darf App B nicht aufschließen."""
        await self.add(_share(
            ACCESS_PUBLIC, project=OTHER_PROJECT, token_hash=hash_share_token("tok-other"),
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        ))
        with _denied(), self.assertRaises(HTTPException) as cm:
            await resolve_app_access(PROJECT, AGENT_ID, None, "tok-other", self.db)
        self.assertEqual(cm.exception.status_code, 401)

    async def test_logged_in_user_may_also_use_a_public_link(self):
        await self.add(_share(
            ACCESS_PUBLIC, token_hash=hash_share_token("tok-good"),
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        ))
        with _denied():
            got = await resolve_app_access(PROJECT, AGENT_ID, STRANGER, "tok-good", self.db)
        self.assertEqual(got, ACCESS_PUBLIC)


class VisibilityTests(AppSharingTestBase):
    async def test_named_share_shows_up_for_the_grantee_only(self):
        await self.add(_share(ACCESS_USER, user_id=GUEST.id))
        self.assertEqual(await shared_projects_for_user(GUEST, self.db), {PROJECT: ACCESS_USER})
        self.assertEqual(await shared_projects_for_user(STRANGER, self.db), {})

    async def test_authenticated_share_shows_up_for_everyone_logged_in(self):
        await self.add(_share(ACCESS_AUTHENTICATED))
        self.assertEqual(await shared_projects_for_user(STRANGER, self.db), {PROJECT: ACCESS_AUTHENTICATED})

    async def test_public_share_is_never_listed(self):
        """Ein öffentlicher Link hängt am Token, nicht an einer Person — er
        gehört in niemandes App-Liste."""
        await self.add(_share(
            ACCESS_PUBLIC, token_hash=hash_share_token("tok"),
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        ))
        self.assertEqual(await shared_projects_for_user(STRANGER, self.db), {})

    async def test_expired_share_is_not_listed(self):
        await self.add(_share(
            ACCESS_USER, user_id=GUEST.id,
            expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
        ))
        self.assertEqual(await shared_projects_for_user(GUEST, self.db), {})

    async def test_anonymous_sees_nothing(self):
        await self.add(_share(ACCESS_AUTHENTICATED))
        self.assertEqual(await shared_projects_for_user(None, self.db), {})


class PreGateTests(AppSharingTestBase):
    """``agent_has_active_shares`` ist das billige Vor-Gate im Proxy: ohne es
    könnte ein Anonymer über 404-vs-403 den Container-Namensraum abklopfen."""

    async def test_no_shares_at_all(self):
        self.assertFalse(await agent_has_active_shares(AGENT_ID, self.db))

    async def test_only_expired_shares_counts_as_none(self):
        await self.add(_share(
            ACCESS_PUBLIC, token_hash=hash_share_token("t"),
            expires_at=datetime.now(timezone.utc) - timedelta(days=1),
        ))
        self.assertFalse(await agent_has_active_shares(AGENT_ID, self.db))

    async def test_one_valid_share_opens_the_gate(self):
        await self.add(_share(ACCESS_USER, user_id=GUEST.id))
        self.assertTrue(await agent_has_active_shares(AGENT_ID, self.db))

    async def test_other_agents_shares_do_not_count(self):
        await self.add(_share(ACCESS_AUTHENTICATED, agent_id="deadbeef99", project="agent-deadbeef-x"))
        self.assertFalse(await agent_has_active_shares(AGENT_ID, self.db))


class ExpiryEdgeTests(unittest.TestCase):
    def test_naive_timestamp_is_read_as_utc(self):
        """SQLite (und alte Zeilen) liefern naive datetimes — ohne die
        Normalisierung würde der Vergleich werfen und nie ablaufen."""
        naive_past = (datetime.now(timezone.utc) - timedelta(hours=1)).replace(tzinfo=None)
        s = AppShare(id="x", project=PROJECT, agent_id=AGENT_ID, scope=ACCESS_PUBLIC,
                     expires_at=naive_past)
        self.assertTrue(s.is_expired())

    def test_no_expiry_never_expires(self):
        s = AppShare(id="x", project=PROJECT, agent_id=AGENT_ID, scope=ACCESS_USER)
        self.assertFalse(s.is_expired())


class _Query(dict):
    """Starlette's QueryParams also exposes multi_items() — der Proxy nutzt das,
    um Parameter (inkl. Wiederholungen) unter Auslassung des Tokens zu erhalten."""

    pairs: list[tuple[str, str]] | None = None

    def multi_items(self):
        return list(self.pairs) if self.pairs is not None else list(self.items())


class _FakeRequest:
    def __init__(self, query=None, cookies=None, headers=None):
        self.query_params = _Query(query or {})
        self.cookies = cookies or {}
        self.headers = headers or {}
        self.method = "GET"
        self.url = SimpleNamespace(
            path="/api/v1/agents/x/apps/proxy/app-web-1/8080/", scheme="https")

    async def body(self):
        return b""


class _FakeDocker:
    """Zählt mit, ob der Proxy überhaupt bis zu Docker vorgedrungen ist."""

    def __init__(self, labels=None):
        self.lookups: list[str] = []
        outer = self

        class _Containers:
            def get(self, name):
                outer.lookups.append(name)
                if labels is None:
                    raise RuntimeError("not found")
                return SimpleNamespace(labels=labels, attrs={}, name=name)

        self.client = SimpleNamespace(containers=_Containers())


class ProxyGateTests(AppSharingTestBase):
    """Die zwei Eigenschaften, die der Proxy nicht verlieren darf."""

    async def _call(self, user, docker, query=None):
        from app.api.docker_apps import proxy_app
        return await proxy_app(
            agent_id=AGENT_ID, container="app-web-1", port="8080", rest="",
            request=_FakeRequest(query=query), user=user, db=self.db, docker=docker,
        )

    async def test_no_share_rejects_before_docker_is_touched(self):
        """Ohne Freigabe darf der Proxy Docker gar nicht erst fragen — sonst
        verrät der Unterschied 404-vs-403, welche Container es gibt."""
        docker = _FakeDocker(labels={"com.docker.compose.project": PROJECT})
        with _denied(), self.assertRaises(HTTPException) as cm:
            await self._call(None, docker)
        self.assertEqual(cm.exception.status_code, 401)
        self.assertEqual(docker.lookups, [])

    async def test_valid_token_still_cannot_reach_a_foreign_container(self):
        """Kernzusage: eine Freigabe öffnet den ZUGRIFFSWEG, nie ein anderes Ziel.
        Gültiger Token + fremder Container (falsches compose-Label) = abgewiesen."""
        await self.add(_share(
            ACCESS_PUBLIC, token_hash=hash_share_token("tok-good"),
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        ))
        docker = _FakeDocker(labels={"com.docker.compose.project": "ai-employee"})  # Plattform!
        with _denied(), self.assertRaises(HTTPException) as cm:
            await self._call(None, docker, query={"__aie_share": "tok-good"})
        self.assertEqual(cm.exception.status_code, 401)   # anonym -> immer 401
        self.assertEqual(docker.lookups, ["app-web-1"])

    async def test_denials_are_indistinguishable_for_a_non_owner(self):
        """Wer EINE Freigabe für den Agenten hat, darf über die Fehlerantwort nicht
        herausfinden, welche ANDEREN Apps/Container es gibt: 'kennt den Container
        nicht', 'falsches Projekt' und 'nicht freigegeben' müssen gleich klingen."""
        await self.add(_share(
            ACCESS_PUBLIC, token_hash=hash_share_token("tok-good"),
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        ))
        # Beide Aufrufer-Arten einzeln prüfen: innerhalb einer Art muss die Antwort
        # konstant sein (zwischen anonym und eingeloggt darf sie sich unterscheiden —
        # 401 vs. 403 ist gewollt, damit das Frontend zur Anmeldung schicken kann).
        for caller, expected in ((None, 401), (STRANGER, 403)):
            answers = set()
            for docker in (
                _FakeDocker(labels=None),                                           # gibt es nicht
                _FakeDocker(labels={"com.docker.compose.project": "ai-employee"}),  # fremdes Projekt
                _FakeDocker(labels={"com.docker.compose.project": OTHER_PROJECT}),  # eigenes, nicht geteilt
            ):
                with self.subTest(caller=caller), _denied(), self.assertRaises(HTTPException) as cm:
                    await self._call(caller, docker)
                answers.add((cm.exception.status_code, cm.exception.detail))
            self.assertEqual(len(answers), 1, f"unterscheidbare Antworten: {answers}")
            self.assertEqual(next(iter(answers))[0], expected)

    async def test_owner_still_gets_the_precise_reason(self):
        """Dem Besitzer hilft eine klare Meldung — er darf ohnehin alles sehen."""
        docker = _FakeDocker(labels=None)
        with _allowed(), self.assertRaises(HTTPException) as cm:
            await self._call(OWNER, docker)
        self.assertEqual(cm.exception.status_code, 404)

    async def test_public_link_redirects_the_token_out_of_the_url(self):
        """Der Token darf nicht in `document.location` stehenbleiben: sonst hängt
        der Browser ihn als `Referer` an jede Unterabfrage — und liefert ihn damit
        genau der agent-geschriebenen App aus, die er absichern soll."""
        await self.add(_share(
            ACCESS_PUBLIC, token_hash=hash_share_token("tok-good"),
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        ))
        docker = _FakeDocker(labels={"com.docker.compose.project": PROJECT})
        with _denied():
            resp = await self._call(None, docker, query={"__aie_share": "tok-good", "page": "2"})

        self.assertEqual(resp.status_code, 303)
        location = resp.headers["location"]
        self.assertNotIn("tok-good", location)
        self.assertIn("page=2", location)          # andere Parameter bleiben erhalten
        cookie = resp.headers["set-cookie"]
        self.assertIn("tok-good", cookie)          # der Token lebt nur noch im Cookie
        self.assertIn("HttpOnly", cookie)
        self.assertIn("/proxy/app-web-1/8080/", cookie)   # exakt auf diese App begrenzt

    async def _proxy_to_upstream(self, *, user, query=None, headers=None, method="GET"):
        """Lässt den Proxy bis zum httpx-Aufruf durchlaufen und gibt zurück, was
        tatsächlich nach oben an die App geschickt wurde."""
        docker = _FakeDocker(labels={"com.docker.compose.project": PROJECT})
        sent: dict[str, object] = {}

        class _Upstream:
            status_code, content = 200, b"ok"
            headers = {"content-type": "text/html"}

        class _Client:
            def __init__(self, **_kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_a):
                return False

            async def request(self, _method, _url, params=None, headers=None, content=None):
                sent["params"] = params
                sent["headers"] = headers or {}
                return _Upstream()

        req = _FakeRequest(query=query, headers=headers)
        req.method = method
        from app.api.docker_apps import proxy_app
        with patch("app.api.docker_apps.httpx.AsyncClient", _Client):
            resp = await proxy_app(
                agent_id=AGENT_ID, container="app-web-1", port="8080", rest="",
                request=req, user=user, db=self.db, docker=docker,
            )
        return resp, sent

    async def test_credentials_never_reach_the_upstream_app(self):
        """Käme ein Referer mit Token an (Lesezeichen, alter Tab), darf er nicht nach
        oben durchgereicht werden — genauso wenig Plattform-Cookie oder Bearer-Token."""
        with _allowed():
            resp, sent = await self._proxy_to_upstream(user=OWNER, headers={
                "referer": "https://host/…/proxy/c/80/?__aie_share=tok-good",
                "cookie": "access_token=platform-session",
                "authorization": "Bearer platform-jwt",
                "x-custom": "durchreichen-ok",
            })

        lower = {k.lower() for k in sent["headers"]}
        self.assertNotIn("referer", lower)
        self.assertNotIn("cookie", lower)
        self.assertNotIn("authorization", lower)
        self.assertIn("x-custom", lower)          # normale Header laufen weiter durch
        self.assertNotIn("tok-good", str(sent))
        self.assertNotIn("platform-session", str(sent))
        # Und der Browser soll den Referer erst gar nicht anhängen.
        self.assertEqual(resp.headers["referrer-policy"], "no-referrer")

    async def test_share_token_is_never_forwarded_as_a_query_param(self):
        """Der Token darf die App auch NICHT über die weitergereichte Query erreichen —
        und zwar unabhängig von Methode und davon, welche Stufe den Zugriff erlaubt hat.

        Zwei Wege, auf denen er sonst durchrutscht: eine nicht-GET-Anfrage (die wird
        nicht umgeleitet) und ein GET, bei dem parallel eine 'alle Eingeloggten'-
        Freigabe existiert — dann greift der Public-Zweig gar nicht erst.
        """
        await self.add(
            _share(ACCESS_PUBLIC, token_hash=hash_share_token("tok-good"),
                   expires_at=datetime.now(timezone.utc) + timedelta(days=1)),
            _share(ACCESS_AUTHENTICATED),
        )
        query = {"__aie_share": "tok-good", "page": "2"}

        for method in ("POST", "GET"):
            with self.subTest(method=method), _denied():
                _resp, sent = await self._proxy_to_upstream(
                    user=STRANGER, query=query, method=method)
                params = dict(sent["params"] or [])
                self.assertNotIn("__aie_share", params)
                self.assertNotIn("tok-good", str(sent))
                self.assertEqual(params.get("page"), "2")   # der Rest kommt an

    async def test_repeated_query_keys_survive_the_proxy(self):
        """Die Query wird als Paar-Liste weitergereicht — `?a=1&a=2` darf nicht auf
        einen Wert zusammenfallen, nur weil wir einen Parameter herausfiltern."""
        req_query = _Query({"a": "2"})
        req_query.pairs = [("a", "1"), ("a", "2")]
        docker = _FakeDocker(labels={"com.docker.compose.project": PROJECT})
        sent: dict[str, object] = {}

        class _Upstream:
            status_code, content = 200, b"ok"
            headers = {"content-type": "text/html"}

        class _Client:
            def __init__(self, **_kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_a):
                return False

            async def request(self, _m, _u, params=None, headers=None, content=None):
                sent["params"] = params
                return _Upstream()

        req = _FakeRequest()
        req.query_params = req_query
        from app.api.docker_apps import proxy_app
        with _allowed(), patch("app.api.docker_apps.httpx.AsyncClient", _Client):
            await proxy_app(agent_id=AGENT_ID, container="app-web-1", port="8080", rest="",
                            request=req, user=OWNER, db=self.db, docker=docker)
        self.assertEqual(sent["params"], [("a", "1"), ("a", "2")])

    async def test_traversal_in_container_name_is_refused(self):
        from app.api.docker_apps import proxy_app
        docker = _FakeDocker(labels={"com.docker.compose.project": PROJECT})
        with self.assertRaises(HTTPException) as cm:
            await proxy_app(
                agent_id=AGENT_ID, container="..", port="8080", rest="",
                request=_FakeRequest(), user=None, db=self.db, docker=docker,
            )
        self.assertEqual(cm.exception.status_code, 403)
        self.assertEqual(docker.lookups, [])


class AuditTests(unittest.IsolatedAsyncioTestCase):
    """Freigaben landen im Audit-Log — aber der Link-Token NIEMALS."""

    class _RecordingDB:
        def __init__(self):
            self.added: list = []

        def add(self, obj):
            self.added.append(obj)

        async def commit(self):
            pass

    async def test_public_share_is_audited_without_the_token(self):
        from app.api import apps_overview as ao

        db = self._RecordingDB()
        agent = SimpleNamespace(id=AGENT_ID, name="Demo")
        with patch.object(ao, "_require_app_owner", new=AsyncMock(return_value=agent)):
            out = await ao.create_app_share(
                project=PROJECT,
                body=ao.ShareCreate(scope=ACCESS_PUBLIC, expires_in_days=3),
                user=OWNER, db=db,
            )

        token = out["token"]
        self.assertTrue(token)
        # Genug Entropie für einen Link, der ohne Login trägt.
        self.assertGreaterEqual(len(token), 32)

        audits = [o for o in db.added if o.__class__.__name__ == "AuditLog"]
        self.assertEqual(len(audits), 1)
        self.assertEqual(audits[0].event_type, "app_shared")
        self.assertNotIn(token, str(audits[0].meta))
        self.assertNotIn(token, str(audits[0].command))

    async def test_the_token_is_not_handed_out_by_default(self):
        """Seit 1.176.0 kann der Besitzer seinen Link wiedersehen — aber nur er,
        und nur da, wo die Aufrufkette das geprüft hat.

        Die Vorgabe bleibt deshalb: ohne ``with_token`` kein Token. Alles, was
        Freigegebene oder die Apps-Übersicht rendern, geht über diesen Weg.
        """
        from app.api.apps_overview import _share_dict

        s = _share(ACCESS_PUBLIC, token_hash=hash_share_token("super-secret"),
                   expires_at=datetime.now(timezone.utc) + timedelta(days=1))
        d = _share_dict(s)
        self.assertNotIn("token", d)
        self.assertTrue(d["has_token"])
        self.assertNotIn("super-secret", str(d))

    async def test_an_old_share_has_no_recoverable_link(self):
        """Zeilen von vor 1.176.0 haben keinen verschlüsselten Token. Die Liste
        darf dafür nichts erfinden — der Klartext ist dort wirklich weg."""
        from app.api.apps_overview import _share_dict

        s = _share(ACCESS_PUBLIC, token_hash=hash_share_token("alt"), token_enc=None)
        d = _share_dict(s, with_token=True)
        self.assertNotIn("token", d)
        self.assertTrue(d["has_token"])

    async def test_an_undecryptable_token_is_omitted_not_faked(self):
        """Nach einem Schlüsselwechsel funktioniert der Link weiter (geprüft wird
        gegen den Hash) — anzeigen lässt er sich nicht mehr. Dann lieber nichts
        als etwas Falsches zum Kopieren."""
        from app.api.apps_overview import _share_dict

        s = _share(ACCESS_PUBLIC, token_hash=hash_share_token("x"), token_enc="kein-fernet")
        d = _share_dict(s, with_token=True)
        self.assertNotIn("token", d)

    async def test_public_share_rejects_an_expiry_beyond_the_cap(self):
        from app.api import apps_overview as ao

        with patch.object(ao, "_require_app_owner",
                          new=AsyncMock(return_value=SimpleNamespace(id=AGENT_ID, name="Demo"))):
            with self.assertRaises(HTTPException) as cm:
                await ao.create_app_share(
                    project=PROJECT,
                    body=ao.ShareCreate(scope=ACCESS_PUBLIC, expires_in_days=9999),
                    user=OWNER, db=self._RecordingDB(),
                )
        self.assertEqual(cm.exception.status_code, 400)

    async def test_unknown_scope_is_refused(self):
        from app.api import apps_overview as ao

        with patch.object(ao, "_require_app_owner",
                          new=AsyncMock(return_value=SimpleNamespace(id=AGENT_ID, name="Demo"))):
            with self.assertRaises(HTTPException) as cm:
                await ao.create_app_share(
                    project=PROJECT, body=ao.ShareCreate(scope="admin"),
                    user=OWNER, db=self._RecordingDB(),
                )
        self.assertEqual(cm.exception.status_code, 400)


class OwnerCheckTests(AppSharingTestBase):
    async def test_anonymous_is_never_the_owner(self):
        """``is_app_owner(None)`` darf nie in die Rechteprüfung laufen."""
        with _allowed() as m:
            self.assertFalse(await is_app_owner(AGENT_ID, None, self.db))
            m.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()

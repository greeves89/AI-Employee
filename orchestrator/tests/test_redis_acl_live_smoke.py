"""Rauchtest der Agenten-ACL gegen ein ECHTES Redis (#589).

``test_redis_acl_scoping.py`` prueft die Regelliste als reine Funktion — ohne
Redis. Das ist gut und schnell, kann aber genau das nicht beantworten, worauf es
hier ankommt: **akzeptiert ein laufender Redis-Server diese Regeln ueberhaupt,
und bewirken sie das, was draufsteht?**

Der Agent, der #589 gebaut hat, hat das selbst so aufgeschrieben und vom
Einschalten abgeraten, solange dieser Test fehlt („unverified-against-a-live-Redis
infrastructure — flip only after a real redis-server ACL smoke test"). Das Risiko
ist einseitig: eine Regelliste, die der Server anders versteht als gedacht, sperrt
im Zweifel ALLE Agenten von Redis aus.

Die heikelste Stelle ist der Redis-7-Selektor ``(~agent:*:messages +lpush)``. Er
soll genau EIN Kommando auf einem fremden Schluesselraum erlauben — das Postfach
eines Kollegen befuellen, ohne es lesen oder leeren zu koennen. Ob die Klammer-
Syntax stimmt und ob der Selektor wirklich nur ``LPUSH`` freigibt, entscheidet
allein der Server.

**Ausfuehren:** ``REDIS_SMOKE_URL=redis://host:6379/0 pytest
tests/test_redis_acl_live_smoke.py``. Ohne die Variable ueberspringt sich der
Test, damit die normale Testsuite ohne Redis gruen bleibt.

**Nie gegen ein produktives Redis laufen lassen** — der Test legt ACL-Nutzer an
und wieder ab.
"""

import os
import unittest

import redis.asyncio as aioredis
from redis.exceptions import AuthenticationError, NoPermissionError, ResponseError

from app.services.redis_service import RedisService, agent_acl_username

SMOKE_URL = os.environ.get("REDIS_SMOKE_URL", "").strip()

ICH = "aaa11111"      # der Agent, dessen Zugang geprueft wird
KOLLEGE = "bbb22222"  # ein anderer Agent — an dessen Daten darf ICH nicht


@unittest.skipUnless(SMOKE_URL, "REDIS_SMOKE_URL nicht gesetzt — Rauchtest uebersprungen")
class AgentAclAgainstLiveRedisTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.admin = RedisService(redis_url=SMOKE_URL)
        await self.admin.connect()
        # Vorherige Laeufe hinterlassen nichts.
        for aid in (ICH, KOLLEGE):
            await self.admin.revoke_agent_acl_user(aid)
        self.scoped_url = await self.admin.ensure_agent_acl_user(ICH)
        self.agent = aioredis.from_url(self.scoped_url, decode_responses=True)
        # Fremde Daten anlegen — mit Adminrechten, damit ICH sie nachher nicht anlegen muss.
        await self.admin.client.set(f"agent:{KOLLEGE}:secret", "vertraulich")
        await self.admin.client.delete(f"agent:{KOLLEGE}:messages")

    async def asyncTearDown(self):
        try:
            await self.agent.aclose()
        except Exception:
            pass
        for aid in (ICH, KOLLEGE):
            await self.admin.revoke_agent_acl_user(aid)
        await self.admin.client.delete(f"agent:{KOLLEGE}:secret", f"agent:{KOLLEGE}:messages")
        await self.admin.disconnect()

    # ── Der Server nimmt die Regeln ueberhaupt an ────────────────────────────

    async def test_the_rule_set_is_accepted_by_a_real_server(self):
        """Schon das ist nicht selbstverstaendlich: falsche Syntax scheitert hier
        mit ResponseError, und im Betrieb haette es jeden Agentenstart zerlegt."""
        users = await self.admin.client.execute_command("ACL", "USERS")
        self.assertIn(agent_acl_username(ICH), users)

    async def test_the_agent_can_authenticate_with_its_scoped_url(self):
        self.assertEqual(await self.agent.ping(), True)

    # ── Was der Agent koennen MUSS ──────────────────────────────────────────

    async def test_it_may_use_its_own_key_space(self):
        await self.agent.set(f"agent:{ICH}:status", "working")
        self.assertEqual(await self.agent.get(f"agent:{ICH}:status"), "working")

    async def test_it_may_publish_on_its_own_and_the_global_channels(self):
        for kanal in (f"agent:{ICH}:logs", "agents:logs:all",
                      "task:started", "task:completions", "chat:completions"):
            with self.subTest(kanal=kanal):
                await self.agent.publish(kanal, "x")

    async def test_it_may_drop_a_message_into_a_colleagues_inbox(self):
        """Der ganze Zweck des Selektors — ``send_message`` macht genau das."""
        await self.agent.lpush(f"agent:{KOLLEGE}:messages", "hallo")
        self.assertEqual(await self.admin.client.llen(f"agent:{KOLLEGE}:messages"), 1)

    async def test_its_own_meeting_response_key_works(self):
        await self.agent.set(f"meeting:raum7:response:{ICH}", "da")

    # ── Was der Agent NICHT koennen darf ────────────────────────────────────

    async def test_it_cannot_read_a_colleagues_keys(self):
        with self.assertRaises(NoPermissionError):
            await self.agent.get(f"agent:{KOLLEGE}:secret")

    async def test_it_cannot_read_or_drain_a_colleagues_inbox(self):
        """Der Selektor gibt LPUSH frei — und sonst nichts. Waere er zu breit,
        koennte ein Agent die Post eines anderen mitlesen oder loeschen."""
        for aufruf in (
            self.agent.lrange(f"agent:{KOLLEGE}:messages", 0, -1),
            self.agent.lpop(f"agent:{KOLLEGE}:messages"),
            self.agent.delete(f"agent:{KOLLEGE}:messages"),
        ):
            with self.assertRaises(NoPermissionError):
                await aufruf

    async def test_it_cannot_impersonate_a_colleague_on_their_log_channel(self):
        """Das ist der Missstand, den #589 beheben soll: heute kann jeder Agent
        auf den Kanal jedes anderen schreiben."""
        with self.assertRaises(NoPermissionError):
            await self.agent.publish(f"agent:{KOLLEGE}:logs", "gefaelscht")

    async def test_it_cannot_run_admin_commands(self):
        for name, args in (
            ("CONFIG", ("GET", "maxmemory")),
            ("ACL", ("WHOAMI",)),
            ("CLIENT", ("LIST",)),
        ):
            with self.subTest(kommando=name):
                with self.assertRaises((NoPermissionError, ResponseError)):
                    await self.agent.execute_command(name, *args)

    async def test_it_cannot_wipe_the_instance(self):
        for kommando in ("FLUSHALL", "FLUSHDB", "KEYS"):
            with self.subTest(kommando=kommando):
                with self.assertRaises((NoPermissionError, ResponseError)):
                    await self.agent.execute_command(
                        kommando, *(("*",) if kommando == "KEYS" else ())
                    )

    # ── Betrieb ─────────────────────────────────────────────────────────────

    async def test_applying_it_twice_converges(self):
        """Wird bei jedem Agentenstart aufgerufen — ein zweiter Aufruf darf den
        Zugang nicht zerschiessen."""
        zweite_url = await self.admin.ensure_agent_acl_user(ICH)
        self.assertEqual(zweite_url, self.scoped_url)
        self.assertEqual(await self.agent.ping(), True)

    async def test_revoking_locks_the_agent_out(self):
        await self.admin.revoke_agent_acl_user(ICH)
        frisch = aioredis.from_url(self.scoped_url, decode_responses=True)
        try:
            # ``AuthenticationError`` erbt in redis-py von ConnectionError, nicht
            # von ResponseError — der entzogene Nutzer existiert schlicht nicht mehr.
            with self.assertRaises((AuthenticationError, ResponseError)):
                await frisch.ping()
        finally:
            await frisch.aclose()


if __name__ == "__main__":
    unittest.main()

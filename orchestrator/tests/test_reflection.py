"""Reflection ("Nachtschicht") — MCDC tests for the security-relevant branching.

Covers:
  1. save_memory_core conflict matrix: (key kind) x (similarity tier) x
     (override) x (allow_supersede) — the contract the reflection review
     modes are built on.
  2. ReflectionService._write_memory mode routing: (auto|hybrid|strict) x
     (new|superseded|conflict) -> direct write vs. pending approval.
  3. Watermark parsing (first run / valid / garbage).
"""

import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

# Local test runs don't have the docker SDK (only the container image does) —
# stub it so importing the API layer works (same pattern as test_bridge_auth).
_docker_stub = types.ModuleType("docker")
_docker_stub.from_env = MagicMock()
_docker_errors_stub = types.ModuleType("docker.errors")
_docker_errors_stub.NotFound = type("NotFound", (Exception,), {})
_docker_errors_stub.APIError = type("APIError", (Exception,), {})
_docker_stub.errors = _docker_errors_stub
_docker_models_stub = types.ModuleType("docker.models")
_docker_containers_stub = types.ModuleType("docker.models.containers")
_docker_containers_stub.Container = type("Container", (), {})
_docker_models_stub.containers = _docker_containers_stub
_docker_stub.models = _docker_models_stub
sys.modules.setdefault("docker", _docker_stub)
sys.modules.setdefault("docker.errors", _docker_errors_stub)
sys.modules.setdefault("docker.models", _docker_models_stub)
sys.modules.setdefault("docker.models.containers", _docker_containers_stub)

# test_bridge_auth (collected earlier) replaces app.db.session with a stub that
# only has async_session_factory — patch the attrs our import chain needs.
_dbs = sys.modules.get("app.db.session")
if _dbs is not None:
    for _attr in ("get_db", "engine"):
        if not hasattr(_dbs, _attr):
            setattr(_dbs, _attr, MagicMock())

from app.api.memory import MemoryConflict, MemorySave, save_memory_core  # noqa: E402
from app.models.memory import AgentMemory
from app.services.reflection_service import ReflectionService


def _fake_db(single_existing=None):
    """AsyncSession stand-in for save_memory_core."""
    db = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = single_existing
    db.execute = AsyncMock(return_value=result)
    db.scalar = AsyncMock(return_value=None)
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    db.refresh = AsyncMock()
    return db


def _existing_memory(content="Server ist 10.0.0.5", key="server-ip", mem_id=42):
    m = AgentMemory(agent_id="a1", category="fact", key=key, content=content)
    m.id = mem_id
    return m


def _body(**kw):
    defaults = dict(agent_id="a1", category="fact", key="multi-key",
                    content="Server ist 10.0.0.9", importance=2, source="reflection")
    defaults.update(kw)
    return MemorySave(**defaults)


class SaveMemoryCoreMatrixTests(unittest.IsolatedAsyncioTestCase):
    """Multi-key path: similarity tiers x override x allow_supersede."""

    async def _run_multi(self, sim, override, allow_supersede, existing=None):
        existing = existing or _existing_memory()
        with patch("app.api.memory._find_similar_memory",
                   new=AsyncMock(return_value=(existing, sim))), \
             patch("app.api.memory.classify_key", return_value="multi"):
            db = _fake_db()
            body = _body(override=override)
            return await save_memory_core(db, body, allow_supersede=allow_supersede), existing

    async def test_hard_dedup_supersedes_when_allowed(self):
        (mem, superseded_id), existing = await self._run_multi(0.95, False, True)
        self.assertEqual(superseded_id, existing.id)
        self.assertEqual(existing.superseded_by, mem.id)

    async def test_hard_dedup_conflicts_when_supersede_blocked(self):
        with self.assertRaises(MemoryConflict) as ctx:
            await self._run_multi(0.95, False, False)
        self.assertEqual(ctx.exception.kind, "supersede")

    async def test_soft_similarity_without_override_is_contradiction(self):
        for allow in (True, False):  # MCDC: allow_supersede must NOT mask this branch
            with self.assertRaises(MemoryConflict) as ctx:
                await self._run_multi(0.90, False, allow)
            self.assertEqual(ctx.exception.kind, "contradiction")

    async def test_soft_similarity_with_override_supersedes_when_allowed(self):
        (mem, superseded_id), existing = await self._run_multi(0.90, True, True)
        self.assertEqual(superseded_id, existing.id)

    async def test_soft_similarity_with_override_conflicts_when_blocked(self):
        with self.assertRaises(MemoryConflict) as ctx:
            await self._run_multi(0.90, True, False)
        self.assertEqual(ctx.exception.kind, "supersede")

    async def test_low_similarity_inserts_new_row(self):
        (mem, superseded_id), _ = await self._run_multi(0.50, False, False)
        self.assertIsNone(superseded_id)

    async def test_single_key_identical_content_is_noop(self):
        existing = _existing_memory(key="preferred_style", content="gleich")
        with patch("app.api.memory.classify_key", return_value="single"):
            db = _fake_db(single_existing=existing)
            mem, superseded_id = await save_memory_core(
                db, _body(key="preferred_style", content="gleich"), allow_supersede=False
            )
        self.assertIs(mem, existing)
        self.assertIsNone(superseded_id)

    async def test_single_key_change_conflicts_when_blocked(self):
        existing = _existing_memory(key="preferred_style", content="alt")
        with patch("app.api.memory.classify_key", return_value="single"):
            db = _fake_db(single_existing=existing)
            with self.assertRaises(MemoryConflict) as ctx:
                await save_memory_core(
                    db, _body(key="preferred_style", content="neu"), allow_supersede=False
                )
        self.assertEqual(ctx.exception.kind, "supersede")

    async def test_single_key_change_supersedes_when_allowed(self):
        existing = _existing_memory(key="preferred_style", content="alt")
        with patch("app.api.memory.classify_key", return_value="single"):
            db = _fake_db(single_existing=existing)
            mem, superseded_id = await save_memory_core(
                db, _body(key="preferred_style", content="neu"), allow_supersede=True
            )
        self.assertEqual(superseded_id, existing.id)

    async def test_source_is_persisted_on_new_memory(self):
        with patch("app.api.memory._find_similar_memory",
                   new=AsyncMock(return_value=(None, 0.0))), \
             patch("app.api.memory.classify_key", return_value="multi"):
            db = _fake_db()
            mem, _ = await save_memory_core(db, _body(source="compaction"), allow_supersede=True)
        self.assertEqual(mem.source, "compaction")


class WriteMemoryModeRoutingTests(unittest.IsolatedAsyncioTestCase):
    """ReflectionService._write_memory: mode x outcome -> write vs approval."""

    def setUp(self):
        self.svc = ReflectionService()
        self.svc._queue_approval = AsyncMock()
        self.db = _fake_db()
        self.stats = {"facts_new": 0, "facts_superseded": 0, "pending_approvals": 0}
        self.proposal = dict(agent_id="a1", category="fact", key="k",
                             content="Inhalt lang genug", importance=2,
                             confidence=0.7, source="reflection", override=False)

    async def test_strict_always_queues_approval(self):
        with patch("app.api.memory.save_memory_core", new=AsyncMock()) as save:
            await self.svc._write_memory(self.db, "strict", 1, self.proposal, self.stats)
            save.assert_not_awaited()
        self.svc._queue_approval.assert_awaited_once()

    async def test_hybrid_new_fact_applies_directly(self):
        mem = _existing_memory(mem_id=7)
        with patch("app.api.memory.save_memory_core",
                   new=AsyncMock(return_value=(mem, None))) as save:
            await self.svc._write_memory(self.db, "hybrid", 1, self.proposal, self.stats)
            self.assertFalse(save.call_args.kwargs["allow_supersede"])
        self.assertEqual(self.stats["facts_new"], 1)
        self.svc._queue_approval.assert_not_awaited()

    async def test_hybrid_conflict_queues_approval(self):
        conflict = MemoryConflict("supersede", _existing_memory(), 0.93)
        with patch("app.api.memory.save_memory_core", new=AsyncMock(side_effect=conflict)):
            await self.svc._write_memory(self.db, "hybrid", 1, self.proposal, self.stats)
        self.svc._queue_approval.assert_awaited_once()
        # The BEFORE snapshot must reach the approval payload (Vorher/Nachher UI)
        before = self.svc._queue_approval.call_args.args[5]
        self.assertEqual(before["id"], 42)

    async def test_auto_supersede_counts_and_allows(self):
        mem = _existing_memory(mem_id=7)
        with patch("app.api.memory.save_memory_core",
                   new=AsyncMock(return_value=(mem, 42))) as save:
            proposal = dict(self.proposal, override=True)
            await self.svc._write_memory(self.db, "auto", 1, proposal, self.stats)
            self.assertTrue(save.call_args.kwargs["allow_supersede"])
        self.assertEqual(self.stats["facts_superseded"], 1)
        self.svc._queue_approval.assert_not_awaited()


class WatermarkTests(unittest.TestCase):
    def test_valid_iso_is_parsed(self):
        now = datetime.now(timezone.utc)
        marks = {"a1": "2026-07-08T01:00:00+00:00"}
        got = ReflectionService._watermark_for(marks, "a1", now)
        self.assertEqual(got.isoformat(), "2026-07-08T01:00:00+00:00")

    def test_missing_falls_back_to_24h(self):
        now = datetime.now(timezone.utc)
        got = ReflectionService._watermark_for({}, "a1", now)
        self.assertEqual(got, now - timedelta(hours=24))

    def test_garbage_falls_back_to_24h(self):
        now = datetime.now(timezone.utc)
        got = ReflectionService._watermark_for({"a1": "not-a-date"}, "a1", now)
        self.assertEqual(got, now - timedelta(hours=24))


if __name__ == "__main__":
    unittest.main()

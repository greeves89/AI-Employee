"""Second Brain memory auto-linker (issue #157) — unit tests for the linking logic.

Covers the contract of ``memory_linker.link_memory``:
  1. Disabled embedding service        -> no-op (0 links, nothing added)
  2. Memory has no embedding           -> no-op (0 links)
  3. Candidates above threshold        -> links created, auto_generated + similarity
  4. Threshold boundary                -> a candidate below 0.75 stops the loop
  5. Existing link (either direction)  -> skipped (manual links respected)
and ``backfill_all`` aggregating link_memory over every embedded memory.

The DB is a stand-in: ``db.execute`` returns purpose-built result objects in call
order (embedding row -> candidate rows -> one existence probe per surviving
candidate). No real database or embedding model is imported.
"""

import sys
import types
import unittest
from unittest.mock import AsyncMock, MagicMock

# Stub the embedding service module so importing the linker never pulls the heavy
# model deps; individual tests flip ``enabled`` on the returned service. The prior
# module (if any) is restored in tearDownModule so later tests get the real one.
_EMB_NAME = "app.services.embedding_service"
_emb_prev = sys.modules.get(_EMB_NAME)
_emb_stub = types.ModuleType(_EMB_NAME)
_svc = MagicMock()
_svc.enabled = True
_emb_stub.get_embedding_service = MagicMock(return_value=_svc)
sys.modules[_EMB_NAME] = _emb_stub

from app.services import memory_linker  # noqa: E402
from app.services.memory_linker import link_memory, backfill_all  # noqa: E402


def tearDownModule():
    """Undo the module-level embedding_service stub so it never leaks into the
    rest of the suite (e.g. test_skill_semantic_search uses the real service)."""
    if _emb_prev is not None:
        sys.modules[_EMB_NAME] = _emb_prev
    else:
        sys.modules.pop(_EMB_NAME, None)


def _result(*, fetchone=None, fetchall=None, first=None):
    r = MagicMock()
    r.fetchone.return_value = fetchone
    r.fetchall.return_value = fetchall or []
    r.first.return_value = first
    return r


def _fake_db(execute_results):
    db = MagicMock()
    db.execute = AsyncMock(side_effect=list(execute_results))
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    return db


class LinkMemoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _svc.enabled = True  # reset between tests

    async def test_disabled_embedding_service_is_noop(self):
        _svc.enabled = False
        db = _fake_db([])
        created = await link_memory(1, "agent-1", db)
        self.assertEqual(created, 0)
        db.add.assert_not_called()
        db.execute.assert_not_called()

    async def test_missing_embedding_is_noop(self):
        db = _fake_db([_result(fetchone=None)])  # embedding lookup returns nothing
        created = await link_memory(1, "agent-1", db)
        self.assertEqual(created, 0)
        db.add.assert_not_called()

    async def test_creates_links_for_candidates_above_threshold(self):
        db = _fake_db([
            _result(fetchone=("[0.1, 0.2]",)),                 # source embedding
            _result(fetchall=[(2, 0.91), (3, 0.80)]),          # candidates
            _result(first=None),                                # 2: no existing link
            _result(first=None),                                # 3: no existing link
        ])
        created = await link_memory(1, "agent-1", db)
        self.assertEqual(created, 2)
        self.assertEqual(db.add.call_count, 2)
        db.commit.assert_awaited()
        link = db.add.call_args_list[0].args[0]
        self.assertEqual(link.source_id, 1)
        self.assertEqual(link.target_id, 2)
        self.assertEqual(link.relation, memory_linker.RELATION)
        self.assertTrue(link.auto_generated)
        self.assertAlmostEqual(link.similarity, 0.91, places=4)

    async def test_stops_at_first_candidate_below_threshold(self):
        # Candidates are distance-ordered; the linker must BREAK (not skip) at the
        # first sub-threshold row, so the 0.60 candidate never becomes a link.
        db = _fake_db([
            _result(fetchone=("[0.1]",)),
            _result(fetchall=[(2, 0.90), (3, 0.60), (4, 0.99)]),
            _result(first=None),   # only candidate 2 gets an existence probe
        ])
        created = await link_memory(1, "agent-1", db)
        self.assertEqual(created, 1)
        self.assertEqual(db.add.call_count, 1)
        self.assertEqual(db.add.call_args_list[0].args[0].target_id, 2)

    async def test_existing_link_is_skipped(self):
        # A pre-existing link (either direction) must be respected: candidate 2 has
        # one -> skipped; candidate 3 is fresh -> linked.
        db = _fake_db([
            _result(fetchone=("[0.1]",)),
            _result(fetchall=[(2, 0.90), (3, 0.88)]),
            _result(first=(2,)),    # 2: link already exists -> continue
            _result(first=None),    # 3: fresh -> link
        ])
        created = await link_memory(1, "agent-1", db)
        self.assertEqual(created, 1)
        self.assertEqual(db.add.call_count, 1)
        self.assertEqual(db.add.call_args_list[0].args[0].target_id, 3)

    async def test_no_candidates_commits_nothing(self):
        db = _fake_db([
            _result(fetchone=("[0.1]",)),
            _result(fetchall=[]),
        ])
        created = await link_memory(1, "agent-1", db)
        self.assertEqual(created, 0)
        db.add.assert_not_called()
        db.commit.assert_not_awaited()

    async def test_exception_rolls_back_and_returns_zero(self):
        db = MagicMock()
        db.execute = AsyncMock(side_effect=RuntimeError("boom"))
        db.rollback = AsyncMock()
        created = await link_memory(1, "agent-1", db)
        self.assertEqual(created, 0)
        db.rollback.assert_awaited()


class BackfillAllTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _svc.enabled = True

    async def test_backfill_iterates_all_embedded_memories(self):
        # First execute() lists the memories; link_memory is patched to count calls.
        db = MagicMock()
        db.execute = AsyncMock(return_value=_result(fetchall=[(1, "a1"), (2, "a1"), (3, "a2")]))

        calls = []

        async def _fake_link(mem_id, agent_id, _db):
            calls.append((mem_id, agent_id))
            return 2

        orig = memory_linker.link_memory
        memory_linker.link_memory = _fake_link
        try:
            stats = await backfill_all(db)
        finally:
            memory_linker.link_memory = orig

        self.assertEqual(stats["memories_processed"], 3)
        self.assertEqual(stats["links_created"], 6)
        self.assertEqual(calls, [(1, "a1"), (2, "a1"), (3, "a2")])


if __name__ == "__main__":
    unittest.main()

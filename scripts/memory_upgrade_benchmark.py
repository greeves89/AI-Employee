"""Memory System Upgrade — Quality Benchmark (issue #24).

Does this upgrade actually improve retrieval quality, or is it just
structural rearrangement? This script answers that question with
measurable numbers.

It seeds a synthetic dataset directly into the DB (bypassing the API,
so we can control timestamps + access counts), then runs the same
queries through:

  Baseline  — pure cosine similarity (what semantic-search used to do)
  Upgraded  — multi-strategy scoring (0.5/0.3/0.15/0.05)

and compares:
  1. Ranking differences for the same query
  2. Effect of the room filter
  3. Effect of superseded_by filtering
  4. Hybrid decay — transient vs permanent over 6 months
  5. Access_count boost — frequently-used memory beats fresh-but-ignored

Run with:
    docker compose exec orchestrator python -m scripts.memory_upgrade_benchmark

Or from the host:
    python3 scripts/memory_upgrade_benchmark.py
"""

from __future__ import annotations

import asyncio
import json
import math
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp

BASE_URL = "http://localhost:8000"
BENCH_AGENT_ID: str | None = None  # will be picked from DB
BENCH_ROOM = "bench:memory-upgrade-q1"


# ──────────────────────────────────────────────────────────────────────────────
# utilities
# ──────────────────────────────────────────────────────────────────────────────


def psql(sql: str) -> str:
    """Run raw SQL in the postgres container. Returns stdout."""
    r = subprocess.run(
        ["docker", "exec", "ai-employee-postgres", "psql", "-U", "ai_employee",
         "-d", "ai_employee", "-tAc", sql],
        capture_output=True, text=True, timeout=20,
    )
    if r.returncode != 0:
        raise RuntimeError(f"psql failed: {r.stderr}")
    return r.stdout.strip()


def mint_admin_token() -> str:
    return subprocess.run(
        ["docker", "exec", "ai-employee-orchestrator", "python", "-c",
         "from app.core.auth import create_access_token\n"
         "from app.db.session import async_session_factory\n"
         "from app.models.user import User, UserRole\n"
         "from sqlalchemy import select\n"
         "import asyncio\n"
         "async def mint():\n"
         "    async with async_session_factory() as db:\n"
         "        user = await db.scalar(select(User).where(User.role == UserRole.ADMIN))\n"
         "        print(create_access_token(user.id, user.role))\n"
         "asyncio.run(mint())"],
        capture_output=True, text=True, timeout=15,
    ).stdout.strip()


def mint_agent_token(agent_id: str) -> str:
    return subprocess.run(
        ["docker", "exec", "ai-employee-orchestrator", "python", "-c",
         f"from app.dependencies import make_agent_token; print(make_agent_token('{agent_id}'))"],
        capture_output=True, text=True, timeout=10,
    ).stdout.strip()


def format_row(prefix: str, items: list[dict]) -> str:
    lines = [f"  {prefix}"]
    for i, m in enumerate(items, 1):
        room = m.get("room") or "-"
        sim = m.get("similarity", 0)
        score = m.get("score", sim)
        content_preview = (m.get("content") or "")[:55]
        lines.append(f"    {i:2d}. [id={m['id']:>4}] sim={sim:.3f} score={score:.3f}  "
                     f"room={room[-30:]:<30}  {content_preview}")
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# seed synthetic data
# ──────────────────────────────────────────────────────────────────────────────


# 18 synthetic memories that exercise all the upgrade features.
# Each entry: (room, key, content, importance, tag_type, age_days, access_count)
SEED_MEMORIES: list[tuple[str, str, str, int, str, int, int]] = [
    # Same room, various ages + access counts — tests decay + access boost
    (f"{BENCH_ROOM}/backend",  "code_pattern", "Use async sessions for database queries with SET LOCAL RLS bypass",   5, "permanent",   0,  5),
    (f"{BENCH_ROOM}/backend",  "code_pattern", "Always call db.commit() inside a try/except block to catch errors",   3, "permanent",  30,  0),
    (f"{BENCH_ROOM}/backend",  "code_pattern", "Postgres generated columns cannot receive INSERT values",             4, "permanent",  90,  2),
    (f"{BENCH_ROOM}/backend",  "code_pattern", "SET LOCAL only lives inside a transaction — commits drop it",         5, "permanent", 180, 15),  # old but very valuable
    (f"{BENCH_ROOM}/backend",  "code_pattern", "Pydantic v2 uses model_validate instead of parse_obj",                3, "permanent",   7,  0),

    # Transient memories — should decay FAST
    (f"{BENCH_ROOM}/backend",  "current_task",  "Debugging the memory benchmark script",                               3, "transient",  0,  0),
    (f"{BENCH_ROOM}/backend",  "current_task",  "Fixing the alembic cached pass() problem",                            3, "transient",  7,  0),
    (f"{BENCH_ROOM}/backend",  "current_task",  "Investigating why webhook 500 errored",                               3, "transient", 30,  0),  # transient, 30d → should score very low

    # Sub-room (frontend) — tests structural scoring
    (f"{BENCH_ROOM}/frontend", "code_pattern", "Next.js app router pages are async by default",                        4, "permanent",  14,  3),
    (f"{BENCH_ROOM}/frontend", "code_pattern", "Tailwind @radix-ui dialog must use forwardRef for triggers",           3, "permanent",   2,  0),

    # Cousin room (infra) — should score LOWER than backend for a backend query
    (f"{BENCH_ROOM}/infra",    "code_pattern", "Docker bind mounts on macOS have high I/O latency",                    2, "permanent",  60,  1),
    (f"{BENCH_ROOM}/infra",    "code_pattern", "Redis AOF fails when disk is full — emit MISCONF error",               3, "permanent", 100,  4),

    # Completely unrelated room — shouldn't surface on focused queries
    (f"other:project/docs",    "code_pattern", "Use mermaid diagrams for architecture drawings",                       2, "permanent",  30,  0),
    (f"other:project/docs",    "code_pattern", "README should have quickstart in the first 20 lines",                  2, "permanent",  45,  0),

    # No-room (global)
    (None,                     "code_pattern", "Prefer composition over inheritance in Python class design",           4, "permanent",  90,  0),

    # Importance test — very high importance old permanent memory
    (f"{BENCH_ROOM}/backend",  "architecture",  "Orchestrator+Agent use Redis pub/sub for task events — see PR #41",   5, "permanent", 365, 30),

    # Ignored memory that should lose to accessed memory
    (f"{BENCH_ROOM}/backend",  "code_pattern", "SQLAlchemy 2.0 Mapped[str] replaces Column in type hints",             4, "permanent",  10,  0),

    # Counterpart: same age, similar importance, but frequently accessed
    (f"{BENCH_ROOM}/backend",  "code_pattern", "SQLAlchemy session expire_on_commit=False keeps objects usable",       4, "permanent",  10, 25),
]


async def _embed_via_service(session: aiohttp.ClientSession, texts: list[str]) -> list[list[float] | None]:
    """Use the orchestrator's embedding service by POSTing a memory and
    reading the row back. Simpler: call the internal embed endpoint if
    it exists, otherwise shell into the container.

    To keep this script self-contained, we shell into the container and
    call the embedding service directly. Slower but reliable.
    """
    # Build a small python one-liner that embeds each text and prints them
    import json
    payload = json.dumps(texts)
    script = (
        "import asyncio, json, sys\n"
        "from app.services.embedding_service import get_embedding_service\n"
        "async def main():\n"
        "    svc = get_embedding_service()\n"
        "    if not svc.enabled:\n"
        "        print(json.dumps([None]*len(sys.argv[1:])))\n"
        "        return\n"
        "    out = []\n"
        "    for t in sys.argv[1:]:\n"
        "        e = await svc.embed(t)\n"
        "        out.append(e)\n"
        "    print(json.dumps(out))\n"
        "asyncio.run(main())"
    )
    proc = subprocess.run(
        ["docker", "exec", "ai-employee-orchestrator", "python", "-c", script, *texts],
        capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"embed script failed: {proc.stderr}")
    return json.loads(proc.stdout.strip())


async def seed_dataset(agent_id: str) -> list[int]:
    """Insert all SEED_MEMORIES directly into the DB with controlled
    timestamps and embeddings. Returns the list of inserted IDs.
    """
    print("\n[seed] cleaning any previous bench data...")
    psql(f"DELETE FROM agent_memories WHERE agent_id = '{agent_id}' AND (room LIKE '{BENCH_ROOM}%' OR (room IS NULL AND key IN ('code_pattern','architecture','current_task') AND content LIKE 'Prefer composition%'));")

    print(f"[seed] embedding {len(SEED_MEMORIES)} memories via the embedding service...")
    texts = [f"{key}: {content}" for (_, key, content, _, _, _, _) in SEED_MEMORIES]
    async with aiohttp.ClientSession() as session:
        embeddings = await _embed_via_service(session, texts)

    ids: list[int] = []
    now = datetime.now(timezone.utc)

    for (room, key, content, importance, tag_type, age_days, access_count), vec in zip(SEED_MEMORIES, embeddings):
        created = now - timedelta(days=age_days)
        last_accessed = (now - timedelta(days=max(0, age_days - 5))) if access_count > 0 else None

        # Escape single quotes
        content_esc = content.replace("'", "''")
        room_sql = f"'{room}'" if room else "NULL"
        la_sql = f"'{last_accessed.isoformat()}'" if last_accessed else "NULL"

        # Use raw SQL because generated `value_hash` column blocks ORM insert
        insert_sql = f"""
        INSERT INTO agent_memories
          (agent_id, category, key, content, importance, access_count,
           created_at, updated_at, room, confidence, appeared_count,
           last_accessed_at, tag_type)
        VALUES
          ('{agent_id}', 'learning', '{key}', '{content_esc}',
           {importance}, {access_count},
           '{created.isoformat()}', '{created.isoformat()}',
           {room_sql}, 1.0, 0, {la_sql}, '{tag_type}')
        RETURNING id;
        """
        raw = psql(insert_sql)
        # psql -tAc returns the RETURNING value on the first line, may
        # be followed by a line like "INSERT 0 1" — we only need line 0.
        new_id = raw.splitlines()[0].strip()
        ids.append(int(new_id))

        # Attach embedding if available
        if vec is not None:
            vec_literal = "[" + ",".join(str(x) for x in vec) + "]"
            psql(
                f"UPDATE agent_memories SET embedding = '{vec_literal}'::vector "
                f"WHERE id = {new_id};"
            )

    print(f"[seed] inserted {len(ids)} memories")
    return ids


# ──────────────────────────────────────────────────────────────────────────────
# queries
# ──────────────────────────────────────────────────────────────────────────────


async def baseline_search(agent_id: str, query_text: str, limit: int = 5) -> list[dict]:
    """Simulate the OLD semantic-search: pure cosine similarity, no
    room filter, no recency/importance weighting.
    """
    # Generate embedding for query
    emb = await _embed_via_service(None, [query_text])  # type: ignore
    query_vec = emb[0]
    if query_vec is None:
        return []

    vec_literal = "[" + ",".join(str(x) for x in query_vec) + "]"
    sql = f"""
    SELECT id, key, content, room, importance, access_count, tag_type,
           created_at, last_accessed_at,
           1 - (embedding <=> '{vec_literal}'::vector) AS similarity
    FROM agent_memories
    WHERE agent_id = '{agent_id}'
      AND embedding IS NOT NULL
    ORDER BY embedding <=> '{vec_literal}'::vector
    LIMIT {limit};
    """
    rows = psql(sql).strip().split("\n")
    result = []
    for row in rows:
        if not row.strip():
            continue
        parts = row.split("|")
        if len(parts) < 10:
            continue
        result.append({
            "id": int(parts[0]),
            "key": parts[1],
            "content": parts[2],
            "room": parts[3] if parts[3] else None,
            "importance": int(parts[4]),
            "access_count": int(parts[5]),
            "tag_type": parts[6],
            "similarity": float(parts[9]),
            "score": float(parts[9]),  # baseline = same as similarity
        })
    return result


async def upgraded_search(agent_id: str, query_text: str, room: str | None, limit: int = 5) -> list[dict]:
    """Call the NEW /memory/semantic-search API with multi-strategy scoring."""
    agent_token = mint_agent_token(agent_id)
    url = f"{BASE_URL}/api/v1/memory/semantic-search?agent_id={agent_id}&q={aiohttp.helpers.quote(query_text)}&limit={limit}"
    if room:
        url += f"&room={aiohttp.helpers.quote(room)}"
    async with aiohttp.ClientSession() as s:
        async with s.get(
            url,
            headers={"Authorization": f"Bearer {agent_token}", "X-Agent-ID": agent_id},
        ) as r:
            if r.status != 200:
                return []
            data = await r.json()
            return data.get("memories", [])


# ──────────────────────────────────────────────────────────────────────────────
# comparisons / experiments
# ──────────────────────────────────────────────────────────────────────────────


async def experiment_1_ranking_diff(agent_id: str) -> None:
    """Show how the ranking changes for the same query."""
    print("\n" + "=" * 78)
    print("  EXPERIMENT 1 — ranking diff, same query, 6 results")
    print("=" * 78)

    query = "async database sessions in Python"

    print(f"\nQuery: \"{query}\"")
    print(f"(focusing on room: {BENCH_ROOM}/backend)\n")

    baseline = await baseline_search(agent_id, query, limit=6)
    upgraded = await upgraded_search(agent_id, query, room=f"{BENCH_ROOM}/backend", limit=6)

    print(format_row("BASELINE (pure cosine, no room filter):", baseline))
    print("")
    print(format_row("UPGRADED (multi-strategy, room filter, supersede-aware):", upgraded))

    # Diff analysis
    baseline_ids = [m["id"] for m in baseline]
    upgraded_ids = [m["id"] for m in upgraded]
    overlap = len(set(baseline_ids) & set(upgraded_ids))
    print(f"\n  → overlap: {overlap}/{max(len(baseline_ids), len(upgraded_ids))} memories")
    print(f"  → baseline_top is id={baseline_ids[0] if baseline_ids else None}, "
          f"upgraded_top is id={upgraded_ids[0] if upgraded_ids else None}")


async def experiment_2_hybrid_decay(agent_id: str) -> None:
    """Compare scores of transient vs permanent memories at different ages."""
    print("\n" + "=" * 78)
    print("  EXPERIMENT 2 — hybrid decay: transient vs permanent over time")
    print("=" * 78)

    # Pull the raw rows directly so we can show computed scores
    rows = psql(f"""
        SELECT id, content, tag_type,
               EXTRACT(DAY FROM (NOW() - created_at)) as age_days,
               access_count, importance
        FROM agent_memories
        WHERE agent_id = '{agent_id}'
          AND room = '{BENCH_ROOM}/backend'
          AND key IN ('code_pattern', 'current_task')
        ORDER BY age_days;
    """).split("\n")

    def recency(tag_type: str, age_days: float, access_count: int) -> float:
        access_boost = min(1.0, 0.1 * access_count)
        if tag_type == "transient":
            base = math.exp(-age_days / 30.0)
        else:
            base = 1.0 / (1.0 + math.log1p(age_days / 7.0))
        return min(1.0, base + 0.2 * access_boost)

    print(f"\n  {'id':<6}{'tag_type':<12}{'age(d)':<8}{'access':<8}{'recency':<10} content")
    print(f"  {'-'*6}{'-'*12}{'-'*8}{'-'*8}{'-'*10}{'-'*50}")
    for row in rows:
        if not row.strip():
            continue
        parts = row.split("|")
        if len(parts) < 6:
            continue
        id_, content, tag_type, age, acc, _imp = parts[0], parts[1], parts[2], float(parts[3]), int(parts[4]), int(parts[5])
        r = recency(tag_type, age, acc)
        print(f"  {id_:<6}{tag_type:<12}{int(age):<8}{acc:<8}{r:<10.4f} {content[:50]}")

    print("\n  Interpretation:")
    print("    - transient@30d should be around 0.37 (exp(-1)) → dropped out of retrieval")
    print("    - permanent@180d should still score ~0.25 (log decay)")
    print("    - accessed memories get a +0.2 boost regardless of age")


async def experiment_3_structural_vs_semantic(agent_id: str) -> None:
    """Show that room-filter excludes unrelated memories."""
    print("\n" + "=" * 78)
    print("  EXPERIMENT 3 — room filter excludes cousin/unrelated memories")
    print("=" * 78)

    query = "code pattern design"

    print(f"\nQuery: \"{query}\"\n")

    print("  WITHOUT room filter (what baseline does):")
    no_room = await upgraded_search(agent_id, query, room=None, limit=8)
    for i, m in enumerate(no_room, 1):
        print(f"    {i:2d}. [id={m['id']:>4}] room={(m.get('room') or '-')[-32:]:<32}  score={m.get('score', 0):.3f}")

    print("\n  WITH room='{}/backend' (upgraded path):".format(BENCH_ROOM))
    with_room = await upgraded_search(agent_id, query, room=f"{BENCH_ROOM}/backend", limit=8)
    for i, m in enumerate(with_room, 1):
        print(f"    {i:2d}. [id={m['id']:>4}] room={(m.get('room') or '-')[-32:]:<32}  score={m.get('score', 0):.3f}")

    backend_ids_no_room = sum(1 for m in no_room if (m.get("room") or "").endswith("/backend"))
    backend_ids_with_room = sum(1 for m in with_room if (m.get("room") or "").endswith("/backend"))

    print(f"\n  → w/o filter: {backend_ids_no_room}/{len(no_room)} hits are in /backend")
    print(f"  → w/  filter: {backend_ids_with_room}/{len(with_room)} hits are in /backend")


async def experiment_4_supersede_hides_old(agent_id: str) -> None:
    """Supersede an old memory and verify it disappears from search."""
    print("\n" + "=" * 78)
    print("  EXPERIMENT 4 — supersede → old memory excluded from retrieval")
    print("=" * 78)

    # Find the "Pydantic v2" memory (we seeded it 7d old)
    target_id_str = psql(f"""
        SELECT id FROM agent_memories
        WHERE agent_id = '{agent_id}'
          AND room = '{BENCH_ROOM}/backend'
          AND content LIKE 'Pydantic v2%'
        LIMIT 1;
    """).strip()
    if not target_id_str:
        print("  skipped — seed memory not found")
        return
    target_id = int(target_id_str)

    # Search for "Pydantic" — should find it
    before = await upgraded_search(agent_id, "Pydantic model validation", room=f"{BENCH_ROOM}/backend", limit=3)
    print(f"\n  BEFORE supersede — top hits for 'Pydantic model validation':")
    for m in before:
        mark = " ← target" if m["id"] == target_id else ""
        print(f"    [id={m['id']:>4}] score={m.get('score', 0):.3f}  {m['content'][:50]}{mark}")

    # Mark it as superseded by the "SQLAlchemy Mapped" memory (not really a
    # supersede semantically, but demonstrates the filter)
    newer_id_str = psql(f"""
        SELECT id FROM agent_memories
        WHERE agent_id = '{agent_id}'
          AND content LIKE 'SQLAlchemy 2.0 Mapped%'
        LIMIT 1;
    """).strip()
    if not newer_id_str:
        print("  skipped — newer memory not found")
        return
    newer_id = int(newer_id_str)

    psql(f"UPDATE agent_memories SET superseded_by = {newer_id}, superseded_at = NOW() WHERE id = {target_id};")
    print(f"\n  [manual supersede] marked id={target_id} as superseded_by={newer_id}")

    after = await upgraded_search(agent_id, "Pydantic model validation", room=f"{BENCH_ROOM}/backend", limit=3)
    print(f"\n  AFTER supersede — same query:")
    for m in after:
        mark = " ← target (should be GONE)" if m["id"] == target_id else ""
        print(f"    [id={m['id']:>4}] score={m.get('score', 0):.3f}  {m['content'][:50]}{mark}")

    still_there = any(m["id"] == target_id for m in after)
    print(f"\n  → target still in results: {still_there}  (expected: False)")

    # Restore so we don't poison the DB for the next run
    psql(f"UPDATE agent_memories SET superseded_by = NULL, superseded_at = NULL WHERE id = {target_id};")


async def experiment_5_access_boost(agent_id: str) -> None:
    """Two memories with same age/importance, different access_count."""
    print("\n" + "=" * 78)
    print("  EXPERIMENT 5 — access_count boost breaks ties")
    print("=" * 78)

    # Our seed has:
    #   id=X "SQLAlchemy Mapped[str]" — access_count=0
    #   id=Y "expire_on_commit=False" — access_count=25
    # Both same age (10d), same importance (4)

    rows = psql(f"""
        SELECT id, content, access_count
        FROM agent_memories
        WHERE agent_id = '{agent_id}'
          AND room = '{BENCH_ROOM}/backend'
          AND (content LIKE 'SQLAlchemy 2.0 Mapped%'
               OR content LIKE 'SQLAlchemy session expire%')
        ORDER BY access_count;
    """).strip().split("\n")

    print("\n  Pair — same 10d age, same importance 4, differ only in access_count:")
    for row in rows:
        if not row.strip():
            continue
        parts = row.split("|")
        print(f"    [id={parts[0]:>4}] access_count={int(parts[2]):<3}  {parts[1][:55]}")

    query = "SQLAlchemy best practices"
    result = await upgraded_search(agent_id, query, room=f"{BENCH_ROOM}/backend", limit=5)

    print(f"\n  Query: \"{query}\"  (upgraded path):")
    ranks = {}
    for i, m in enumerate(result, 1):
        print(f"    {i:2d}. [id={m['id']:>4}] score={m.get('score', 0):.3f}  access_count={m.get('access_count', 0):<3}  {m['content'][:50]}")
        ranks[m['id']] = i

    # Find the two targets
    ignored = psql(f"SELECT id FROM agent_memories WHERE content LIKE 'SQLAlchemy 2.0 Mapped%' AND agent_id = '{agent_id}';").strip()
    accessed = psql(f"SELECT id FROM agent_memories WHERE content LIKE 'SQLAlchemy session expire%' AND agent_id = '{agent_id}';").strip()
    if ignored and accessed:
        ig = int(ignored)
        ac = int(accessed)
        ig_rank = ranks.get(ig, 999)
        ac_rank = ranks.get(ac, 999)
        print(f"\n  → ignored memory rank: {ig_rank}")
        print(f"  → accessed memory rank: {ac_rank}")
        if ac_rank < ig_rank:
            print("  ✅ access_boost works — frequently-used memory ranks higher")
        elif ac_rank == ig_rank:
            print("  ⚠️  equal — access_boost too small to break this tie")
        else:
            print("  ❌ access_boost not effective here")


# ──────────────────────────────────────────────────────────────────────────────
# main
# ──────────────────────────────────────────────────────────────────────────────


async def main() -> None:
    global BENCH_AGENT_ID
    BENCH_AGENT_ID = psql("SELECT id FROM agents LIMIT 1;").strip()
    if not BENCH_AGENT_ID:
        print("no agents — seed some first")
        sys.exit(1)

    print("\n" + "=" * 78)
    print("  MEMORY SYSTEM UPGRADE — QUALITY BENCHMARK (issue #24)")
    print("  Agent:", BENCH_AGENT_ID)
    print("  Bench room prefix:", BENCH_ROOM)
    print("=" * 78)

    await seed_dataset(BENCH_AGENT_ID)

    await experiment_1_ranking_diff(BENCH_AGENT_ID)
    await experiment_2_hybrid_decay(BENCH_AGENT_ID)
    await experiment_3_structural_vs_semantic(BENCH_AGENT_ID)
    await experiment_4_supersede_hides_old(BENCH_AGENT_ID)
    await experiment_5_access_boost(BENCH_AGENT_ID)

    print("\n" + "=" * 78)
    print("  DONE — cleaning bench dataset")
    print("=" * 78)
    psql(f"DELETE FROM agent_memories WHERE agent_id = '{BENCH_AGENT_ID}' AND (room LIKE '{BENCH_ROOM}%' OR content LIKE 'Prefer composition%');")


if __name__ == "__main__":
    asyncio.run(main())

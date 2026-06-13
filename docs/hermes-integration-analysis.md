# Hermes-Patterns: Integration Analysis (2026-05-30)

Companion doc to `feat/hermes-inspired-memory-curator` branch.

## What was found by post-implementation code review

### 🔴 Bug 1 — Memory eviction is invisible to all consumers

`memory_caps.enforce` marks evicted rows with `superseded_by=None, superseded_at=now`.
But every consumer of the memory table uses the same predicate to mean **active**:

```python
WHERE superseded_by IS NULL
```

Consumers that would still return evicted memories:
- `api/memory.py` — save dedup, semantic_search, search, preload, list_agent_memories
- `api/brain.py` — cross-agent semantic search
- `services/memory_compressor.py` — room summary
- `services/profile_extractor.py` — user-profile derivation

Net effect: eviction would log "evicted N items" but those items continue to
be loaded into agents' system prompts. **Caps would silently not work.**

**Fix:** Introduce a new column `evicted_at` (DateTime, nullable, indexed). The
active filter becomes `superseded_by IS NULL AND evicted_at IS NULL`.

### 🟠 Bug 2 — Curator can destroy an in-flight A/B test

`improvement_engine` runs an A/B validation cycle on skills: when a skill
performs poorly, the engine proposes new content, sets
`improvement_status='probation'`, and waits 5 usages or 14 days before
deciding to validate-or-rollback.

`SkillCurator.run` blindly moves any low-rating ACTIVE skill into STALE. If
the engine just set probation, the curator could prematurely demote it
while the new content is still being measured.

**Fix:** Curator excludes skills with `improvement_status IN ('pending_review', 'probation')`.

### 🟡 Concern 3 — No background scheduler wired

The curator and bucket-usage are pure on-demand endpoints today. The Hermes
parallel is a background curator that runs periodically without user input.

Options for AI-Employee:
1. **Manual button + cron via routines** — simplest. Daniel triggers via UI or a daily routine calls the endpoint.
2. **In-process `asyncio.create_task`** — fits the existing pattern in `main.py` (oauth refresh, task listener), but adds DB load on every orchestrator pod.
3. **External worker** — most scalable, requires new service.

**Recommendation:** Option 1 for now. A user routine `/api/v1/schedules/` calling
`POST /skills/marketplace/curator/run` once a day. Revisit when there are 100+ skills.

### 🟡 Concern 4 — Frontend has no visibility

- `GET /memory/bucket-usage` returns useful data but no widget consumes it.
- New `SkillStatus.STALE` would appear in skill lists without UI styling.
- Curator dry-run is a useful admin tool but no button calls it.

Out of scope for this PR; tracked separately.

## Decisions

| Topic | Choice | Reason |
|---|---|---|
| Eviction marker | new `evicted_at` column | Avoids retrofitting 8 queries with sentinel logic |
| Probation interaction | Curator skips probation | A/B engine is authoritative for those skills |
| Background run | Manual / routine | Less coupling, easier to disable |
| Unique-index touching | No | Eviction doesn't change dedup semantics |
| Migration head | rebase onto `z0t1u2v3w4x5` | matches current `main` |

## Open items (not in this PR)

- Memory bucket-usage widget in Dashboard.
- Skill list shows STALE state with subdued styling.
- Honcho-style peer card (issue #190).
- Conflict resolution with `feat/memory-system-upgrade` branch — merge order
  matters because both add columns to `agent_memories`.

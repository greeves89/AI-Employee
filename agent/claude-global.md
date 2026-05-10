# AI Employee Agent — Global Instructions

## Memory System (CRITICAL)

**Use ONLY MCP memory tools. Never use Claude Code's built-in /memory command.**

The MCP memory system (`memory_save`, `memory_search`) is the ONLY persistent memory:
- It is shared across all tasks and restarts
- It is searchable by other agents
- It is the source of truth for all learnings, decisions, and credentials

Claude Code's file-based memory (written to CLAUDE.md or via /memory) is:
- Local to this container only
- Lost on container restart
- NOT synchronized with the MCP memory
- **DISABLED for your use**

If you want to save a learning, credential, or decision: call `memory_save`.
If you want to recall something: call `memory_search`.
Never write to this file or use /memory.

## Second Brain (CRITICAL — use before every task)

Every agent has access to the user's **Second Brain** — a unified semantic knowledge graph
shared across ALL agents of this user. It grows as agents contribute to it.

**Before starting any task:**
1. Call `brain_search` with a query relevant to your task
2. This surfaces knowledge, research, and decisions from ALL agents of this user
3. It is always more complete than your own memory alone

**After completing important work:**
- Call `brain_contribute` to add learnings, decisions, or research to the shared brain
- Other agents will find this context automatically via semantic search

Tools: `brain_search(q, include_memories=false)` | `brain_contribute(title, content, tags=[])`

## Agent State File (CRITICAL — read at start, update at end)

Every run — task, proactive, or chat — must maintain `/workspace/.agent_state.md`.

**At the START of every run**: read this file if it exists. It tells you what you last did,
what is currently active, and what the user last asked for. Do this BEFORE starting any work.

**At the END of every run**: update (or create) this file with a concise summary:

```markdown
# Agent State
Last updated: <ISO timestamp>
Last run type: <proactive|task|chat>
Last run summary: <1-2 sentences what was done>

## Active Work
- <current position / ongoing task / open issue — whatever is relevant to your role>

## User Directives
- <any explicit instructions from the user that should persist across runs>

## Next Steps
- <what you plan to do on the next proactive run>
```

Keep it short (max ~30 lines). Overwrite the whole file each time — do not append.
This file is the single fastest way for you to remember what you were doing.

## Skill System

Before every task:
1. Check your installed skills (shown in task context under "YOUR INSTALLED SKILLS")
2. If no installed skill matches, call `skill_search` from the marketplace
3. After using a skill, always call `skill_rate` — this improves future skill quality

## Autonomy

Always respect your autonomy whitelist. When in doubt, call `request_approval`.

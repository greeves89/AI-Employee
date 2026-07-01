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

## Daily Log System (MANDATORY — every agent, every day)

Every agent **must** maintain a daily log at `/workspace/daily/YYYY-MM-DD.md`.

### During any task or chat turn
Append a brief entry to today's log:
```bash
mkdir -p /workspace/daily
DATE=$(date +%Y-%m-%d)
cat >> /workspace/daily/${DATE}.md << 'EOF'
- HH:MM — <1-sentence description of what was done / decided>
EOF
```
Keep entries short (one line per action). Do NOT rewrite the whole file — always append.

### At session end (feierabend)
Run the built-in `feierabend` skill (install via `skill_install` if not present):
- Reads today's log
- Writes a clean **## Summary** + **## Open Items** section at the bottom
- Updates `.agent_state.md` with the open items as Next Steps

### At the start of a new day
Before responding to any message, check the last 5 days:
```bash
ls /workspace/daily/*.md 2>/dev/null | sort | tail -5 | xargs grep -h "^## Open Items" -A 20 2>/dev/null || echo "No open items found"
```
Summarise all open items and present them to the user before starting new work.
**Never skip this** — open items from previous days are unfinished commitments.

### Log format
```markdown
# Daily Log — 2026-06-14

- 09:05 — Analysed Garrit Wilson comparison, drafted feature plan
- 09:47 — Updated claude-global.md with daily log system
- 10:12 — Rebuilt agent container image

## Summary  ← written by feierabend skill
Everything committed. Image rebuilt and deployed.

## Open Items  ← written by feierabend skill
- [ ] Test rich markdown rendering on live device
- [ ] Verify sendRichMessage on Telegram after server pull
```

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

**Where skills live:**
- Marketplace skills you install go to `/workspace/.claude/skills/<skill-name>/SKILL.md` — your container's workspace, persists across restarts.
- To create a NEW skill for the global marketplace, use `skill_propose` (NOT manual file writes — propose goes through review and is shared with all agents).
- Never write to `~/.claude/skills/` (Claude Code's user-global location) — it does not persist or sync.

## Persistent Scheduling — DO NOT use CronCreate

Claude Code's `CronCreate` tool is **session-only** — your cron dies when the session ends.
Never use it for anything you want to survive restarts.

For persistent automations, use the platform's Schedule API:
- `create_schedule(name, prompt, interval_seconds | cron_expression, ...)` — runs reliably across restarts via the orchestrator's scheduler service.
- `list_schedules`, `update_schedule`, `delete_schedule` for management.

Same rule: **CronCreate = session, create_schedule = persistent**. Always pick `create_schedule`.

## Autonomy

Always respect your autonomy whitelist. When in doubt, call `request_approval`.

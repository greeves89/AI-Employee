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

## Skill System

Before every task:
1. Check your installed skills (shown in task context under "YOUR INSTALLED SKILLS")
2. If no installed skill matches, call `skill_search` from the marketplace
3. After using a skill, always call `skill_rate` — this improves future skill quality

## Autonomy

Always respect your autonomy whitelist. When in doubt, call `request_approval`.

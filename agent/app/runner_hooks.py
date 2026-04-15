"""Shared hooks for both AgentRunner (Claude CLI) and LLMRunner (custom LLM).

Provides startup context (knowledge/memory/approval rules) and end-of-task
reflection prompts so both runners behave consistently.
"""

import json as _json
import logging
import os
import urllib.request

from app.config import settings

logger = logging.getLogger(__name__)


TASK_STARTUP_PREFIX = """
FIRST STEPS (do these BEFORE starting the actual task):
1. Read /workspace/knowledge.md to recall your role, skills, and learned patterns
2. Use knowledge_search (query relevant to this task) to check the shared knowledge base
3. Use memory_search (query: "") to check for recent memories and user preferences
4. Use list_todos to check for pending work items
If you encounter ANY problem during the task, ALWAYS search knowledge_search and memory_search
for solutions BEFORE reporting errors or asking the user.

---
"""


SELF_IMPROVEMENT_SUFFIX = """

---
MANDATORY REFLECTION (do ALL of these BEFORE finishing — no exceptions):

1. **VALIDATE your work**: If you wrote code, run `npm run build` / `pytest` / `go build` etc.
   Fix all errors before considering the task done. NEVER claim success on broken code.

2. **Push your work**: Commit with conventional-commit message, then `git push`.
   Never leave finished work only local.

3. **REFLECT — what went wrong?**: Look back at this task critically. Answer these for yourself:
   - What errors did I hit? (compile errors, runtime errors, wrong assumptions, denied commands)
   - What took longer than it should have?
   - What did I do that I should NOT do next time?
   - What did I do right that I should keep doing?

4. **SAVE the learnings (MANDATORY)**: For EACH thing you learned, call `memory_save` with:
   - category: "learning"
   - importance: 4 or 5 (for critical lessons)
   - key: snake_case name (e.g. "npm_build_before_commit", "avoid_rm_rf_home")
   - content: the full lesson with WHY it matters
   If this task had ZERO learnings, save one memory saying "task_clean_run: completed without issues"
   so we know you reflected.

5. **Update knowledge.md**: Append to these sections in `/workspace/knowledge.md`:
   - "## Learned Patterns" — new patterns that worked
   - "## Errors & Fixes" — errors + their fixes (so future-you doesn't repeat them)
   Format: `- <situation>: <what to do> (<why>)`
   Keep concise. This file is what you read at the START of every task.

6. **Create a skill if there's a repeatable pattern**: If this task revealed a workflow
   you'll need again, create `/workspace/.claude/skills/<skill-name>/SKILL.md` with
   frontmatter (name, description) + instructions. Makes you permanently better.

This reflection is NOT optional. You MUST perform it. Short tasks get short reflections,
long tasks get detailed ones — but ALL tasks end with memory_save calls.
"""


def get_memory_preload() -> str:
    """Fetch critical memories for prompt injection.

    Agents often forget API keys and user preferences after session reset. This ensures
    every task starts with the agent's most important long-term knowledge already loaded.
    """
    try:
        url = f"{settings.orchestrator_url}/api/v1/memory/preload/{settings.agent_id}"
        with urllib.request.urlopen(url, timeout=5) as response:
            data = _json.loads(response.read())

        critical = data.get("critical", [])
        credentials = data.get("credentials", [])
        learnings = data.get("recent_learnings", [])

        if not (critical or credentials or learnings):
            return ""

        lines = [
            "",
            "=== YOUR LONG-TERM MEMORY (pre-loaded — you already know these!) ===",
        ]
        if credentials:
            lines.append("\n## Credentials & Keys (use these when needed):")
            for m in credentials:
                lines.append(f"  - {m['key']}: {m['content']}")
        if critical:
            lines.append("\n## Critical (user corrections, key decisions, preferences):")
            for m in critical:
                if m["category"] in ("credentials", "api_key", "secret", "auth"):
                    continue  # already listed above
                lines.append(f"  - [{m['category']}] {m['key']}: {m['content']}")
        if learnings:
            lines.append("\n## Recent Learnings:")
            for m in learnings[:5]:
                lines.append(f"  - {m['key']}: {m['content']}")
        lines.extend([
            "",
            "You already KNOW the above. Do not ask the user for things listed here.",
            "If you need something specific not listed, use memory_search to find more.",
            "=== END MEMORY PRELOAD ===",
            "",
        ])
        return "\n".join(lines)
    except Exception:
        return ""


def get_approval_rules_prefix() -> str:
    """Fetch active approval rules and embed as a frozen snapshot in the prompt.

    Hook Config Snapshot (Claude Code ch12):
    Rules are fetched ONCE at task/message start and embedded in the prompt. Any
    changes to rules during execution are ignored for this session — preventing
    runtime injection attacks that modify hook config mid-task to bypass safety
    checks (e.g. a prompt that tells the agent to disable hooks then act freely).
    """
    try:
        url = f"{settings.orchestrator_url}/api/v1/approval-rules/for-agent/{settings.agent_id}"
        req = urllib.request.Request(url, headers={"X-Agent-Token": settings.agent_token})
        with urllib.request.urlopen(req, timeout=5) as response:
            data = _json.loads(response.read())
        rules = data.get("rules", [])
        if not rules:
            return ""
        lines = [
            "",
            "=== APPROVAL RULES (MANDATORY) ===",
            "The user has defined these rules. You MUST call `request_approval` BEFORE acting",
            "whenever any of these rules apply to what you are about to do:",
            "",
        ]
        for r in rules:
            threshold = f" (threshold: {r['threshold']})" if r.get("threshold") is not None else ""
            lines.append(f"  [{r['category']}] {r['name']}{threshold}: {r['description']}")
        lines.extend([
            "",
            "If unsure whether a rule applies, ASK via request_approval. Better safe than sorry.",
            "=== END APPROVAL RULES ===",
            "",
        ])
        return "\n".join(lines)
    except Exception:
        return ""


def get_improvement_context() -> str:
    """Read improvement data from knowledge.md Performance Metrics section."""
    try:
        knowledge_path = os.path.join(settings.workspace_dir, "knowledge.md")
        if not os.path.exists(knowledge_path):
            return ""
        with open(knowledge_path) as f:
            content = f.read()
        if "## Performance Metrics" in content:
            idx = content.index("## Performance Metrics")
            section = content[idx:]
            next_heading = section.find("\n## ", 3)
            if next_heading > 0:
                section = section[:next_heading]
            return (
                "\n--- YOUR PERFORMANCE FEEDBACK ---\n"
                + section.strip()
                + "\nUse this feedback to improve your work quality. "
                "Address the top issues mentioned above.\n---\n"
            )
    except Exception:
        pass
    return ""

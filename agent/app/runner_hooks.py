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
3. Use memory_search with a focused query AND pass `room` to narrow to the current project/area
   (e.g. room="project:<repo-name>/<area>"). Rooms dramatically improve retrieval precision.
4. Use list_todos to check for pending work items

If you encounter ANY problem during the task, ALWAYS search knowledge_search and memory_search
for solutions BEFORE reporting errors or asking the user.

---
"""

# Lightweight prefix for chat/telegram — skip knowledge.md, todos, memory preload
CHAT_STARTUP_PREFIX = """
You have access to tools: web_search, web_fetch, bash, read_file, write_file, memory_search,
knowledge_search, and more. USE THEM when the user asks for current information or tasks.
Do NOT just describe what you would do — actually call the tools and deliver results.

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

4. **SAVE the learnings (MANDATORY)**: For EACH thing you learned, call `memory_save` with these fields:
   - category: "learning"
   - importance: USE THIS SCALE CAREFULLY:
     * **5** = MUST NEVER FORGET: credentials, user preferences, working pipelines/workflows,
       tools you installed, capabilities you gained, API keys, critical decisions.
       Rule: "Would I be useless without this?" → 5
     * **4** = IMPORTANT: code patterns, error fixes, project architecture decisions,
       things that took > 10 min to figure out.
       Rule: "Would I waste time rediscovering this?" → 4
     * **3** = NICE TO KNOW: minor observations, one-time fixes, routine task notes.
       Rule: "Could I easily re-derive this?" → 3
     When in doubt, use 4. Losing knowledge is worse than storing too much.
   - key: snake_case name from the canonical set — prefer:
     * "code_pattern" for reusable coding patterns (multi-value, many can coexist)
     * "lesson_learned" for things to remember (multi-value)
     * "anti_pattern" for things to NEVER do again (multi-value)
     * "decision_rationale" for why an architectural choice was made (multi-value)
     * "capability_gained" for new tools/workflows you can now do (multi-value, importance=5!)
     * "working_pipeline" for end-to-end workflows that work (multi-value, importance=5!)
     * "current_task" for in-progress work (single-value — auto-supersedes the old one)
   - content: the full lesson with WHY it matters
   - **room**: "project:<repo-name>/<area>" — USE A ROOM. Example: "project:ai-employee/backend/auth".
     This is critical for retrieval precision. Without a room, your future-self can't
     filter to the right area and gets polluted results.
   - **tag_type**:
     * "transient"  — for current_task, today's debugging notes, task state. Decays in ~30d.
     * "permanent"  — for code_pattern, lesson_learned, decision_rationale. Lives forever.
     When in doubt, use permanent (default).
   - **tags**: pick from: task, code, decision, learning, error, correction, pattern,
     architecture, performance, security, user_preference, meta.

   If the server returns a 409 contradiction warning, it means a very similar memory already
   exists in the same room. Review it via memory_search, then re-call memory_save with
   override=true if the new content should replace the old one. The old memory is kept as
   an audit trail via superseded_by.

   If this task had ZERO learnings, save one memory with key="current_task", tag_type="transient",
   content="task_clean_run: completed without issues" so we know you reflected.

5. **Update knowledge.md**: Append to these sections in `/workspace/knowledge.md`:
   - "## Learned Patterns" — new patterns that worked
   - "## Errors & Fixes" — errors + their fixes (so future-you doesn't repeat them)
   Format: `- <situation>: <what to do> (<why>)`
   Keep concise. This file is what you read at the START of every task.

6. **Rate this task (MANDATORY)**: Call `rate_task` with:
   - rating: 1-5 (be honest — 3 means OK, 5 means truly excellent)
   - reflection: ONE sentence about what went well or what to do differently next time

7. **Create a skill if there's a repeatable pattern**: If this task revealed a workflow
   you'll need again, call `create_skill` with a title, description, and the solution.
   This saves it to the shared marketplace for all agents.

This reflection is NOT optional. You MUST perform it. Short tasks get short reflections,
long tasks get detailed ones — but ALL tasks end with memory_save + rate_task calls.
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


def get_skill_preload() -> str:
    """Fetch assigned skills from the marketplace for prompt injection.

    Skills are loaded from the central DB (not filesystem) and injected
    into the agent's prompt so it knows its available routines/templates.
    """
    try:
        url = f"{settings.orchestrator_url}/api/v1/skills/agent/available"
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {settings.agent_token}",
            "X-Agent-ID": settings.agent_id,
        })
        with urllib.request.urlopen(req, timeout=5) as response:
            data = _json.loads(response.read())

        skills = data.get("skills", [])
        if not skills:
            return ""

        lines = [
            "",
            "=== YOUR SKILLS (use these when relevant) ===",
        ]
        for s in skills:
            lines.append(f"\n### Skill: {s['name']}")
            if s.get("description"):
                lines.append(f"_{s['description']}_")
            lines.append(s.get("content", "")[:2000])
        lines.extend([
            "",
            "Apply the above skills when the task matches. If you discover a new "
            "reusable pattern, propose it with skill_propose.",
            "=== END SKILLS ===",
            "",
        ])
        return "\n".join(lines)
    except Exception:
        return ""


def get_skills_context() -> str:
    """Scan installed skills from the workspace and inject as context.

    Skills on the filesystem survive restarts (persistent volume).
    This ensures the agent knows its capabilities immediately without
    needing to rediscover them via memory_search.
    """
    import os
    # Scan all known skill directories (different AI tools use different paths)
    skills_dirs = [
        os.path.join(settings.workspace_dir, ".claude", "skills"),
        os.path.join(settings.workspace_dir, ".agents", "skills"),
        os.path.join(settings.workspace_dir, "skills"),
    ]
    # Also auto-discover any other */skills/ dirs in workspace root
    for entry in os.listdir(settings.workspace_dir):
        candidate = os.path.join(settings.workspace_dir, entry, "skills")
        if entry.startswith(".") and os.path.isdir(candidate) and candidate not in skills_dirs:
            skills_dirs.append(candidate)
    found_skills: dict[str, str] = {}  # name → content (deduped)
    for skills_dir in skills_dirs:
        if not os.path.isdir(skills_dir):
            continue
        for entry in os.listdir(skills_dir):
            skill_path = os.path.join(skills_dir, entry, "SKILL.md")
            if os.path.isfile(skill_path):
                try:
                    with open(skill_path) as f:
                        content = f.read()
                    name = entry
                    if name not in found_skills:  # first occurrence wins
                        found_skills[name] = content[:500]
                except Exception:
                    pass
    # Also check for standalone SKILL.md in subdirs (e.g. /workspace/pdf_generator/SKILL.md)
    for entry in os.listdir(settings.workspace_dir):
        skill_path = os.path.join(settings.workspace_dir, entry, "SKILL.md")
        if os.path.isfile(skill_path) and entry not in found_skills:
            try:
                with open(skill_path) as f:
                    found_skills[entry] = f.read()[:500]
            except Exception:
                pass

    if not found_skills:
        return ""

    lines = [
        "",
        "=== YOUR INSTALLED SKILLS (from /workspace — these survive restarts!) ===",
    ]
    for name, content in list(found_skills.items())[:15]:  # Cap at 15
        lines.append(f"\n### {name}")
        lines.append(content)
    lines.extend([
        "",
        "You HAVE these capabilities. Use them when relevant. Do NOT say you can't do something",
        "if you have a skill for it.",
        "=== END SKILLS ===",
        "",
    ])
    return "\n".join(lines)


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

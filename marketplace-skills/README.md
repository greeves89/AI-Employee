# Marketplace Skills (SKILL.md sources)

Source directory for SKILL.md-based agent skills served through the skill
marketplace. Register this repo as a skill source (admin UI, issue #371) with
subdir `marketplace-skills` — the crawler picks up every `SKILL.md` here and
installs selected skills into agents at `/workspace/.claude/skills/<name>/`.

Not to be confused with [agent/skills/](../agent/skills/), which contains
Python tool plugins (`skill.json` + `tools.py`) loaded directly by the agent
runtime. The top-level `skills/` directory is the gitignored runtime install
target, never a source location.

| Skill | Purpose |
|---|---|
| [writing-agent-ready-issues](writing-agent-ready-issues/SKILL.md) | Product-owner triage: turn raw backlog items and epics into issues an autonomous agent can implement cleanly |

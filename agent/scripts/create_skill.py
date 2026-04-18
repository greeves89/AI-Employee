#!/usr/bin/env python3
"""
create_skill.py — Skill Marketplace scaffolder
================================================
Usage:
    python agent/scripts/create_skill.py <skill-id> "Skill Display Name"

Example:
    python agent/scripts/create_skill.py crm-lookup "CRM Lookup"

Creates:
    agent/skills/<skill-id>/
        skill.json
        tools.py
        README.md
        templates/   (empty)
        scripts/     (empty)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SKILLS_DIR = Path(__file__).parent.parent / "skills"


SKILL_JSON_TEMPLATE = """\
{{
  "id": "{skill_id}",
  "name": "{skill_name}",
  "version": "1.0.0",
  "description": "TODO: describe what this skill does",
  "author": "ai-employee",
  "tags": [],
  "dependencies": [],
  "tools": ["{tool_name}"],
  "enabled": true
}}
"""

TOOLS_PY_TEMPLATE = '''\
"""{skill_name} skill — tools.py"""

from __future__ import annotations

from app.skills_loader import skill_tool


@skill_tool(
    name="{tool_name}",
    description="TODO: describe what {tool_name} does",
    parameters={{
        "type": "object",
        "properties": {{
            "input": {{
                "type": "string",
                "description": "TODO: describe the input parameter",
            }},
        }},
        "required": ["input"],
    }},
)
async def {tool_name}(params: dict) -> str:
    """TODO: implement {tool_name}."""
    return f"Result: {{params['input']}}"
'''

README_TEMPLATE = """\
# {skill_name}

**Skill ID:** `{skill_id}`
**Version:** 1.0.0

## Overview

TODO: describe what this skill does.

## Tools

### `{tool_name}`

TODO: describe the tool parameters and output.

## Dependencies

TODO: list any pip packages required.

## Examples

```python
# Via agent: just ask it naturally
# "Please ... using {tool_name}"
```
"""


def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    skill_id = sys.argv[1].lower().replace(" ", "-")
    skill_name = sys.argv[2]
    # Default tool name: first word of skill_id, underscored
    tool_name = skill_id.replace("-", "_")

    skill_dir = SKILLS_DIR / skill_id
    if skill_dir.exists():
        print(f"Error: skill directory already exists: {skill_dir}")
        sys.exit(1)

    # Create directories
    skill_dir.mkdir(parents=True)
    (skill_dir / "templates").mkdir()
    (skill_dir / "scripts").mkdir()

    # Write files
    (skill_dir / "skill.json").write_text(
        SKILL_JSON_TEMPLATE.format(
            skill_id=skill_id, skill_name=skill_name, tool_name=tool_name
        )
    )
    (skill_dir / "tools.py").write_text(
        TOOLS_PY_TEMPLATE.format(
            skill_name=skill_name, skill_id=skill_id, tool_name=tool_name
        )
    )
    (skill_dir / "README.md").write_text(
        README_TEMPLATE.format(
            skill_name=skill_name, skill_id=skill_id, tool_name=tool_name
        )
    )

    print(f"✅ Skill scaffolded: {skill_dir}")
    print(f"   → Edit skill.json  to add description, tags, dependencies")
    print(f"   → Edit tools.py    to implement the tool logic")
    print(f"   → Edit README.md   to document usage")
    print(f"   → Add templates/   and scripts/ as needed")
    print()
    print(f"The skill is auto-loaded on next agent start. No core code changes needed.")


if __name__ == "__main__":
    main()

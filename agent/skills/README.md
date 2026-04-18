# AI Employee — Skill Marketplace

Skills are self-contained plugins that extend every agent with new tools.
**Adding a skill = adding a directory. No core code changes needed.**

## Directory Structure

```
skills/
  <skill-id>/
    skill.json        # Manifest (required)
    tools.py          # Tool implementations (required)
    templates/        # Optional: Jinja2 / Markdown templates
    scripts/          # Optional: Helper scripts
    README.md         # Optional: Usage documentation
```

## Creating a New Skill

```bash
python agent/scripts/create_skill.py my-skill "My Skill Name"
```

This scaffolds the full directory structure with example code.

## Skill Manifest (skill.json)

```json
{
  "id": "my-skill",
  "name": "My Skill Name",
  "version": "1.0.0",
  "description": "What this skill does",
  "author": "your-name",
  "tags": ["data", "analysis"],
  "dependencies": ["pandas"],
  "tools": ["tool_one", "tool_two"],
  "enabled": true
}
```

## Tools in tools.py

Each tool is a Python async function decorated with `@skill_tool`:

```python
from app.skills_loader import skill_tool

@skill_tool(
    name="my_tool",
    description="Does something useful",
    parameters={
        "type": "object",
        "properties": {
            "input": {"type": "string", "description": "Input text"}
        },
        "required": ["input"]
    }
)
async def my_tool(params: dict) -> str:
    return f"Result: {params['input']}"
```

## Available Skills

| ID | Name | Tools | Tags |
|----|------|-------|------|
| spreadsheet | Spreadsheet Analysis | analyze_spreadsheet | data, excel, csv |
| document | Document Analysis | analyze_document | pdf, docx, contracts |
| finance | Finance Reports | generate_finance_report | finance, budget, reporting |

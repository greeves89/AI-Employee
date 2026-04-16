"""Trigger Evaluator - matches webhook payloads against EventTrigger conditions.

Evaluates conditions and interpolates prompt templates with payload data.
"""

import logging
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.event_trigger import EventTrigger

logger = logging.getLogger(__name__)

# Matches {{payload.field.subfield}} in prompt templates
TEMPLATE_RE = re.compile(r"\{\{payload\.([a-zA-Z0-9_.\[\]]+)\}\}")


def _resolve_path(data: Any, path: str) -> Any:
    """Resolve a dot-separated path against a nested dict/list.

    Examples:
        _resolve_path({"a": {"b": 1}}, "a.b") -> 1
        _resolve_path({"items": [{"x": 1}]}, "items[0].x") -> 1
        _resolve_path({"a": 1}, "a.b.c") -> None  (missing key)
    """
    current = data
    # Split on dots but handle array indices like items[0]
    parts = re.split(r"\.(?![^\[]*\])", path)
    for part in parts:
        if current is None:
            return None
        # Handle array index: field[0]
        idx_match = re.match(r"^(.+)\[(\d+)\]$", part)
        if idx_match:
            key, idx = idx_match.group(1), int(idx_match.group(2))
            if isinstance(current, dict):
                current = current.get(key)
            else:
                return None
            if isinstance(current, list) and 0 <= idx < len(current):
                current = current[idx]
            else:
                return None
        elif isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def match_conditions(payload: dict, conditions: dict) -> bool:
    """Check if all conditions match the payload.

    Each condition key is a dotted path into the payload. The value is compared
    for equality (or special operators):
      - {"action": "opened"} → payload["action"] == "opened"
      - {"pull_request.draft": false} → payload["pull_request"]["draft"] == False
      - {"action": {"$in": ["opened", "reopened"]}} → payload["action"] in ["opened", "reopened"]
      - {"action": {"$ne": "closed"}} → payload["action"] != "closed"
      - {"labels": {"$contains": "bug"}} → "bug" in payload["labels"]
    """
    for path, expected in conditions.items():
        actual = _resolve_path(payload, path)

        if isinstance(expected, dict):
            # Operator-based matching
            if "$in" in expected:
                if actual not in expected["$in"]:
                    return False
            elif "$ne" in expected:
                if actual == expected["$ne"]:
                    return False
            elif "$contains" in expected:
                if not isinstance(actual, (list, str)):
                    return False
                if expected["$contains"] not in actual:
                    return False
            elif "$exists" in expected:
                exists = actual is not None
                if exists != expected["$exists"]:
                    return False
            else:
                # Plain dict equality
                if actual != expected:
                    return False
        else:
            if actual != expected:
                return False

    return True


def interpolate_template(template: str, payload: dict) -> str:
    """Replace {{payload.field}} placeholders with actual values from the payload.

    Unknown fields are replaced with "(unknown)".
    """
    def replacer(match: re.Match) -> str:
        path = match.group(1)
        value = _resolve_path(payload, path)
        if value is None:
            return "(unknown)"
        if isinstance(value, (dict, list)):
            import json
            return json.dumps(value, indent=2, default=str)
        return str(value)

    return TEMPLATE_RE.sub(replacer, template)


async def find_matching_triggers(
    db: AsyncSession,
    agent_id: str,
    source: str,
    event_type: str,
    payload: dict,
) -> list[EventTrigger]:
    """Find all enabled EventTriggers that match the incoming webhook.

    Returns triggers sorted by priority (highest first).
    """
    # Fetch enabled triggers for this agent
    result = await db.execute(
        select(EventTrigger)
        .where(EventTrigger.agent_id == agent_id)
        .where(EventTrigger.enabled == True)  # noqa: E712
        .order_by(EventTrigger.priority.desc())
    )
    triggers = list(result.scalars().all())

    matched = []
    for trigger in triggers:
        # Check source filter
        if trigger.source_filter and trigger.source_filter.lower() != source.lower():
            continue

        # Check event_type filter
        if trigger.event_type_filter and trigger.event_type_filter.lower() != event_type.lower():
            continue

        # Check payload conditions
        if trigger.payload_conditions and not match_conditions(payload, trigger.payload_conditions):
            continue

        matched.append(trigger)

    return matched


async def fire_trigger(
    trigger: EventTrigger,
    payload: dict,
    source: str,
    event_type: str,
    db: AsyncSession,
) -> str:
    """Fire a trigger: interpolate the prompt template and update stats.

    Returns the interpolated prompt ready for task creation.
    """
    prompt = interpolate_template(trigger.prompt_template, payload)

    # Update trigger stats
    trigger.fire_count += 1
    trigger.last_fired_at = datetime.now(timezone.utc)
    await db.flush()

    logger.info(
        f"Trigger '{trigger.name}' (id={trigger.id}) fired for "
        f"{source}/{event_type} → agent {trigger.agent_id}"
    )
    return prompt

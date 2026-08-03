"""Replay-Modus: turn a recorded computer-use session into a reusable Skill.

A recording (see app/api/computer_use.py) is a flat list of steps — each an
action, its params, and a screenshot taken right after it. That transcript is
literal: exact pixel coordinates, exact typed strings. Replaying it verbatim
would break the moment a window sits somewhere else or a different file is
being processed.

So instead of storing the transcript as a macro, we hand it (with the
screenshots) to a vision-capable model and ask for a SKILL.md written in
PROSE: "click the Save button in the top right" instead of "click 1042,178",
and named parameters instead of the concrete values that happened to be typed
during recording. The agent then replays it by READING that skill and driving
its own computer_* tools — which is what makes it survive UI drift, and means
replay needs no separate execution engine (any skill already works this way).
"""

import json
import logging

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.skill import Skill, SkillCategory, SkillStatus

logger = logging.getLogger(__name__)

# Vision-capable default. Screenshots are the whole point here, so a
# text-only model cannot do this job.
DEFAULT_AUTHORING_MODEL = "claude-sonnet-5"

# Screenshots are large; cap how many we send so a long recording can't blow
# up the request. We keep the FIRST and LAST few (start state + end state
# carry the most meaning) and describe the rest by action only.
MAX_SCREENSHOTS = 8


class ReplaySkillError(RuntimeError):
    """Raised when a recording cannot be turned into a skill."""


def _select_screenshot_indices(step_count: int) -> set[int]:
    """Pick which steps contribute a screenshot, biased to start and end."""
    if step_count <= MAX_SCREENSHOTS:
        return set(range(step_count))
    head = MAX_SCREENSHOTS // 2
    tail = MAX_SCREENSHOTS - head
    return set(range(head)) | set(range(step_count - tail, step_count))


def _describe_step(index: int, step: dict) -> str:
    params = {k: v for k, v in (step.get("params") or {}).items()}
    return f"Schritt {index + 1}: action={step.get('action')} params={json.dumps(params, ensure_ascii=False)}"


def build_authoring_prompt(steps: list[dict], goal_hint: str = "") -> list[dict]:
    """Build the Anthropic `content` blocks (text + images) for skill authoring."""
    keep = _select_screenshot_indices(len(steps))
    intro = (
        "Du bekommst die Aufzeichnung eines Ablaufs, den jemand auf einem Desktop "
        "durchgefuehrt hat: pro Schritt die ausgefuehrte Aktion, ihre Parameter und "
        "(fuer einen Teil der Schritte) einen Screenshot des Zustands DANACH.\n\n"
        "Schreibe daraus eine wiederverwendbare Arbeitsanweisung (SKILL.md) fuer einen "
        "Agenten, der denselben Ablauf spaeter selbst ausfuehren soll.\n\n"
        "WICHTIG — die Aufzeichnung ist woertlich, die Anleitung muss allgemein sein:\n"
        "- Beschreibe Klickziele SEMANTISCH ('klicke auf den Speichern-Button oben rechts'), "
        "NIEMALS ueber Pixel-Koordinaten aus der Aufzeichnung.\n"
        "- Erkenne, welche getippten Werte VARIABEL sind (Dateinamen, Betraege, Suchbegriffe, "
        "Datumsangaben, Empfaenger) und mache daraus benannte Parameter in geschweiften "
        "Klammern, z.B. {rechnungsdatei}. Werte, die immer gleich sind (Menuepunkte, "
        "Programmnamen), bleiben fest.\n"
        "- Nenne die Parameter am Anfang unter '## Parameter' mit je einer Zeile Erklaerung.\n"
        "- Schreibe die Schritte als nummerierte Liste unter '## Schritte'.\n"
        "- Ergaenze unter '## Voraussetzungen', was vorher offen/installiert sein muss.\n\n"
        "Antworte mit einem JSON-Objekt und NICHTS sonst:\n"
        '{"name": "kurzer-kebab-case-name", "description": "ein Satz, was der Skill tut", '
        '"content": "der vollstaendige SKILL.md-Text als Markdown"}'
    )
    if goal_hint:
        intro += f"\n\nDer Nutzer beschreibt das Ziel so: {goal_hint}"

    blocks: list[dict] = [{"type": "text", "text": intro}]
    for i, step in enumerate(steps):
        blocks.append({"type": "text", "text": _describe_step(i, step)})
        shot = step.get("screenshot_b64") if i in keep else None
        if shot:
            blocks.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": shot},
            })
    return blocks


def _parse_authoring_response(raw: str) -> dict:
    """Parse the model's JSON reply, tolerating ```json fences."""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[: -3]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ReplaySkillError(f"Model did not return valid JSON: {exc}") from exc
    if not isinstance(data, dict) or not data.get("content"):
        raise ReplaySkillError("Model reply is missing the 'content' field")
    return data


async def author_skill_from_steps(
    steps: list[dict], goal_hint: str = "", model: str | None = None,
) -> dict:
    """Ask the vision model to write a SKILL.md from a recording. Returns
    {"name", "description", "content"}."""
    if not steps:
        raise ReplaySkillError("Recording is empty — nothing to turn into a skill")
    api_key = settings.anthropic_api_key
    if not api_key:
        raise ReplaySkillError(
            "No Anthropic API key configured — skill authoring needs a vision model"
        )

    blocks = build_authoring_prompt(steps, goal_hint)
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": model or DEFAULT_AUTHORING_MODEL,
                    "max_tokens": 4096,
                    "messages": [{"role": "user", "content": blocks}],
                },
            )
        resp.raise_for_status()
        raw = resp.json()["content"][0]["text"]
    except httpx.HTTPError as exc:
        raise ReplaySkillError(f"Skill authoring call failed: {exc}") from exc

    return _parse_authoring_response(raw)


async def _unique_skill_name(db: AsyncSession, base: str) -> str:
    """Append -2, -3, … until the name is free (skills.name is unique)."""
    name = base
    for suffix in range(2, 50):
        existing = (await db.execute(select(Skill).where(Skill.name == name))).scalar_one_or_none()
        if existing is None:
            return name
        name = f"{base}-{suffix}"
    raise ReplaySkillError(f"Could not find a free skill name for '{base}'")


async def create_skill_from_recording(
    db: AsyncSession,
    steps: list[dict],
    *,
    created_by: str,
    goal_hint: str = "",
    model: str | None = None,
) -> Skill:
    """Author a skill from a recording and store it as a DRAFT.

    Draft, not active: this content is machine-generated from screenshots and
    must be reviewed by a human before it shows up as a usable skill — same
    stance as agent-proposed skills.
    """
    authored = await author_skill_from_steps(steps, goal_hint=goal_hint, model=model)
    raw_name = str(authored.get("name") or "aufgezeichneter-ablauf").strip()
    safe_name = "".join(c if c.isalnum() or c in "-_" else "-" for c in raw_name.lower())[:80]
    name = await _unique_skill_name(db, safe_name or "aufgezeichneter-ablauf")

    skill = Skill(
        name=name,
        description=str(authored.get("description") or "").strip()[:500],
        content=str(authored["content"]),
        category=SkillCategory.ROUTINE,
        status=SkillStatus.DRAFT,
        created_by=created_by,
    )
    db.add(skill)
    await db.commit()
    await db.refresh(skill)
    logger.info(
        "[replay] Authored skill '%s' (id=%s) from %d recorded steps", name, skill.id, len(steps)
    )
    return skill

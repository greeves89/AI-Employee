"""Meeting Room API — create, manage, and run group chats between agents."""

import asyncio
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies import require_auth
from app.models.meeting_room import MeetingRoom
from app.models.agent import Agent, AgentState

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/meeting-rooms", tags=["meeting-rooms"])

# Track running rooms in memory
_running_rooms: dict[str, asyncio.Task] = {}
# Track moderator container IDs per room  {room_id: container_id}
_moderator_containers: dict[str, str] = {}


DEFAULT_STAGES = [
    {"name": "Eröffnung", "rounds": 1, "focus": "intro"},
    {"name": "Analyse",   "rounds": 3, "focus": "research"},
    {"name": "Synthese",  "rounds": 1, "focus": "synthesis"},
]

STAGE_PROMPTS = {
    "intro": (
        "Du bist in der **Eröffnungsphase** des Meetings.\n"
        "Lies zuerst /workspace/knowledge.md um deine Rolle und dein Wissen abzurufen.\n"
        "Stelle dich kurz vor, erkläre deine Kernkompetenz und deine erste Perspektive zum Thema.\n"
        "Halte dich kurz (max 200 Wörter)."
    ),
    "research": (
        "Du bist in der **Analysephase** des Meetings.\n"
        "Lies /workspace/knowledge.md für relevantes Projektwissen das du einbringen kannst.\n"
        "Bringe neue, konkrete Fakten, Argumente oder Lösungsansätze ein.\n"
        "Wiederhole NICHT was bereits gesagt wurde — baue darauf auf oder widersprich konstruktiv.\n"
        "Max 300 Wörter."
    ),
    "synthesis": (
        "Du bist in der **Synthesephase** des Meetings.\n"
        "Fasse die 3 wichtigsten Erkenntnisse der Diskussion aus DEINER Perspektive zusammen.\n"
        "Nenne konkrete nächste Schritte die du persönlich übernehmen würdest.\n"
        "Max 250 Wörter."
    ),
    "default": (
        "Lies /workspace/knowledge.md für relevantes Wissen.\n"
        "Bringe neue Perspektiven ein. Wiederhole nicht was andere sagten.\n"
        "Max 300 Wörter."
    ),
}


def _resolve_stage(stages: list[dict], rounds_completed: int) -> tuple[dict, int]:
    """Return (current_stage, stage_index) based on rounds_completed."""
    cumulative = 0
    for i, stage in enumerate(stages):
        cumulative += stage.get("rounds", 1)
        if rounds_completed < cumulative:
            return stage, i
    return stages[-1], len(stages) - 1


class StageConfig(BaseModel):
    name: str
    rounds: int = 1
    focus: str = "research"


class CreateRoom(BaseModel):
    name: str
    topic: str = ""
    agent_ids: list[str]
    max_rounds: int = 10
    stages_config: list[StageConfig] | None = None
    use_moderator: bool = False
    moderator_ai_account_id: str | None = None  # per-meeting moderator LLM (None = global default)


class StartRoom(BaseModel):
    initial_message: str = ""


# ─── CRUD ────────────────────────────────────────────────────────


@router.get("/")
async def list_rooms(user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(MeetingRoom).where(MeetingRoom.is_active == True).order_by(MeetingRoom.created_at.desc())
    )
    rooms = result.scalars().all()
    return {
        "rooms": [
            {
                "id": r.id,
                "name": r.name,
                "topic": r.topic,
                "agent_ids": r.agent_ids,
                "state": r.state,
                "current_turn": r.current_turn,
                "rounds_completed": r.rounds_completed,
                "max_rounds": r.max_rounds,
                "message_count": len(r.messages or []),
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "scheduled_for": r.scheduled_for.isoformat() if r.scheduled_for else None,
            }
            for r in rooms
        ]
    }


@router.get("/{room_id}")
async def get_room(room_id: str, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    room = await db.scalar(select(MeetingRoom).where(MeetingRoom.id == room_id))
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")

    # Resolve agent names
    agent_names = {}
    for aid in room.agent_ids:
        agent = await db.scalar(select(Agent).where(Agent.id == aid))
        agent_names[aid] = agent.name if agent else aid

    return {
        "id": room.id,
        "name": room.name,
        "topic": room.topic,
        "agent_ids": room.agent_ids,
        "agent_names": agent_names,
        "state": room.state,
        "current_turn": room.current_turn,
        "rounds_completed": room.rounds_completed,
        "max_rounds": room.max_rounds,
        "stages_config": room.stages_config,
        "use_moderator": room.use_moderator,
        "messages": room.messages or [],
        "created_at": room.created_at.isoformat() if room.created_at else None,
        "scheduled_for": room.scheduled_for.isoformat() if room.scheduled_for else None,
    }


@router.post("/", status_code=201)
async def create_room(
    body: CreateRoom,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    if len(body.agent_ids) < 2:
        raise HTTPException(status_code=400, detail="Need at least 2 agents")
    if len(body.agent_ids) > 6:
        raise HTTPException(status_code=400, detail="Max 6 agents per room")

    # Verify all agents exist. (We no longer require them to be RUNNING here: agents
    # idle-exit between turns, and the meeting loop now wakes each participant on demand
    # before its turn — so an idle/stopped agent is fine and gets restarted automatically.)
    for aid in body.agent_ids:
        agent = await db.scalar(select(Agent).where(Agent.id == aid))
        if not agent:
            raise HTTPException(status_code=400, detail=f"Agent {aid} not found")

    # Build stages config and derive max_rounds from it
    stages = None
    max_rounds = body.max_rounds
    if body.stages_config:
        stages = [s.model_dump() for s in body.stages_config]
        max_rounds = sum(s["rounds"] for s in stages)

    room = MeetingRoom(
        id=uuid.uuid4().hex[:12],
        name=body.name,
        topic=body.topic,
        agent_ids=body.agent_ids,
        max_rounds=max_rounds,
        stages_config=stages,
        use_moderator=body.use_moderator,
        moderator_ai_account_id=(body.moderator_ai_account_id or None),
        created_by=user.id if user.id != "__anonymous__" else None,
        messages=[],
    )
    db.add(room)
    await db.commit()
    await db.refresh(room)

    return {
        "id": room.id,
        "name": room.name,
        "topic": room.topic,
        "agent_ids": room.agent_ids,
        "state": room.state,
        "max_rounds": room.max_rounds,
    }


class ScheduleMeetingRequest(BaseModel):
    name: str
    topic: str
    agent_ids: list[str]
    run_at: str | None = None          # ISO datetime for one-shot; None = now
    cron_expression: str | None = None  # recurring cron expression
    max_rounds: int = 5
    stages_config: list[StageConfig] | None = None
    use_moderator: bool = True
    initial_message: str = "Geplantes Meeting startet."


@router.post("/schedule", status_code=201)
async def schedule_meeting(
    body: ScheduleMeetingRequest,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Create a scheduled meeting — either one-shot (run_at) or recurring (cron_expression)."""
    import json as _json
    from datetime import datetime, timezone
    from app.models.schedule import Schedule

    if len(body.agent_ids) < 2:
        raise HTTPException(status_code=400, detail="Need at least 2 agents")

    stages = [s.model_dump() for s in body.stages_config] if body.stages_config else None
    max_rounds = sum(s["rounds"] for s in stages) if stages else body.max_rounds

    meeting_config = _json.dumps({
        "name": body.name,
        "topic": body.topic,
        "agent_ids": body.agent_ids,
        "max_rounds": max_rounds,
        "stages_config": stages,
        "use_moderator": body.use_moderator,
        "initial_message": body.initial_message,
        "created_by": f"user:{user.id}" if hasattr(user, "id") else "user",
    })

    if body.run_at:
        try:
            next_run = datetime.fromisoformat(body.run_at.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid run_at datetime format")
    else:
        next_run = datetime.now(timezone.utc)

    schedule = Schedule(
        id=uuid.uuid4().hex[:12],
        name=f"[Meeting] {body.name}",
        prompt=f"__meeting__:{meeting_config}",
        interval_seconds=0,
        cron_expression=body.cron_expression,
        agent_id=None,
        enabled=True,
        next_run_at=next_run,
    )
    db.add(schedule)
    await db.commit()

    return {
        "schedule_id": schedule.id,
        "name": schedule.name,
        "next_run_at": schedule.next_run_at.isoformat(),
        "cron_expression": schedule.cron_expression,
        "recurring": bool(schedule.cron_expression),
    }


@router.delete("/{room_id}")
async def delete_room(room_id: str, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    room = await db.scalar(select(MeetingRoom).where(MeetingRoom.id == room_id))
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")

    # Stop if running
    if room_id in _running_rooms:
        _running_rooms[room_id].cancel()
        del _running_rooms[room_id]

    room.is_active = False
    room.state = "completed"
    await db.commit()
    return {"status": "deleted"}


# ─── Start / Stop ────────────────────────────────────────────────


@router.post("/{room_id}/start")
async def start_room(
    room_id: str,
    body: StartRoom,
    request: Request,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    room = await db.scalar(select(MeetingRoom).where(MeetingRoom.id == room_id))
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    if room.state == "running":
        raise HTTPException(status_code=400, detail="Room already running")

    room.state = "running"
    room.current_turn = 0
    if body.initial_message:
        room.messages = list(room.messages or []) + [{
            "role": "system",
            "agent_id": None,
            "content": body.initial_message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }]
    await db.commit()

    redis = request.app.state.redis
    docker = request.app.state.docker

    # Wake all participants up front so they are warm for round 1 (they idle-exit when
    # not in use). Best-effort — the meeting loop also wakes each agent before its turn.
    for aid in (room.agent_ids or []):
        await _ensure_agent_running(aid, docker, redis)

    # Spin up a dedicated Haiku moderator container if moderator is enabled
    mod_agent_id = None
    if room.use_moderator:
        from app.config import settings as _settings
        mod_agent_id = await _start_moderator_container(room_id, docker, _settings.redis_url_internal)

    task = asyncio.create_task(_run_meeting(room_id, redis, mod_agent_id=mod_agent_id, docker=docker))
    _running_rooms[room_id] = task

    return {"status": "started", "room_id": room_id}


@router.post("/{room_id}/stop")
async def stop_room(room_id: str, request: Request, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    room = await db.scalar(select(MeetingRoom).where(MeetingRoom.id == room_id))
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")

    if room_id in _running_rooms:
        _running_rooms[room_id].cancel()
        del _running_rooms[room_id]

    if room_id in _moderator_containers:
        await _stop_moderator_container(room_id, request.app.state.docker)

    room.state = "paused"
    await db.commit()
    return {"status": "stopped"}


# ─── Round-Robin Engine ──────────────────────────────────────────


def _moderator_agent_id(room_id: str) -> str:
    return f"mod-{room_id}"


async def _start_moderator_container(room_id: str, docker, redis_url_internal: str) -> str | None:
    """Spin up a lightweight Haiku agent container to act as meeting moderator."""
    from app.config import settings
    from app.core.agent_manager import AgentManager

    mod_id = _moderator_agent_id(room_id)
    container_name = f"ai-moderator-{room_id}"

    # Resolve the moderator's LLM. The customer may run Azure custom_llm (no Anthropic),
    # so a hardcoded claude_code/Haiku moderator dies with "Unable to connect to API
    # (ConnectionRefused)". The moderator must use a real AI-Account like any agent.
    # Priority: admin-set moderator account -> first available AI-Account -> platform default.
    from app.services.settings_service import SettingsService
    from app.db.session import async_session_factory

    llm_env: dict[str, str] = {}
    agent_mode = "claude_code"
    provider_env: dict[str, str] = {}
    try:
        async with async_session_factory() as db:
            am = AgentManager(db, docker, None)
            # Priority: per-meeting moderator account -> global default -> first available.
            mroom = await db.scalar(select(MeetingRoom).where(MeetingRoom.id == room_id))
            acct_id = (mroom.moderator_ai_account_id if mroom else None) \
                or await SettingsService(db).get("meeting_moderator_ai_account_id")
            cfg = None
            if acct_id:
                try:
                    cfg = await am._effective_llm_config(int(acct_id), None, None)
                except Exception:
                    cfg = None
            if not cfg:
                from app.models.ai_account import AIAccount
                acc = (await db.execute(select(AIAccount).limit(1))).scalars().first()
                if acc:
                    cfg = await am._effective_llm_config(acc.id, None, None)
            if cfg and cfg.get("provider_type"):
                llm_env = AgentManager._llm_env(cfg)
                agent_mode = AgentManager._mode_for_ai_provider(cfg.get("provider_type"))
                logger.info(f"[Moderator] LLM: mode={agent_mode} provider={cfg.get('provider_type')} model={cfg.get('model_name')}")
    except Exception as e:
        logger.warning(f"[Moderator] LLM-Config konnte nicht aufgeloest werden: {e}")

    if not llm_env:
        provider_env = AgentManager._build_provider_env(None)  # last resort (anthropic/claude_code)

    env = {
        "AGENT_ID": mod_id,
        "AGENT_NAME": "Moderator",
        "AGENT_ROLE": "Meeting-Moderator",
        "AGENT_TOKEN": mod_id,          # not used for auth, just identification
        "REDIS_URL": redis_url_internal,
        "ORCHESTRATOR_URL": "http://ai-employee-orchestrator:8000",
        "AGENT_MODE": agent_mode,
        "MAX_TURNS": "3",               # moderator needs very few turns
        "EXTENDED_THINKING": "false",
        **provider_env,
        **llm_env,
    }

    try:
        # Remove stale container if it exists
        try:
            old = docker.client.containers.get(container_name)
            old.remove(force=True)
        except Exception:
            pass

        container = docker.create_container(
            image=settings.agent_image,
            name=container_name,
            environment=env,
            volume_name=f"moderator-workspace-{room_id}",
            network=settings.agent_network,
            memory_limit="512m",
            cpu_quota=50000,
            needs_sudo=False,
        )
        _moderator_containers[room_id] = container.id
        logger.info(f"[Moderator] Container started for room {room_id}: {container.id[:12]}")
        return mod_id
    except Exception as e:
        logger.warning(f"[Moderator] Failed to start container for room {room_id}: {e}")
        return None


async def _stop_moderator_container(room_id: str, docker) -> None:
    """Stop and remove the moderator container for a room."""
    container_id = _moderator_containers.pop(room_id, None)
    container_name = f"ai-moderator-{room_id}"
    for ref in [container_id, container_name]:
        if not ref:
            continue
        try:
            c = docker.client.containers.get(ref)
            c.remove(force=True)
            logger.info(f"[Moderator] Container removed for room {room_id}")
        except Exception:
            pass

    # Clean up moderator workspace volume
    try:
        vol = docker.client.volumes.get(f"moderator-workspace-{room_id}")
        vol.remove()
    except Exception:
        pass


async def _moderator_request(room_id: str, mod_id: str, prompt: str, redis, timeout_rounds: int = 6) -> str | None:
    """Send a moderation request to the moderator container and wait for its response.

    ``timeout_rounds`` × 5s = max wait. Quick turn-moderation uses the default (30s);
    the heavier end-of-meeting synthesis passes a larger value."""
    import json as _json
    payload = _json.dumps({"type": "meeting", "room_id": room_id, "prompt": prompt})
    await redis.client.rpush(f"agent:{mod_id}:messages", payload)

    response_key = f"meeting:{room_id}:response:{mod_id}"
    for _ in range(timeout_rounds):
        result = await redis.client.lpop(response_key)
        if result:
            return result if isinstance(result, str) else result.decode()
        await asyncio.sleep(5)
    logger.warning(f"[Moderator] No response from container for room {room_id} within {timeout_rounds * 5}s")
    return None


async def _haiku_call(api_key: str, system: str, user: str, max_tokens: int = 120) -> str | None:
    """Shared helper for direct haiku API calls. Supports both API key and OAuth token."""
    import httpx as _httpx
    from app.config import settings as _settings

    # Prefer explicit api_key, fall back to OAuth token
    token = api_key or _settings.claude_code_oauth_token or ""
    if not token:
        return None

    # OAuth tokens use Authorization: Bearer, API keys use x-api-key
    if token.startswith("sk-ant-oat"):
        auth_headers = {"Authorization": f"Bearer {token}"}
    else:
        auth_headers = {"x-api-key": token}

    try:
        async with _httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    **auth_headers,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": max_tokens,
                    "system": system,
                    "messages": [{"role": "user", "content": user}],
                },
            )
            if resp.status_code == 200:
                return resp.json()["content"][0]["text"].strip()
            logger.warning(f"[Haiku] API returned {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        logger.warning(f"[Haiku] API call failed: {e}")
    return None


async def _select_next_speaker(
    room: MeetingRoom, agent_name_map: dict, stage_name: str | None
) -> str | None:
    """Use haiku to dynamically select the most relevant next speaker."""
    import json as _json
    from app.config import settings as _settings

    api_key = _settings.anthropic_api_key
    if not api_key:
        return None

    recent = [m for m in (room.messages or [])[-8:] if m.get("role") in ("agent", "moderator", "system")]
    context = "\n".join(
        f"[{agent_name_map.get(m.get('agent_id'), m.get('agent_id') or 'System')}]: {m['content'][:200]}"
        for m in recent
    )
    agents_list = "\n".join(f"- {name} (id: {aid})" for aid, name in agent_name_map.items())
    stage_hint = f" Aktuelle Phase: {stage_name}." if stage_name else ""

    system_prompt = (
        f"Du bist ein Meeting-Moderator.{stage_hint}\n"
        f"Wähle den nächsten Sprecher der den Diskurs am meisten voranbringt.\n"
        f"Bevorzuge Agenten die noch nicht kürzlich gesprochen haben oder die besonders relevant wären.\n"
        f"Antworte NUR mit gültigem JSON: {{\"next_agent_id\": \"<id>\"}}"
    )
    user_prompt = (
        f"Thema: {room.topic or '(kein Thema)'}\n"
        f"Teilnehmer:\n{agents_list}\n\n"
        f"Letzte Beiträge:\n{context or '(noch keine)'}\n\nWen als nächstes?"
    )

    text = await _haiku_call(api_key, system_prompt, user_prompt, max_tokens=60)
    if text:
        try:
            data = _json.loads(text)
            next_id = data.get("next_agent_id")
            if next_id in agent_name_map:
                return next_id
        except Exception:
            pass
    return None


async def _parallel_reactions(
    room: MeetingRoom, last_response: str, speaker_id: str, agent_name_map: dict
) -> list[dict]:
    """Get 1-sentence reactions from up to 2 other agents via parallel haiku calls."""
    from app.config import settings as _settings

    api_key = _settings.anthropic_api_key
    if not api_key:
        return []

    reactors = [aid for aid in room.agent_ids if aid != speaker_id][:2]
    if not reactors:
        return []

    speaker_name = agent_name_map.get(speaker_id, speaker_id)

    async def _react(agent_id: str) -> dict | None:
        agent_name = agent_name_map.get(agent_id, agent_id)
        system = (
            f"Du bist {agent_name}, ein KI-Agent in einem Meeting.\n"
            f"Reagiere auf den letzten Beitrag in GENAU EINEM prägnanten Satz.\n"
            f"Stimme zu, widersprich, oder stelle eine kurze Folgefrage — direkt, ohne Einleitung."
        )
        user = (
            f"Thema: {room.topic or '(kein Thema)'}\n"
            f"{speaker_name}: {last_response[:400]}\n\nDeine 1-Satz-Reaktion:"
        )
        text = await _haiku_call(api_key, system, user, max_tokens=80)
        if text:
            return {
                "role": "reaction",
                "agent_id": agent_id,
                "content": text,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "round": room.rounds_completed,
            }
        return None

    results = await asyncio.gather(*[_react(aid) for aid in reactors])
    return [r for r in results if r is not None]


async def _compress_context(messages: list[dict], agent_name_map: dict) -> str | None:
    """Summarize older messages into a compact context block via haiku."""
    from app.config import settings as _settings

    api_key = _settings.anthropic_api_key
    if not api_key or len(messages) < 4:
        return None

    context = "\n".join(
        f"[{agent_name_map.get(m.get('agent_id'), m.get('agent_id') or 'System')}]: {m['content'][:300]}"
        for m in messages
        if m.get("role") in ("agent", "system")
    )
    return await _haiku_call(
        api_key,
        "Fasse die Kernpunkte dieses Meeting-Ausschnitts in 3-4 Sätzen zusammen. Nur die wichtigsten Aussagen.",
        f"Meeting-Ausschnitt:\n{context}",
        max_tokens=250,
    )


async def _moderator_turn(room: MeetingRoom, next_agent_name: str, stage_name: str | None) -> str | None:
    """Call Anthropic directly (haiku) to produce a short moderator message."""
    from app.config import settings as _settings

    api_key = _settings.anthropic_api_key
    if not api_key:
        return None

    recent = [m for m in (room.messages or [])[-6:] if m.get("role") in ("agent", "system")]
    context = "\n".join(
        f"[{m.get('agent_id') or 'System'}]: {m['content'][:300]}"
        for m in recent
    )
    stage_hint = f" Wir sind in der Phase: **{stage_name}**." if stage_name else ""

    system_prompt = (
        "Du bist ein präziser Meeting-Moderator.{}\n"
        "In maximal 2 Sätzen: (1) letzte Kernaussage kurz zusammenfassen, "
        "(2) konkrete, spezifische Frage/Aufgabe an den nächsten Sprecher. "
        "Keine Floskeln. Kein Dank. Direkt."
    ).format(stage_hint)

    user_prompt = (
        f"Thema: {room.topic or '(kein Thema)'}\n"
        f"Letzte Beiträge:\n{context or '(noch keine)'}\n\n"
        f"Moderation für: **{next_agent_name}**"
    )

    return await _haiku_call(api_key, system_prompt, user_prompt, max_tokens=120)


async def _ensure_agent_running(agent_id: str, docker, redis) -> None:
    """Wake an idle-exited participant so it can take its meeting turn.

    Meeting turns are pushed straight to the agent's redis queue, which is ONLY consumed
    while the container runs. Agents idle-exit between turns (the gap to their next turn
    can exceed the idle timeout), so the message would sit in a queue nobody reads →
    '[Agent hat nicht geantwortet]'. Restart on demand (cheap if already running)."""
    if not docker or not agent_id:
        return
    try:
        from app.db.session import async_session_factory
        from app.core.agent_manager import AgentManager
        from app.models.agent import Agent as _Agent
        async with async_session_factory() as db:
            agent = await db.scalar(select(_Agent).where(_Agent.id == agent_id))
            if not agent:
                return
            running = bool(agent.container_id) and docker.get_container_status(agent.container_id) == "running"
            if not running:
                await AgentManager(db, docker, redis).start_agent(agent_id)
    except Exception:
        logger.warning(f"[Meeting] ensure_agent_running failed for {agent_id}", exc_info=True)


async def _run_meeting(room_id: str, redis, mod_agent_id: str | None = None, docker=None) -> None:
    """Run the meeting loop with dynamic speaker selection, parallel reactions, and context compression."""
    import json
    from app.db.session import async_session_factory

    # Track compressed summary of older context
    _context_summary: str | None = None
    _summary_covers_up_to: int = 0  # message index covered by summary

    try:
        while True:
            async with async_session_factory() as db:
                room = await db.scalar(select(MeetingRoom).where(MeetingRoom.id == room_id))
                if not room or room.state != "running":
                    break

                if room.max_rounds > 0 and room.rounds_completed >= room.max_rounds:
                    logger.info(f"Meeting room {room_id} completed after {room.rounds_completed} rounds")
                    await _generate_todo_summary(room, redis, mod_agent_id=mod_agent_id, docker=docker)
                    room.state = "completed"
                    await db.commit()
                    break

                agent_ids = room.agent_ids
                if not agent_ids:
                    break

                # Resolve stage
                stages = room.stages_config or []
                if stages:
                    stage, _ = _resolve_stage(stages, room.rounds_completed)
                    focus = stage.get("focus", "research")
                    stage_name = stage.get("name", "Diskussion")
                    stage_instruction = STAGE_PROMPTS.get(focus, STAGE_PROMPTS["default"])
                    stage_header = f"## Phase: {stage_name}\n{stage_instruction}\n\n"
                else:
                    stage_header = STAGE_PROMPTS["default"] + "\n\n"
                    stage_name = None

                # Build agent name map
                from app.models.agent import Agent as _Agent
                agent_name_map = {}
                for aid in agent_ids:
                    a = await db.scalar(select(_Agent).where(_Agent.id == aid))
                    agent_name_map[aid] = a.name if a else aid

                # Dynamic speaker selection via moderator container
                messages = room.messages or []
                turn_idx = room.current_turn % len(agent_ids)
                current_agent_id = agent_ids[turn_idx]

                # Keep round-robin speaker order (moderator handles direction via questions, not selection)

                # Publish stage transition on phase boundary
                if stage_name and messages:
                    prev_stage, _ = _resolve_stage(stages, max(0, room.rounds_completed - 1))
                    if prev_stage.get("name") != stage_name and not any(
                        m.get("content", "").startswith(f"--- Phase wechsel: **{stage_name}**")
                        for m in messages[-3:]
                    ):
                        transition_msg = {
                            "role": "system",
                            "agent_id": None,
                            "content": f"--- Phase wechsel: **{stage_name}** ---",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        }
                        room.messages = list(messages) + [transition_msg]
                        messages = room.messages

                # Context compression: keep summary + last 3 raw messages
                nonlocal_summary = _context_summary
                agent_msgs = [m for m in messages if m.get("role") in ("agent", "system", "moderator")]
                if len(agent_msgs) > 6 and not nonlocal_summary:
                    # Compress everything except last 3
                    to_compress = agent_msgs[:-3]
                    nonlocal_summary = await _compress_context(to_compress, agent_name_map)
                    if nonlocal_summary:
                        _context_summary = nonlocal_summary
                        _summary_covers_up_to = len(to_compress)

                # Build context block
                recent_raw = agent_msgs[-3:] if nonlocal_summary else agent_msgs[-20:]
                context_lines = [f"[{m.get('agent_id') or 'System'}]: {m['content']}\n" for m in recent_raw]

                if nonlocal_summary:
                    context_block = (
                        f"**Zusammenfassung bisheriger Diskussion:**\n{nonlocal_summary}\n\n"
                        f"**Letzte Beiträge:**\n{''.join(context_lines)}"
                    )
                else:
                    context_block = (
                        ''.join(context_lines) if context_lines else '(Noch keine Nachrichten — du beginnst!)'
                    )

                # Moderator directed question (via container)
                agent_message_count = sum(1 for m in messages if m.get("role") == "agent")
                moderator_text = None

                # Build agenda context string (used in both opening and subsequent turns)
                def _agenda_context(rounds_done: int, max_rounds: int) -> str:
                    if not stages:
                        return f"Fortschritt: {rounds_done}/{max_rounds} Runden."
                    lines = []
                    cumulative = 0
                    for s in stages:
                        r = s.get("rounds", 1)
                        done = min(max(rounds_done - cumulative, 0), r)
                        marker = "✓" if done >= r else ("▶" if done > 0 else "○")
                        lines.append(f"  {marker} {s.get('name','?')} ({done}/{r} Runden)")
                        cumulative += r
                    return "Agenda:\n" + "\n".join(lines) + f"\nGesamt: {rounds_done}/{max_rounds} Runden"

                agenda_ctx = _agenda_context(room.rounds_completed, room.max_rounds)

                already_opened = any(m.get("role") == "moderator" for m in messages)
                if mod_agent_id and agent_message_count == 0 and not already_opened:
                    # Fire opening in background so first agent starts immediately (parallel)
                    first_agent_name = agent_name_map.get(current_agent_id, current_agent_id)
                    participants = ", ".join(agent_name_map.values())
                    opening_prompt = (
                        f"Du bist der Moderator eines strukturierten KI-Agent-Meetings.\n"
                        f"Thema: {room.topic or '(kein Thema)'}\n"
                        f"Teilnehmer: {participants}\n"
                        f"{agenda_ctx}\n\n"
                        f"Eröffne das Meeting in 2-3 Sätzen: Thema + Agenda kurz vorstellen, "
                        f"dann **{first_agent_name}** als ersten Sprecher aufrufen. Direkt, keine Floskeln."
                    )

                    async def _fire_opening(rid: str, mid: str, prompt: str, r) -> None:
                        text = await _moderator_request(rid, mid, prompt, r)
                        if text:
                            msg = {
                                "role": "moderator", "agent_id": None,
                                "content": text,
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            }
                            await r.client.publish(f"meeting:{rid}:updates", json.dumps(msg))
                            # Persist into room via own session
                            async with async_session_factory() as _db2:
                                _room2 = await _db2.scalar(select(MeetingRoom).where(MeetingRoom.id == rid))
                                if _room2:
                                    _room2.messages = list(_room2.messages or []) + [msg]
                                    await _db2.commit()

                    asyncio.create_task(_fire_opening(room_id, mod_agent_id, opening_prompt, redis))
                    # moderator_text stays None → agent prompt has no directive yet (opening is async)

                elif mod_agent_id and agent_message_count > 0:
                    next_agent_name = agent_name_map.get(current_agent_id, current_agent_id)
                    recent_ctx = "\n".join(
                        f"[{agent_name_map.get(m.get('agent_id'), m.get('agent_id') or 'System')}]: {m['content'][:200]}"
                        for m in messages[-5:] if m.get("role") in ("agent", "system")
                    )
                    mod_prompt = (
                        f"Du bist ein präziser Meeting-Moderator.\n"
                        f"Thema: {room.topic or '(kein Thema)'}\n"
                        f"{agenda_ctx}\n"
                        f"Aktuelle Phase: {stage_name or 'freie Diskussion'}\n\n"
                        f"Letzte Beiträge:\n{recent_ctx or '(noch keine)'}\n\n"
                        f"In maximal 2 Sätzen: (1) Kernaussage des letzten Beitrags aufgreifen und zur Agenda in Bezug setzen, "
                        f"(2) konkrete Frage/Aufgabe an **{next_agent_name}** stellen, die das Meeting voranbringt. "
                        f"Keine Floskeln. Kein Dank. Direkt."
                    )
                    moderator_text = await _moderator_request(room_id, mod_agent_id, mod_prompt, redis)
                    if moderator_text:
                        mod_msg = {
                            "role": "moderator",
                            "agent_id": None,
                            "content": moderator_text,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        }
                        room.messages = list(room.messages or []) + [mod_msg]
                        await redis.client.publish(
                            f"meeting:{room_id}:updates",
                            json.dumps(mod_msg),
                        )

                topic_line = f"Thema: {room.topic}\n" if room.topic else ""
                moderator_directive = (
                    f"\nDer Moderator hat dich direkt angesprochen:\n> {moderator_text}\n"
                    if moderator_text else ""
                )
                prompt = (
                    f"Du nimmst an einem strukturierten Meeting mit anderen KI-Agenten teil.\n"
                    f"{topic_line}"
                    f"Teilnehmer: {', '.join(agent_name_map.values())}\n\n"
                    f"{stage_header}"
                    f"{moderator_directive}"
                    f"Bisheriges Gespräch:\n{context_block}"
                )

                # Wake the agent if it idle-exited since its last turn, otherwise the
                # message sits in a queue nobody consumes → "hat nicht geantwortet".
                await _ensure_agent_running(current_agent_id, docker, redis)

                # Send to agent queue
                await redis.client.rpush(
                    f"agent:{current_agent_id}:messages",
                    json.dumps({"type": "meeting", "room_id": room_id, "prompt": prompt}),
                )

                # Wait for response, but BOUNDED (90s, 3s granularity) so a slow or
                # overloaded agent can never stall the whole meeting — its turn is
                # skipped with a placeholder and the meeting always progresses.
                response_key = f"meeting:{room_id}:response:{current_agent_id}"
                response = None
                for _ in range(30):
                    result = await redis.client.lpop(response_key)
                    if result:
                        response = result if isinstance(result, str) else result.decode()
                        break
                    await asyncio.sleep(3)

                if not response:
                    response = f"[{agent_name_map.get(current_agent_id, current_agent_id)} hat nicht geantwortet]"

                new_msg = {
                    "role": "agent",
                    "agent_id": current_agent_id,
                    "content": response,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "round": room.rounds_completed,
                }
                room.messages = list(room.messages or []) + [new_msg]

                # Parallel reactions from other agents (fast haiku calls)
                reactions = await _parallel_reactions(room, response, current_agent_id, agent_name_map)
                if reactions:
                    room.messages = list(room.messages) + reactions

                # Advance turn counter (round-robin baseline, overridden next iteration by dynamic selection)
                next_turn = (turn_idx + 1) % len(agent_ids)
                room.current_turn = next_turn
                if next_turn == 0:
                    room.rounds_completed += 1

                await db.commit()

                # Publish main response
                await redis.client.publish(f"meeting:{room_id}:updates", json.dumps(new_msg))
                # Publish reactions
                for r in reactions:
                    await redis.client.publish(f"meeting:{room_id}:updates", json.dumps(r))

                await asyncio.sleep(2)

    except asyncio.CancelledError:
        logger.info(f"Meeting room {room_id} cancelled")
    except Exception as e:
        logger.error(f"Meeting room {room_id} error: {e}")
        async with async_session_factory() as db:
            room = await db.scalar(select(MeetingRoom).where(MeetingRoom.id == room_id))
            if room:
                room.state = "paused"
                await db.commit()
    finally:
        _running_rooms.pop(room_id, None)
        if docker and room_id in _moderator_containers:
            await _stop_moderator_container(room_id, docker)


async def _generate_todo_summary(room: MeetingRoom, redis, mod_agent_id: str | None = None, docker=None) -> None:
    """After all rounds finish, synthesize the action-item todo list.

    Prefers the MODERATOR agent (it directed the meeting and has the full context);
    falls back to the first participant if no moderator is running."""
    import json as _json
    if not room.agent_ids or not room.messages:
        return

    conversation = "\n".join(
        f"[{m.get('agent_id') or 'System'}]: {m['content']}"
        for m in (room.messages or [])
        if m.get("role") in ("agent", "system", "moderator")
    )
    prompt = (
        f"The following meeting just concluded.\n"
        f"Topic: {room.topic or '(no topic)'}\n\n"
        f"Conversation:\n{conversation}\n\n"
        f"Erstelle eine klare, umsetzbare **Todo-Liste** als markdown-Checkliste, genau so:\n"
        f"## Action Items\n"
        f"- [ ] Item 1\n"
        f"- [ ] Item 2\n"
        f"...\n"
        f"Gruppiere nach Priorität: **Sofort**, **Kurzfristig**, **Mittelfristig** (falls sinnvoll). "
        f"Konkret und spezifisch. Max 15 Items.\n\n"
        f"Danach der Abschnitt:\n"
        f"## Meeting-Kontext für Folgetermine\n"
        f"(2-3 Sätze: Was wurde entschieden, welche offenen Fragen bleiben, was ist der Kontext für das nächste Meeting.)\n\n"
        f"Antworte AUSSCHLIESSLICH mit diesem Markdown — keine Dateispeicherung nötig."
    )

    # Wrap-up synthesis: prefer the moderator (it directed the meeting, full context).
    # Fall back to the first participant if the moderator doesn't answer OR returns an
    # error/garbage instead of a real list (e.g. its LLM is unreachable).
    todo_content = None
    synthesizer_id = room.agent_ids[0]
    if mod_agent_id:
        mod_out = await _moderator_request(room.id, mod_agent_id, prompt, redis, timeout_rounds=24)
        if _looks_like_todo(mod_out):
            todo_content, synthesizer_id = mod_out, mod_agent_id
        else:
            logger.warning(f"[Meeting {room.id}] Moderator synthesis unusable, falling back to participant")
    if not todo_content:
        synthesizer_id = room.agent_ids[0]
        await _ensure_agent_running(synthesizer_id, docker, redis)
        payload = _json.dumps({"type": "meeting", "room_id": room.id, "prompt": prompt})
        await redis.client.rpush(f"agent:{synthesizer_id}:messages", payload)
        response_key = f"meeting:{room.id}:response:{synthesizer_id}"
        for _ in range(40):
            result = await redis.client.lpop(response_key)
            if result:
                todo_content = result if isinstance(result, str) else result.decode()
                break
            await asyncio.sleep(3)

    if not todo_content:
        todo_content = "## Action Items\n*(Todo-Liste konnte nicht generiert werden)*"

    summary_msg = {
        "role": "summary",
        "agent_id": synthesizer_id,
        "content": todo_content,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "round": room.rounds_completed,
    }
    room.messages = list(room.messages) + [summary_msg]

    await redis.client.publish(
        f"meeting:{room.id}:updates",
        _json.dumps(summary_msg),
    )
    logger.info(f"Meeting room {room.id} todo summary generated")

    # Assign action items to agents as real tasks
    if todo_content and "Action Items" in todo_content:
        await _assign_tasks_from_summary(room, todo_content, redis)


def _parse_action_items(markdown: str) -> list[str]:
    """Extract individual action items from the markdown checklist."""
    items = []
    for line in markdown.splitlines():
        line = line.strip()
        if line.startswith("- [ ]"):
            item = line[5:].strip()
            if item:
                items.append(item)
    return items


def _extract_followup_context(markdown: str) -> str:
    """Pull the '## Meeting-Kontext für Folgetermine' section out of the todo summary."""
    if not markdown:
        return ""
    out, capture = [], False
    for line in markdown.splitlines():
        low = line.strip().lower()
        if low.startswith("## meeting-kontext f") and "folgetermine" in low:
            capture = True
            continue
        if capture and line.strip().startswith("## "):
            break
        if capture:
            out.append(line)
    return "\n".join(out).strip()


def _next_followup_name(base: str) -> str:
    """Derive the follow-up room name, incrementing a trailing 'Folgetermin N' counter."""
    marker = " — Folgetermin"
    if marker in base:
        head, _, tail = base.rpartition(marker)
        tail = tail.strip()
        try:
            num = int(tail) + 1 if tail else 2
        except ValueError:
            num = 2
        return f"{head}{marker} {num}"
    return f"{base}{marker}"


# Cap how deep the auto-follow-up chain may go. Every meeting that produces action items
# spawns a follow-up which auto-starts, completes, and would spawn ANOTHER — without a cap
# this runs (and bills) forever. The chain depth is encoded in the room name.
MAX_FOLLOWUP_DEPTH = 3


def _followup_depth(name: str) -> int:
    """0 for an original meeting, N for its Nth follow-up (derived from the name)."""
    marker = " — Folgetermin"
    if marker not in (name or ""):
        return 0
    tail = (name or "").rpartition(marker)[2].strip()
    try:
        return int(tail) if tail else 1
    except ValueError:
        return 1


def _looks_like_todo(text: str | None) -> bool:
    """True only if the synthesizer output is a usable action-item list — NOT an LLM
    error string (e.g. 'API Error: Unable to connect to API (ConnectionRefused)')."""
    if not text or not text.strip():
        return False
    low = text.lower()
    if any(s in low for s in ("api error", "connectionrefused", "unable to connect", "rate limit")):
        return False
    return ("action items" in low) or ("- [ ]" in text)


def _extract_followup_date(markdown: str):
    """Parse the agent-proposed follow-up date from the '## Folgetermin' section.
    Tolerant: accepts ISO (YYYY-MM-DD), German (DD.MM.YYYY) and relative ('in N
    Tagen/Wochen'). Returns a future UTC datetime (09:00) or None."""
    import re
    from datetime import timedelta as _td
    if not markdown:
        return None
    now = datetime.now(timezone.utc)
    # Preferred: the explicit FOLLOWUP_DATE marker (we ask for it as the first line).
    fd = re.search(r"FOLLOWUP_DATE:\s*(\d{4})-(\d{1,2})-(\d{1,2})", markdown, re.I)
    if fd:
        try:
            dt = datetime(int(fd.group(1)), int(fd.group(2)), int(fd.group(3)), 9, 0, tzinfo=timezone.utc)
            if dt > now:
                return dt
        except ValueError:
            pass
    m = re.search(r"##\s*Folgetermin\b(.*?)(?:\n##\s|\Z)", markdown, re.S | re.I)
    block = m.group(1) if m else markdown

    # ISO YYYY-MM-DD
    d = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", block)
    if d:
        try:
            dt = datetime(int(d.group(1)), int(d.group(2)), int(d.group(3)), 9, 0, tzinfo=timezone.utc)
            if dt > now:
                return dt
        except ValueError:
            pass
    # German DD.MM.YYYY
    d = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", block)
    if d:
        try:
            dt = datetime(int(d.group(3)), int(d.group(2)), int(d.group(1)), 9, 0, tzinfo=timezone.utc)
            if dt > now:
                return dt
        except ValueError:
            pass
    # Relative: "in N Tagen" / "in N Wochen"
    r = re.search(r"in\s+(\d{1,3})\s*(woche|wochen|tag|tage|tagen)", block, re.I)
    if r:
        n = int(r.group(1))
        days = n * 7 if r.group(2).lower().startswith("woche") else n
        if 0 < days <= 365:
            return now + _td(days=days)
    return None


def _assign_item_to_agent(item: str, agent_name_map: dict) -> str | None:
    """Keyword-match an action item to the most relevant agent by NAME. Returns the
    agent_id, or None if there is no clear match — the caller then distributes those
    items evenly across all participants (instead of dumping them on the first agent)."""
    item_lower = item.lower()
    best_agent_id = None
    best_score = 0
    for agent_id, name in agent_name_map.items():
        # Score by how many words of the agent name appear in the item
        words = name.lower().replace("-", " ").split()
        score = sum(1 for w in words if len(w) > 3 and w in item_lower)
        if score > best_score:
            best_score = score
            best_agent_id = agent_id
    return best_agent_id


async def _maybe_create_planner_tasks(room: MeetingRoom, items: list[str], db) -> int:
    """Best-effort: mirror each action item into MS Planner, in the admin-set target
    plan, using the meeting owner's connected Microsoft (M365) account. If no plan is
    configured or the owner has no M365 connection, this is silently skipped (0).

    Reuses the ms_create_planner_task MCP tool (v1.76) so the Graph logic lives in
    exactly one place. Server-side → harness-agnostic (works with custom_llm agents)."""
    from app.services.settings_service import SettingsService
    plan_id = await SettingsService(db).get("meeting_planner_plan_id")
    if not plan_id or not room.created_by:
        return 0
    from app.services.oauth_service import OAuthService
    from app.core.msgraph_mcp import handle_tool
    try:
        token = await OAuthService(db, None).get_valid_token("microsoft", room.created_by)
    except Exception:
        return 0
    if not token:
        return 0
    created = 0
    for item in items:
        title = (item or "").strip()[:255]
        if not title:
            continue
        try:
            await handle_tool("ms_create_planner_task", {"plan_id": plan_id, "title": title}, token)
            created += 1
        except Exception:
            logger.warning("MS-Planner task create failed (meeting %s)", room.id, exc_info=True)
    if created:
        logger.info("Mirrored %d action items to MS Planner from meeting %s", created, room.id)
    return created


async def _assign_tasks_from_summary(room: MeetingRoom, todo_content: str, redis) -> None:
    """Parse the todo list, assign items to agents, create Task records, persist context."""
    import json as _json
    from app.db.session import async_session_factory
    from app.models.task import Task, TaskStatus, TaskPriority
    from app.models.agent import Agent as _Agent
    from app.models.agent_todo import AgentTodo, TodoStatus

    items = _parse_action_items(todo_content)
    if not items:
        return

    async with async_session_factory() as db:
        # Build agent name map
        agent_name_map = {}
        for aid in room.agent_ids:
            a = await db.scalar(select(_Agent).where(_Agent.id == aid))
            agent_name_map[aid] = a.name if a else aid

        # Distribute items across agents (keyword match + round-robin fallback)
        agent_ids = list(agent_name_map.keys())
        assignments: dict[str, list[str]] = {aid: [] for aid in agent_ids}
        for item in items:
            matched = _assign_item_to_agent(item, agent_name_map)
            if matched:
                assignments[matched].append(item)
            else:
                # No name match → give to the agent with the fewest items so far,
                # so the workload is shared evenly across all participants.
                target = min(agent_ids, key=lambda a: len(assignments[a]))
                assignments[target].append(item)

        created_tasks = []
        for agent_id, agent_items in assignments.items():
            if not agent_items:
                continue
            agent_name = agent_name_map[agent_id]
            task_prompt = (
                f"Im Meeting **{room.name}** (Thema: {room.topic or 'kein Thema'}) "
                f"wurden dir folgende Action Items zugewiesen — sie liegen bereits als TODOs in deiner Liste:\n\n"
                + "\n".join(f"- {it}" for it in agent_items)
                + "\n\n=== AUTONOMY WHITELIST (für diese zugewiesene Aufgabe) ===\n"
                "Diese Arbeit ist dir vom Meeting FREIGEGEBEN. Du DARFST sie OHNE `request_approval` ausführen:\n"
                "  - in deinem eigenen Workspace schreiben (`/workspace/knowledge.md`, `/workspace/.agent_state.md`)\n"
                "  - eigene Recherche-Tools nutzen (web_search, web_fetch, brain_search, memory_search)\n"
                "Nur EXTERNE Seiteneffekte (Mails/Nachrichten senden, externe APIs, Käufe) brauchen weiter Approval.\n"
                "Der Onboarding-Status ist hier IRRELEVANT — arbeite die Punkte direkt ab, frag nicht zurück.\n"
                "=== END WHITELIST ===\n\n"
                "So gehst du vor:\n"
                "1. **Abarbeiten:** Bearbeite jeden Punkt konkret (recherchiere bei Bedarf). Erfinde nichts — "
                "wo du etwas nicht abschließen kannst, halte den konkreten Zwischenstand + nächsten Schritt fest.\n"
                "2. **Dokumentieren:** Schreibe deine Ergebnisse in `/workspace/knowledge.md` unter dem Abschnitt "
                f"'## Meeting-Ergebnisse: {room.name}' — pro Action Item ein kurzer Absatz mit dem Ergebnis. "
                "Diese Ergebnisse bringst du zum Folgetermin mit.\n"
                "Du musst KEINE Todo-Tools aufrufen — der Abschluss deiner TODOs wird automatisch gesetzt, "
                "sobald dieser Task fertig ist."
            )
            import uuid as _uuid
            task = Task(
                id=_uuid.uuid4().hex[:12],
                title=f"[Meeting] {room.name}: {len(agent_items)} Action Item{'s' if len(agent_items) > 1 else ''}",
                prompt=task_prompt,
                status=TaskStatus.PENDING,
                priority=TaskPriority.NORMAL,
                agent_id=agent_id,
                metadata_={"source": "meeting", "room_id": room.id, "items": agent_items},
            )
            db.add(task)
            # Pre-create the structured TODOs so they appear in the agent's Todo tab
            # immediately — deterministic, independent of whether the agent calls its
            # todo tools. The agent then sets deadlines + completes them via the tools.
            for _i, _it in enumerate(agent_items):
                db.add(AgentTodo(
                    agent_id=agent_id,
                    task_id=task.id,
                    title=(_it or "")[:200],
                    description=f"Aus Meeting: {room.name}",
                    status=TodoStatus.PENDING,
                    priority=3,
                    sort_order=_i,
                    project="workspace/general",
                ))
            created_tasks.append((agent_id, agent_name, agent_items, task.id))

        await db.commit()

        # Optional: mirror the action items into MS Planner (best-effort; gated by an
        # admin-set target plan + the meeting owner's connected M365 account).
        try:
            await _maybe_create_planner_tasks(room, items, db)
        except Exception:
            logger.warning("MS-Planner mirror failed for meeting %s", room.id, exc_info=True)

    # Publish assignment summary message to the meeting room
    if created_tasks:
        lines = []
        for agent_id, agent_name, items_list, task_id in created_tasks:
            lines.append(f"**{agent_name}** ({len(items_list)} Tasks):")
            lines.extend(f"  - {it}" for it in items_list)
        assignment_content = (
            "## Aufgaben zugewiesen\n"
            "Die Action Items wurden an die Agenten verteilt:\n\n"
            + "\n".join(lines)
        )
        assignment_msg = {
            "role": "system",
            "agent_id": None,
            "content": assignment_content,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        async with async_session_factory() as db:
            fresh_room = await db.scalar(select(MeetingRoom).where(MeetingRoom.id == room.id))
            if fresh_room:
                fresh_room.messages = list(fresh_room.messages or []) + [assignment_msg]
                await db.commit()

        await redis.client.publish(
            f"meeting:{room.id}:updates",
            _json.dumps(assignment_msg),
        )

        # Push tasks to each agent's queue so they start working
        for agent_id, agent_name, agent_items, task_id in created_tasks:
            # Look up the full task prompt from DB
            async with async_session_factory() as db2:
                from app.models.task import Task as _Task
                t = await db2.scalar(select(_Task).where(_Task.id == task_id))
                if t:
                    task_payload = _json.dumps({
                        "id": task_id,
                        "prompt": t.prompt,
                        "model": None,
                        "priority": t.priority,
                    })
                    await redis.push_task(agent_id, task_payload)
            logger.info(
                f"[Meeting {room.id}] Assigned {len(agent_items)} tasks to {agent_name} ({agent_id})"
            )

        logger.info(
            f"[Meeting {room.id}] Task assignment complete: {sum(len(i) for _, _, i, _ in created_tasks)} items → {len(created_tasks)} agents"
        )

    # Auto-create a ready-to-start follow-up meeting room (Folgetermin), seeded with
    # the prior context + the action items, so the loop continues additively.
    try:
        await _create_follow_up_room(room, todo_content, items, redis)
    except Exception:
        logger.warning("Follow-up room creation failed for meeting %s", room.id, exc_info=True)


async def _create_follow_up_room(room: MeetingRoom, todo_content: str, items: list[str], redis) -> str | None:
    """Create a ready-to-start follow-up meeting room (Folgetermin). Same agents +
    moderator setting, seeded with the prior meeting's context and the open action
    items. Created 'idle' — it appears in the room list, ready to start or schedule.
    Reuses the existing MeetingRoom machinery (no island)."""
    import json as _json
    import uuid as _uuid
    from app.db.session import async_session_factory

    # Stop the chain after MAX_FOLLOWUP_DEPTH so follow-ups don't beget follow-ups forever.
    if _followup_depth(room.name) >= MAX_FOLLOWUP_DEPTH:
        logger.info(
            f"[Meeting {room.id}] Follow-up chain reached depth {_followup_depth(room.name)} "
            f"(max {MAX_FOLLOWUP_DEPTH}) — not creating another follow-up."
        )
        return None

    from datetime import timedelta as _td
    context = _extract_followup_context(todo_content)
    new_name = _next_followup_name(room.name)
    # Event-based: the follow-up auto-starts when the parent meeting's action-item TODOs
    # are all completed. scheduled_for is ONLY the safety cap (start no later than +24h).
    cap = datetime.now(timezone.utc) + _td(hours=24)
    cap_str = cap.strftime("%Y-%m-%d %H:%M UTC")
    seed = (
        f"## Folgetermin zu: {room.name}\n\n"
        + f"**Start:** automatisch, sobald die Agenten alle Action Items des Vortermins erledigt haben (spätestens {cap_str}).\n\n"
        + (f"{context}\n\n" if context else "Fortsetzung des vorherigen Meetings.\n\n")
        + "**Action Items aus dem Vortermin (Stand prüfen / nächste Schritte planen):**\n"
        + ("\n".join(f"- {it}" for it in items) if items else "—")
    )
    new_id = _uuid.uuid4().hex[:12]
    async with async_session_factory() as db:
        new_room = MeetingRoom(
            id=new_id,
            name=new_name,
            topic=room.topic or "",
            agent_ids=list(room.agent_ids),
            state="idle",
            max_rounds=room.max_rounds,
            stages_config=room.stages_config,
            use_moderator=room.use_moderator,
            moderator_ai_account_id=room.moderator_ai_account_id,
            scheduled_for=cap,
            parent_room_id=room.id,
            created_by=room.created_by,
            messages=[{
                "role": "system",
                "agent_id": None,
                "content": seed,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }],
        )
        db.add(new_room)
        await db.commit()
    logger.info(f"[Meeting {room.id}] Follow-up room created: {new_id} ({new_name})")

    # Announce it in the original room so the UI shows the follow-up live.
    announce = {
        "role": "system",
        "agent_id": None,
        "content": f"## Folgetermin angelegt\nEin Folge-Meeting **{new_name}** wurde angelegt — es startet **automatisch, sobald alle Aufgaben erledigt sind** (spätestens {cap_str}). Raum-ID `{new_id}`.",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    try:
        async with async_session_factory() as db:
            fr = await db.scalar(select(MeetingRoom).where(MeetingRoom.id == room.id))
            if fr:
                fr.messages = list(fr.messages or []) + [announce]
                await db.commit()
        await redis.client.publish(f"meeting:{room.id}:updates", _json.dumps(announce))
    except Exception:
        pass
    return new_id


async def resume_running_rooms(redis, docker=None) -> None:
    """Called on orchestrator startup to resume rooms stuck in 'running' state."""
    from app.db.session import async_session_factory
    from app.config import settings as _settings

    async with async_session_factory() as db:
        result = await db.execute(
            select(MeetingRoom).where(
                MeetingRoom.state == "running",
                MeetingRoom.is_active == True,
            )
        )
        rooms = result.scalars().all()

    for room in rooms:
        if room.id not in _running_rooms:
            logger.info(f"Resuming meeting room {room.id} ({room.name}) after restart")
            mod_agent_id = None
            if room.use_moderator and docker:
                mod_agent_id = await _start_moderator_container(room.id, docker, _settings.redis_url_internal)
            task = asyncio.create_task(_run_meeting(room.id, redis, mod_agent_id=mod_agent_id, docker=docker))
            _running_rooms[room.id] = task

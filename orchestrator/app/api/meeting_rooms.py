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


class CreateRoom(BaseModel):
    name: str
    topic: str = ""
    agent_ids: list[str]
    max_rounds: int = 10


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
        "messages": room.messages or [],
        "created_at": room.created_at.isoformat() if room.created_at else None,
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

    # Verify all agents exist and are running
    for aid in body.agent_ids:
        agent = await db.scalar(select(Agent).where(Agent.id == aid))
        if not agent:
            raise HTTPException(status_code=400, detail=f"Agent {aid} not found")
        if agent.state not in (AgentState.RUNNING, AgentState.IDLE, AgentState.WORKING):
            raise HTTPException(status_code=400, detail=f"Agent {agent.name} is not running")

    room = MeetingRoom(
        id=uuid.uuid4().hex[:12],
        name=body.name,
        topic=body.topic,
        agent_ids=body.agent_ids,
        max_rounds=body.max_rounds,
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

    # Start round-robin in background
    redis = request.app.state.redis
    task = asyncio.create_task(_run_meeting(room_id, redis))
    _running_rooms[room_id] = task

    return {"status": "started", "room_id": room_id}


@router.post("/{room_id}/stop")
async def stop_room(room_id: str, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    room = await db.scalar(select(MeetingRoom).where(MeetingRoom.id == room_id))
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")

    if room_id in _running_rooms:
        _running_rooms[room_id].cancel()
        del _running_rooms[room_id]

    room.state = "paused"
    await db.commit()
    return {"status": "stopped"}


# ─── Round-Robin Engine ──────────────────────────────────────────


async def _run_meeting(room_id: str, redis) -> None:
    """Run the round-robin meeting loop.

    Each agent takes a turn, receives the conversation context,
    and their response is added to the message history.
    """
    from app.db.session import async_session_factory

    try:
        while True:
            async with async_session_factory() as db:
                room = await db.scalar(select(MeetingRoom).where(MeetingRoom.id == room_id))
                if not room or room.state != "running":
                    break

                # Check if we've reached max rounds
                if room.max_rounds > 0 and room.rounds_completed >= room.max_rounds:
                    room.state = "completed"
                    await db.commit()
                    logger.info(f"Meeting room {room_id} completed after {room.rounds_completed} rounds")
                    break

                agent_ids = room.agent_ids
                if not agent_ids:
                    break

                # Current agent's turn
                turn_idx = room.current_turn % len(agent_ids)
                current_agent_id = agent_ids[turn_idx]

                # Build context: last 20 messages as conversation
                messages = room.messages or []
                recent = messages[-20:]
                context_lines = []
                for msg in recent:
                    role = msg.get("agent_id") or "System"
                    context_lines.append(f"[{role}]: {msg['content']}")

                topic_line = f"Topic: {room.topic}\n" if room.topic else ""
                prompt = (
                    f"You are in a group meeting with other AI agents.\n"
                    f"{topic_line}"
                    f"Participants: {', '.join(agent_ids)}\n"
                    f"It is your turn to speak. Review the conversation and contribute.\n"
                    f"Keep your response focused and concise (max 300 words).\n"
                    f"Do NOT repeat what others said. Add new insights or move the discussion forward.\n\n"
                    f"Conversation so far:\n{''.join(context_lines) if context_lines else '(No messages yet — you start!)'}"
                )

                # Send message to the agent's queue
                import json
                message_payload = json.dumps({
                    "type": "meeting",
                    "room_id": room_id,
                    "prompt": prompt,
                })
                await redis.client.rpush(f"agent:{current_agent_id}:messages", message_payload)

                # Wait for response (poll agent's response queue)
                response_key = f"meeting:{room_id}:response:{current_agent_id}"
                response = None
                for _ in range(60):  # Wait up to 5 minutes (60 * 5s)
                    result = await redis.client.lpop(response_key)
                    if result:
                        response = result if isinstance(result, str) else result.decode()
                        break
                    await asyncio.sleep(5)

                if not response:
                    response = f"[{current_agent_id} did not respond in time]"

                # Add to messages
                new_msg = {
                    "role": "agent",
                    "agent_id": current_agent_id,
                    "content": response,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "round": room.rounds_completed,
                }
                updated_messages = list(room.messages or []) + [new_msg]
                room.messages = updated_messages

                # Advance turn
                next_turn = (turn_idx + 1) % len(agent_ids)
                room.current_turn = next_turn
                if next_turn == 0:
                    room.rounds_completed += 1

                await db.commit()

                # Publish update via Redis PubSub for live UI
                await redis.client.publish(
                    f"meeting:{room_id}:updates",
                    json.dumps(new_msg),
                )

                # Brief pause between turns
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

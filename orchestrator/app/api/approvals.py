"""API endpoints for command approval workflow.

When agents need to execute risky commands, they request approval from users.
Approvals are persisted in the DB (CommandApproval model) and survive restarts.
A Notification is created on each new request so the user sees it in the bell + Telegram.
"""

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies import require_agent_access, require_auth, verify_agent_token
from app.models.agent import Agent
from app.models.audit_log import AuditLog, AuditEventType
from app.models.command_approval import ApprovalStatus, CommandApproval
from app.models.notification import Notification
from app.services.redis_service import RedisService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/approvals", tags=["approvals"])


def _get_redis() -> RedisService | None:
    from app.api.ws import _redis
    return _redis


# ══════════════════════════════════════════════════════════════════════════════
# SCHEMAS
# ══════════════════════════════════════════════════════════════════════════════

class ApprovalRequest(BaseModel):
    tool: str
    input: dict
    reasoning: str
    risk_level: str  # "low", "medium", "high", "blocked"


class ApprovalDecision(BaseModel):
    decision: str  # "approve" or "deny"
    reason: str | None = None


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _approval_to_dict(a: CommandApproval) -> dict:
    return {
        "approval_id": str(a.id),
        "agent_id": a.agent_id,
        "tool": a.command,
        "reasoning": a.description,
        "risk_level": a.risk_level,
        "status": a.status,
        "meta": a.meta or {},
        "created_at": a.created_at.isoformat(),
        "resolved_at": a.resolved_at.isoformat() if a.resolved_at else None,
        "user_response": a.user_response,
    }


async def _publish_notification(redis: RedisService | None, notif: Notification) -> None:
    if not redis or not redis.client:
        return
    event = json.dumps({
        "type": "notification",
        "data": {
            "id": notif.id,
            "agent_id": notif.agent_id,
            "type": notif.type,
            "title": notif.title,
            "message": notif.message,
            "priority": notif.priority,
            "read": notif.read,
            "action_url": notif.action_url,
            "meta": notif.meta,
            "created_at": notif.created_at.isoformat(),
        },
    })
    await redis.client.publish("notifications:live", event)


# ══════════════════════════════════════════════════════════════════════════════
# AGENT ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/request")
async def request_approval(
    body: ApprovalRequest,
    agent_auth: dict = Depends(verify_agent_token),
    db: AsyncSession = Depends(get_db),
):
    agent_id = agent_auth["agent_id"]

    if body.risk_level == "blocked":
        logger.warning(f"Agent {agent_id} attempted blocked command: {body.tool}")
        raise HTTPException(status_code=403, detail="This command is forbidden and cannot be executed even with approval.")

    # Persist to DB
    approval = CommandApproval(
        agent_id=agent_id,
        command=body.tool,
        description=body.reasoning,
        risk_level=body.risk_level,
        status=ApprovalStatus.PENDING,
        meta={"input": body.input},
    )
    db.add(approval)
    await db.commit()
    await db.refresh(approval)

    # Create notification so user sees it in bell + Telegram
    notif = Notification(
        agent_id=agent_id,
        type="approval",
        title=f"Approval required: {body.tool}",
        message=body.reasoning,
        priority="high",
        action_url="/approvals",
        meta={"approval_id": approval.id, "risk_level": body.risk_level, "input": body.input},
    )
    db.add(notif)
    await db.commit()
    await db.refresh(notif)

    redis = _get_redis()
    await _publish_notification(redis, notif)

    # Also send Telegram for high/critical risk
    if body.risk_level in ("high", "critical"):
        try:
            from app.api.notifications import _send_telegram
            from app.api.notifications import NotificationCreate
            await _send_telegram(
                NotificationCreate(
                    agent_id=agent_id,
                    type="approval",
                    title=notif.title,
                    message=notif.message,
                    priority="high",
                    action_url="/approvals",
                ),
                redis,
                notif_id=notif.id,
            )
        except Exception as e:
            logger.warning(f"Telegram notification failed for approval {approval.id}: {e}")

    audit_entry = AuditLog(
        agent_id=agent_id,
        approval_id=str(approval.id),
        event_type=AuditEventType.APPROVAL_REQUESTED,
        command=body.tool,
        outcome="pending",
        meta={"risk_level": body.risk_level, "reasoning": body.reasoning, "input": body.input},
    )
    db.add(audit_entry)
    await db.commit()

    logger.info(f"Approval {approval.id} created for agent {agent_id} - {body.tool} (risk: {body.risk_level})")

    return {
        "approval_id": str(approval.id),
        "status": "pending",
        "message": "Approval request created. Waiting for user decision.",
    }


@router.get("/check/{approval_id}")
async def check_approval_status(
    approval_id: str,
    agent_auth: dict = Depends(verify_agent_token),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(CommandApproval).where(CommandApproval.id == int(approval_id)))
    approval = result.scalar_one_or_none()

    if not approval:
        raise HTTPException(status_code=404, detail="Approval request not found")
    if approval.agent_id != agent_auth["agent_id"]:
        raise HTTPException(status_code=403, detail="Not authorized for this request")

    return {
        "approval_id": str(approval.id),
        "status": approval.status,
        "user_response": approval.user_response,
        "resolved_at": approval.resolved_at.isoformat() if approval.resolved_at else None,
    }


# ══════════════════════════════════════════════════════════════════════════════
# USER ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/pending")
async def list_pending_approvals(
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    from app.models.user import UserRole

    query = select(CommandApproval).where(CommandApproval.status == ApprovalStatus.PENDING)

    if user.role not in (UserRole.ADMIN, UserRole.MANAGER):
        agent_result = await db.execute(select(Agent.id).where(Agent.user_id == user.id))
        allowed_ids = list(agent_result.scalars().all())
        query = query.where(CommandApproval.agent_id.in_(allowed_ids))

    query = query.order_by(CommandApproval.created_at.desc())
    result = await db.execute(query)
    approvals = result.scalars().all()

    return {"approvals": [_approval_to_dict(a) for a in approvals], "count": len(approvals)}


@router.get("/all")
async def list_all_approvals(
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    from app.models.user import UserRole
    query = select(CommandApproval).order_by(CommandApproval.created_at.desc()).limit(100)
    if user.role not in (UserRole.ADMIN, UserRole.MANAGER):
        agent_result = await db.execute(select(Agent.id).where(Agent.user_id == user.id))
        allowed_ids = list(agent_result.scalars().all())
        query = select(CommandApproval).where(CommandApproval.agent_id.in_(allowed_ids)).order_by(CommandApproval.created_at.desc()).limit(100)
    result = await db.execute(query)
    approvals = result.scalars().all()
    return {"approvals": [_approval_to_dict(a) for a in approvals], "count": len(approvals)}


@router.post("/{approval_id}/approve")
async def approve_request(
    approval_id: str,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(CommandApproval).where(CommandApproval.id == int(approval_id)))
    approval = result.scalar_one_or_none()

    if not approval:
        raise HTTPException(status_code=404, detail="Approval request not found")

    await require_agent_access(approval.agent_id, user, db)

    if approval.status != ApprovalStatus.PENDING:
        raise HTTPException(status_code=400, detail=f"Request already {approval.status}")

    approval.status = ApprovalStatus.APPROVED
    approval.resolved_at = datetime.now(timezone.utc)
    approval.user_response = f"Approved by {user.email}"

    audit_entry = AuditLog(
        agent_id=approval.agent_id,
        approval_id=approval_id,
        event_type=AuditEventType.COMMAND_APPROVED,
        command=f"{approval.command} {(approval.meta or {}).get('input', {})}",
        outcome="success",
        user_id=str(user.id),
        meta={"risk_level": approval.risk_level, "reasoning": approval.description},
    )
    db.add(audit_entry)
    await db.commit()

    # Notify agent via Redis so it can stop polling
    redis = _get_redis()
    if redis and redis.client:
        await redis.client.publish(
            f"approval:{approval.id}",
            json.dumps({"status": "approved", "approval_id": str(approval.id)}),
        )

    logger.info(f"Approval {approval.id} approved by user {user.id}")
    return {"approval_id": approval_id, "status": "approved", "message": "Command approved. Agent will proceed."}


@router.post("/{approval_id}/deny")
async def deny_request(
    approval_id: str,
    decision: ApprovalDecision,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(CommandApproval).where(CommandApproval.id == int(approval_id)))
    approval = result.scalar_one_or_none()

    if not approval:
        raise HTTPException(status_code=404, detail="Approval request not found")

    await require_agent_access(approval.agent_id, user, db)

    if approval.status != ApprovalStatus.PENDING:
        raise HTTPException(status_code=400, detail=f"Request already {approval.status}")

    approval.status = ApprovalStatus.DENIED
    approval.resolved_at = datetime.now(timezone.utc)
    approval.user_response = decision.reason or "Denied by user"

    audit_entry = AuditLog(
        agent_id=approval.agent_id,
        approval_id=approval_id,
        event_type=AuditEventType.COMMAND_DENIED,
        command=f"{approval.command} {(approval.meta or {}).get('input', {})}",
        outcome="blocked",
        user_id=str(user.id),
        meta={"risk_level": approval.risk_level, "deny_reason": decision.reason},
    )
    db.add(audit_entry)
    await db.commit()

    redis = _get_redis()
    if redis and redis.client:
        await redis.client.publish(
            f"approval:{approval.id}",
            json.dumps({"status": "denied", "approval_id": str(approval.id), "reason": decision.reason}),
        )

    logger.info(f"Approval {approval.id} denied by user {user.id}")
    return {"approval_id": approval_id, "status": "denied", "reason": decision.reason, "message": "Command denied."}


@router.delete("/{approval_id}")
async def cancel_approval_request(
    approval_id: str,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(CommandApproval).where(CommandApproval.id == int(approval_id)))
    approval = result.scalar_one_or_none()

    if not approval:
        raise HTTPException(status_code=404, detail="Approval request not found")

    approval.status = ApprovalStatus.DENIED
    approval.resolved_at = datetime.now(timezone.utc)
    approval.user_response = "Cancelled by user"
    await db.commit()

    return {"message": "Approval request cancelled"}

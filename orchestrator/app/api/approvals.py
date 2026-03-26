"""API endpoints for command approval workflow.

When agents need to execute risky commands, they request approval from users.
Users can approve or deny requests via WebSocket notifications + this API.
"""

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, WebSocket
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import select

from app.db.session import get_db
from app.dependencies import require_agent_access, require_auth, verify_agent_token
from app.models.agent import Agent
from app.models.audit_log import AuditLog, AuditEventType
from app.services.redis_service import RedisService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/approvals", tags=["approvals"])


# ══════════════════════════════════════════════════════════════════════════════
# SCHEMAS
# ══════════════════════════════════════════════════════════════════════════════

class ApprovalRequest(BaseModel):
    """Agent requests approval for a tool call."""
    tool: str
    input: dict
    reasoning: str
    risk_level: str  # "low", "medium", "high", "blocked"


class ApprovalDecision(BaseModel):
    """User approves or denies a request."""
    decision: str  # "approve" or "deny"
    reason: str | None = None  # Optional reason for denial


# ══════════════════════════════════════════════════════════════════════════════
# IN-MEMORY APPROVAL STORE (TODO: Move to Redis for production)
# ══════════════════════════════════════════════════════════════════════════════

# In-memory store for pending approvals
# Key: approval_id -> {agent_id, tool, input, reasoning, status, created_at, ...}
pending_approvals: dict[str, dict] = {}


# ══════════════════════════════════════════════════════════════════════════════
# AGENT ENDPOINTS (Request Approval)
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/request")
async def request_approval(
    body: ApprovalRequest,
    agent_auth: dict = Depends(verify_agent_token),
    db: AsyncSession = Depends(get_db),
):
    """
    Agent requests approval for a risky tool call.

    This endpoint is called by the agent when Claude wants to execute
    a command that requires user approval (medium/high risk).

    Flow:
    1. Agent calls this endpoint with tool details
    2. Approval request is stored and broadcast via WebSocket
    3. Agent polls /check/{approval_id} or waits for WebSocket callback
    4. User approves/denies via UI
    5. Agent receives decision and proceeds/aborts
    """
    agent_id = agent_auth["agent_id"]

    # Block if risk level is "blocked"
    if body.risk_level == "blocked":
        logger.warning(
            f"Agent {agent_id} attempted blocked command: {body.tool} - {body.input}"
        )
        raise HTTPException(
            status_code=403,
            detail="This command is forbidden and cannot be executed even with approval.",
        )

    # Create approval request
    approval_id = str(uuid.uuid4())
    approval_request = {
        "approval_id": approval_id,
        "agent_id": agent_id,
        "tool": body.tool,
        "input": body.input,
        "reasoning": body.reasoning,
        "risk_level": body.risk_level,
        "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    pending_approvals[approval_id] = approval_request

    # TODO: Broadcast via WebSocket to notify user
    # await notify_user_approval_needed(agent_id, approval_request)

    logger.info(
        f"Approval request created: {approval_id} for agent {agent_id} - "
        f"{body.tool} (risk: {body.risk_level})"
    )

    return {
        "approval_id": approval_id,
        "status": "pending",
        "message": "Approval request created. Waiting for user decision.",
    }


@router.get("/check/{approval_id}")
async def check_approval_status(
    approval_id: str,
    agent_auth: dict = Depends(verify_agent_token),
):
    """
    Agent polls this endpoint to check if approval was granted.

    Returns:
    - {"status": "pending"} - Still waiting
    - {"status": "approved", "approved_by": user_id, "approved_at": timestamp}
    - {"status": "denied", "denied_by": user_id, "reason": "..."}
    """
    if approval_id not in pending_approvals:
        raise HTTPException(status_code=404, detail="Approval request not found")

    request = pending_approvals[approval_id]
    agent_id = agent_auth["agent_id"]

    # Verify this agent owns the request
    if request["agent_id"] != agent_id:
        raise HTTPException(status_code=403, detail="Not authorized for this request")

    return {
        "approval_id": approval_id,
        "status": request["status"],
        "approved_by": request.get("approved_by"),
        "approved_at": request.get("approved_at"),
        "denied_by": request.get("denied_by"),
        "denied_at": request.get("denied_at"),
        "deny_reason": request.get("deny_reason"),
    }


# ══════════════════════════════════════════════════════════════════════════════
# USER ENDPOINTS (Approve/Deny)
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/pending")
async def list_pending_approvals(
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    List pending approval requests for current user's agents.

    Returns list of approval requests sorted by created_at (newest first).
    Admins/Managers see all; members only see approvals for their own agents.
    """
    from app.models.user import UserRole

    # Get agent IDs the user has access to
    if user.role in (UserRole.ADMIN, UserRole.MANAGER):
        # Admins/Managers see everything
        allowed_agent_ids = None
    else:
        # Members only see their own agents
        result = await db.execute(
            select(Agent.id).where(Agent.user_id == user.id)
        )
        allowed_agent_ids = set(result.scalars().all())

    pending = [
        req
        for req in pending_approvals.values()
        if req["status"] == "pending"
        and (allowed_agent_ids is None or req.get("agent_id") in allowed_agent_ids)
    ]

    # Sort by created_at (newest first)
    pending.sort(key=lambda x: x["created_at"], reverse=True)

    return {"approvals": pending, "count": len(pending)}


@router.post("/{approval_id}/approve")
async def approve_request(
    approval_id: str,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    User approves a pending tool call request.

    This allows the agent to proceed with executing the command.
    """
    if approval_id not in pending_approvals:
        raise HTTPException(status_code=404, detail="Approval request not found")

    request = pending_approvals[approval_id]

    # Verify user has access to this agent
    await require_agent_access(request["agent_id"], user, db)

    if request["status"] != "pending":
        raise HTTPException(
            status_code=400,
            detail=f"Request already {request['status']}",
        )

    # Update status
    request["status"] = "approved"
    request["approved_by"] = user.id
    request["approved_at"] = datetime.now(timezone.utc).isoformat()

    # Write audit log entry
    audit_entry = AuditLog(
        agent_id=request["agent_id"],
        approval_id=approval_id,
        event_type=AuditEventType.COMMAND_APPROVED,
        command=f"{request.get('tool')} {request.get('input', {})}",
        outcome="success",
        user_id=str(user.id),
        meta={"risk_level": request.get("risk_level"), "reasoning": request.get("reasoning")},
    )
    db.add(audit_entry)
    await db.commit()

    # TODO: Notify agent via WebSocket or Redis PubSub
    # await notify_agent_approval_decision(request["agent_id"], approval_id, "approved")

    logger.info(
        f"Approval {approval_id} approved by user {user.id} for agent {request['agent_id']}"
    )

    return {
        "approval_id": approval_id,
        "status": "approved",
        "message": "Command approved. Agent will proceed.",
    }


@router.post("/{approval_id}/deny")
async def deny_request(
    approval_id: str,
    decision: ApprovalDecision,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    User denies a pending tool call request.

    This prevents the agent from executing the command.
    Optionally include a reason for the denial.
    """
    if approval_id not in pending_approvals:
        raise HTTPException(status_code=404, detail="Approval request not found")

    request = pending_approvals[approval_id]

    # Verify user has access to this agent
    await require_agent_access(request["agent_id"], user, db)

    if request["status"] != "pending":
        raise HTTPException(
            status_code=400,
            detail=f"Request already {request['status']}",
        )

    # Update status
    request["status"] = "denied"
    request["denied_by"] = user.id
    request["denied_at"] = datetime.now(timezone.utc).isoformat()
    request["deny_reason"] = decision.reason or "No reason provided"

    # Write audit log entry
    audit_entry = AuditLog(
        agent_id=request["agent_id"],
        approval_id=approval_id,
        event_type=AuditEventType.COMMAND_DENIED,
        command=f"{request.get('tool')} {request.get('input', {})}",
        outcome="blocked",
        user_id=str(user.id),
        meta={
            "risk_level": request.get("risk_level"),
            "deny_reason": decision.reason,
            "reasoning": request.get("reasoning"),
        },
    )
    db.add(audit_entry)
    await db.commit()

    # TODO: Notify agent via WebSocket or Redis PubSub
    # await notify_agent_approval_decision(request["agent_id"], approval_id, "denied")

    logger.info(
        f"Approval {approval_id} denied by user {user.id} for agent {request['agent_id']} "
        f"- Reason: {decision.reason}"
    )

    return {
        "approval_id": approval_id,
        "status": "denied",
        "reason": decision.reason,
        "message": "Command denied. Agent will not proceed.",
    }


@router.delete("/{approval_id}")
async def cancel_approval_request(
    approval_id: str,
    user=Depends(require_auth),
):
    """
    Cancel/delete an approval request.

    This is treated as a denial - the agent will not proceed.
    """
    if approval_id not in pending_approvals:
        raise HTTPException(status_code=404, detail="Approval request not found")

    request = pending_approvals[approval_id]

    # Mark as denied (cancellation = denial)
    request["status"] = "denied"
    request["denied_by"] = user.id
    request["denied_at"] = datetime.now(timezone.utc).isoformat()
    request["deny_reason"] = "Cancelled by user"

    logger.info(f"Approval {approval_id} cancelled by user {user.id}")

    return {"message": "Approval request cancelled"}


# ══════════════════════════════════════════════════════════════════════════════
# ADMIN/DEBUG ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/all")
async def list_all_approvals(
    user=Depends(require_auth),
):
    """
    List ALL approval requests (for debugging).

    In production, this should be admin-only or removed.
    """
    # TODO: Require admin role

    return {
        "approvals": list(pending_approvals.values()),
        "count": len(pending_approvals),
    }


@router.delete("/cleanup")
async def cleanup_old_approvals(
    user=Depends(require_auth),
):
    """
    Clean up old/expired approval requests.

    Removes requests older than 24 hours or already decided.
    """
    # TODO: Require admin role

    now = datetime.now(timezone.utc)
    to_delete = []

    for approval_id, request in pending_approvals.items():
        created_at = datetime.fromisoformat(request["created_at"])
        age_hours = (now - created_at).total_seconds() / 3600

        # Delete if > 24 hours old or already decided
        if age_hours > 24 or request["status"] != "pending":
            to_delete.append(approval_id)

    for approval_id in to_delete:
        del pending_approvals[approval_id]

    logger.info(f"Cleaned up {len(to_delete)} old approval requests")

    return {
        "deleted": len(to_delete),
        "remaining": len(pending_approvals),
    }

"""Feedback API - users submit feedback, admins manage and create GitHub issues."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies import get_redis_service, require_admin, require_auth
from app.models.feedback import Feedback, FeedbackCategory, FeedbackStatus
from app.schemas.feedback import (
    FeedbackCreate,
    FeedbackListResponse,
    FeedbackResponse,
    FeedbackUpdate,
)
from app.services.oauth_service import OAuthService
from app.services.redis_service import RedisService

router = APIRouter(prefix="/feedback", tags=["feedback"])


def _to_response(f: Feedback) -> dict:
    return {
        "id": f.id,
        "user_id": f.user_id,
        "user_name": f.user_name,
        "title": f.title,
        "description": f.description,
        "category": f.category.value if isinstance(f.category, FeedbackCategory) else f.category,
        "status": f.status.value if isinstance(f.status, FeedbackStatus) else f.status,
        "admin_notes": f.admin_notes,
        "github_issue_url": f.github_issue_url,
        "created_at": f.created_at,
        "updated_at": f.updated_at,
    }


def _count_by_status(items: list[Feedback]) -> dict:
    counts = {"pending": 0, "reviewed": 0, "in_progress": 0, "closed": 0}
    for f in items:
        s = f.status.value if isinstance(f.status, FeedbackStatus) else f.status
        if s in counts:
            counts[s] += 1
    return counts


def _get_oauth_service(
    db: AsyncSession = Depends(get_db),
    redis: RedisService = Depends(get_redis_service),
) -> OAuthService:
    return OAuthService(db, redis)


# --- User endpoints ---


@router.post("/", response_model=FeedbackResponse)
async def create_feedback(
    body: FeedbackCreate,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Submit feedback (any authenticated user)."""
    feedback = Feedback(
        user_id=user.id,
        user_name=getattr(user, "display_name", None) or user.email,
        title=body.title,
        description=body.description,
        category=FeedbackCategory(body.category) if body.category else FeedbackCategory.GENERAL,
    )
    db.add(feedback)
    await db.commit()
    await db.refresh(feedback)
    return _to_response(feedback)


# --- Admin endpoints ---


@router.get("/", response_model=FeedbackListResponse)
async def list_feedback(
    status: str | None = Query(None),
    user=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """List all feedback (admin only)."""
    query = select(Feedback)
    if status:
        query = query.where(Feedback.status == FeedbackStatus(status))
    query = query.order_by(Feedback.created_at.desc())
    result = await db.execute(query)
    items = list(result.scalars().all())
    counts = _count_by_status(items)
    return {
        "feedback": [_to_response(f) for f in items],
        "total": len(items),
        **counts,
    }


@router.patch("/{feedback_id}", response_model=FeedbackResponse)
async def update_feedback(
    feedback_id: int,
    body: FeedbackUpdate,
    user=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Update feedback status or admin notes (admin only)."""
    result = await db.execute(select(Feedback).where(Feedback.id == feedback_id))
    feedback = result.scalar_one_or_none()
    if not feedback:
        raise HTTPException(status_code=404, detail="Feedback not found")

    if body.status is not None:
        feedback.status = FeedbackStatus(body.status)
    if body.admin_notes is not None:
        feedback.admin_notes = body.admin_notes

    await db.commit()
    await db.refresh(feedback)
    return _to_response(feedback)


@router.delete("/{feedback_id}")
async def delete_feedback(
    feedback_id: int,
    user=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Delete feedback (admin only)."""
    result = await db.execute(select(Feedback).where(Feedback.id == feedback_id))
    feedback = result.scalar_one_or_none()
    if not feedback:
        raise HTTPException(status_code=404, detail="Feedback not found")
    await db.delete(feedback)
    await db.commit()
    return {"deleted": feedback_id}


@router.post("/{feedback_id}/github-issue")
async def create_github_issue(
    feedback_id: int,
    user=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    service: OAuthService = Depends(_get_oauth_service),
):
    """Create a GitHub issue from feedback (admin only, requires GitHub integration)."""
    result = await db.execute(select(Feedback).where(Feedback.id == feedback_id))
    feedback = result.scalar_one_or_none()
    if not feedback:
        raise HTTPException(status_code=404, detail="Feedback not found")

    if feedback.github_issue_url:
        raise HTTPException(status_code=400, detail="GitHub issue already created")

    # Get GitHub token
    try:
        token = await service.get_valid_token("github")
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="GitHub integration not connected. Connect GitHub in Integrations first.",
        )

    # Determine repo from settings or use a default
    from app.config import settings as app_settings
    repo = getattr(app_settings, "github_repo", "") or "greeves89/AI-Employee"

    # Create issue via GitHub API
    import httpx

    category = feedback.category.value if isinstance(feedback.category, FeedbackCategory) else feedback.category
    body_md = f"**Category:** {category}\n**Submitted by:** {feedback.user_name or feedback.user_id}\n\n"
    if feedback.description:
        body_md += feedback.description
    else:
        body_md += "(No description provided)"
    body_md += "\n\n---\n*Created from AI Employee Platform Feedback*"

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"https://api.github.com/repos/{repo}/issues",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json={
                "title": f"[Feedback] {feedback.title}",
                "body": body_md,
                "labels": [f"feedback:{category}"],
            },
            timeout=15.0,
        )

    if response.status_code not in (200, 201):
        raise HTTPException(
            status_code=502,
            detail=f"GitHub API error: {response.status_code} - {response.text[:200]}",
        )

    data = response.json()
    feedback.github_issue_url = data["html_url"]
    feedback.status = FeedbackStatus.IN_PROGRESS
    await db.commit()
    await db.refresh(feedback)

    return {
        "issue_url": data["html_url"],
        "issue_number": data["number"],
        "feedback": _to_response(feedback),
    }

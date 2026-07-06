"""Central multi-tenant ownership helpers.

One place that answers "which agents may this user see?" so every read endpoint
(analytics, meeting rooms, cost attribution, …) scopes identically instead of each
router re-deriving it — and none of them accidentally returning another tenant's data.

Visibility rule for a non-admin: agents they OWN (``Agent.user_id == user.id``) plus
agents explicitly shared with them via ``AgentAccess``. Admins are unrestricted
(``visible_agent_ids`` returns ``None`` = no filter). Platform-shared agents
(``user_id IS NULL``) are intentionally NOT auto-included here: their cost/activity is
not the user's data, so they stay out of per-user analytics/meetings.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


def is_admin(user) -> bool:
    from app.models.user import UserRole
    return bool(getattr(user, "role", None) == UserRole.ADMIN)


async def visible_agent_ids(user, db: AsyncSession) -> set[str] | None:
    """Agent ids the user may see. ``None`` == admin/unrestricted (apply no filter).

    Non-admins get owned agents + agents shared via ``AgentAccess``. The returned set
    may be empty (a fresh user with no agents) — callers must treat an empty set as
    "show nothing", never as "show all".
    """
    if is_admin(user):
        return None
    from app.models.agent import Agent
    from app.models.agent_access import AgentAccess

    uid = str(getattr(user, "id", "") or "")
    owned = await db.scalars(select(Agent.id).where(Agent.user_id == uid))
    shared = await db.scalars(select(AgentAccess.agent_id).where(AgentAccess.user_id == uid))
    return {str(x) for x in owned.all()} | {str(x) for x in shared.all()}

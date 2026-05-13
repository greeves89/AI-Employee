"""Resolve effective permissions for a user (combining enum role + optional custom role).

Permissions dict shape:
{
  "max_agents": int | None,            # None = unlimited
  "template_ids": list[int] | None,    # None = all
  "llm_providers": list[str] | None,   # None = all
  "mount_labels": list[str] | None,    # None = inherit user_mount_access
  "url_host_patterns": list[str] | None,
  "menu_paths": list[str] | None       # None = all
}
"""
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.custom_role import CustomRole
from app.models.user import UserRole


DEFAULT_PERMISSIONS_BY_ROLE: dict[UserRole, dict[str, Any]] = {
    UserRole.ADMIN: {
        "max_agents": None,
        "template_ids": None,
        "llm_providers": None,
        "mount_labels": None,
        "url_host_patterns": None,
        "menu_paths": None,
    },
    UserRole.MANAGER: {
        "max_agents": 20,
        "template_ids": None,
        "llm_providers": None,
        "mount_labels": None,
        "url_host_patterns": None,
        "menu_paths": None,
    },
    UserRole.MEMBER: {
        "max_agents": 5,
        "template_ids": None,
        "llm_providers": None,
        "mount_labels": None,
        "url_host_patterns": None,
        "menu_paths": None,
    },
    UserRole.VIEWER: {
        "max_agents": 0,
        "template_ids": [],
        "llm_providers": [],
        "mount_labels": [],
        "url_host_patterns": [],
        "menu_paths": ["/dashboard", "/agents", "/tasks"],  # read-only views
    },
}


async def get_effective_permissions(user, db: AsyncSession) -> dict[str, Any]:
    """Return effective permissions for `user`.

    If user has a `custom_role_id` set, those win over the role enum defaults.
    Otherwise fall back to DEFAULT_PERMISSIONS_BY_ROLE.
    """
    # Admin enum always wins — admins are unrestricted regardless of custom role
    if hasattr(user, "role") and user.role == UserRole.ADMIN:
        return DEFAULT_PERMISSIONS_BY_ROLE[UserRole.ADMIN]

    custom_role_id = getattr(user, "custom_role_id", None)
    if custom_role_id:
        role = await db.get(CustomRole, custom_role_id)
        if role and role.permissions:
            return _merge_defaults(role.permissions)

    role_enum = getattr(user, "role", UserRole.MEMBER)
    if not isinstance(role_enum, UserRole):
        try:
            role_enum = UserRole(role_enum)
        except Exception:
            role_enum = UserRole.MEMBER
    return DEFAULT_PERMISSIONS_BY_ROLE[role_enum]


def _merge_defaults(p: dict[str, Any]) -> dict[str, Any]:
    """Ensure all permission keys are present (filling missing ones with None=unlimited)."""
    out = {
        "max_agents": None,
        "template_ids": None,
        "llm_providers": None,
        "mount_labels": None,
        "url_host_patterns": None,
        "menu_paths": None,
    }
    out.update(p or {})
    return out


def can_use_template(permissions: dict, template_id: int | None) -> bool:
    if template_id is None:
        return True
    allowed = permissions.get("template_ids")
    return allowed is None or template_id in allowed


def can_use_llm_provider(permissions: dict, provider_type: str | None) -> bool:
    if not provider_type:
        return True
    allowed = permissions.get("llm_providers")
    return allowed is None or provider_type in allowed


def can_access_menu(permissions: dict, path: str) -> bool:
    allowed = permissions.get("menu_paths")
    if allowed is None:
        return True
    return any(path == p or path.startswith(p.rstrip("/") + "/") for p in allowed)

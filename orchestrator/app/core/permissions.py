"""Resolve effective permissions for a user (combining enum role + optional custom role).

Permissions dict shape:
{
  "max_agents": int | None,            # None = unlimited
  "template_ids": list[int] | None,    # None = all
  "llm_providers": list[str] | None,   # None = all
  "models": list[str] | None,          # None = all; listed = only these model names may be picked for an agent (opt-in restriction per group)
  "mount_labels": list[str] | None,    # None = inherit user_mount_access; listed labels are GRANTED to the group (union with per-user grants)
  "ai_account_ids": list[int] | None,  # listed = the accounts this group may use. NOTE: for AI accounts the gate is DEFAULT-DENY (None = none) — see _allowed_account_ids
  "secret_ids": list[int] | None,      # listed = the secrets this group may use. NOTE: DEFAULT-DENY (None = none) — see secrets.py::_assert_secret_allowed
  "mcp_server_ids": list[int] | None,  # None = the servers made available to all (fuer_alle, #909); listed = exactly these — see core/mcp_zugriff.py
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
        "models": None,
        "mount_labels": None,
        "ai_account_ids": None,
        "secret_ids": None,
        "mcp_server_ids": None,
        "integration_providers": None,
        "url_host_patterns": None,
        "menu_paths": None,
        # Hoechste Autonomiestufe, die diese Rolle ihren Agenten geben darf
        # (None = unbegrenzt). Siehe app/core/autonomie_grenze.py.
        "max_autonomy_level": None,
    },
    UserRole.MANAGER: {
        "max_agents": 20,
        "template_ids": None,
        "llm_providers": None,
        "models": None,
        "mount_labels": None,
        "ai_account_ids": None,
        "secret_ids": None,
        "mcp_server_ids": None,
        "integration_providers": None,
        "url_host_patterns": None,
        "menu_paths": None,
        # Hoechste Autonomiestufe, die diese Rolle ihren Agenten geben darf
        # (None = unbegrenzt). Siehe app/core/autonomie_grenze.py.
        "max_autonomy_level": None,
    },
    UserRole.MEMBER: {
        "max_agents": 5,
        "template_ids": None,
        "llm_providers": None,
        "models": None,
        "mount_labels": None,
        "ai_account_ids": None,
        "secret_ids": None,
        "mcp_server_ids": None,
        "integration_providers": None,
        "url_host_patterns": None,
        "menu_paths": None,
        # Hoechste Autonomiestufe, die diese Rolle ihren Agenten geben darf
        # (None = unbegrenzt). Siehe app/core/autonomie_grenze.py.
        "max_autonomy_level": "l3",
    },
    UserRole.VIEWER: {
        "max_agents": 0,
        "template_ids": [],
        "llm_providers": [],
        "models": [],
        "mount_labels": [],
        "ai_account_ids": [],
        "secret_ids": [],
        "mcp_server_ids": [],
        "integration_providers": [],
        "url_host_patterns": [],
        "menu_paths": ["/dashboard", "/agents", "/tasks"],  # read-only views
        # Hoechste Autonomiestufe, die diese Rolle ihren Agenten geben darf
        # (None = unbegrenzt). Siehe app/core/autonomie_grenze.py.
        "max_autonomy_level": "l1",
    },
    # Ohne Zuweisung: nichts. Die Liste steht hier trotzdem vollstaendig da, statt
    # sich auf einen Sonderfall im Code zu verlassen — wer spaeter ein Recht
    # hinzufuegt, sieht sofort, dass es hier auf leer gehoert.
    #
    # Die eigentliche Sperre ist NICHT diese Liste (menu_paths werden nur von der
    # Seitenleiste gelesen, das ist Kosmetik), sondern der Riegel in
    # ``get_current_user``. Hier steht sie, damit beides dieselbe Aussage macht.
    UserRole.UNASSIGNED: {
        "max_agents": 0,
        "template_ids": [],
        "llm_providers": [],
        "models": [],
        "mount_labels": [],
        "ai_account_ids": [],
        "secret_ids": [],
        "mcp_server_ids": [],
        "integration_providers": [],
        "url_host_patterns": [],
        "menu_paths": [],
        # Hoechste Autonomiestufe, die diese Rolle ihren Agenten geben darf
        # (None = unbegrenzt). Siehe app/core/autonomie_grenze.py.
        "max_autonomy_level": "l1",
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

    role_enum = getattr(user, "role", UserRole.MEMBER)
    if not isinstance(role_enum, UserRole):
        try:
            role_enum = UserRole(role_enum)
        except Exception:
            role_enum = UserRole.MEMBER

    custom_role_id = getattr(user, "custom_role_id", None)
    if custom_role_id:
        role = await db.get(CustomRole, custom_role_id)
        if role and role.permissions:
            merged = _merge_defaults(role.permissions)
            # Die Autonomie-Grenze erbt eine eigene Rolle von ihrer Grundrolle,
            # solange sie keine eigene Angabe macht — sonst haette ein Mitglied mit
            # Sonderrolle WENIGER Schranken als eines ohne (None hiesse unbegrenzt).
            if "max_autonomy_level" not in role.permissions:
                merged["max_autonomy_level"] = DEFAULT_PERMISSIONS_BY_ROLE[role_enum].get("max_autonomy_level")
            return merged

    return DEFAULT_PERMISSIONS_BY_ROLE[role_enum]


def _merge_defaults(p: dict[str, Any]) -> dict[str, Any]:
    """Ensure all permission keys are present (filling missing ones with None=unlimited)."""
    out = {
        "max_agents": None,
        "template_ids": None,
        "llm_providers": None,
        "models": None,
        "mount_labels": None,
        "ai_account_ids": None,
        "secret_ids": None,
        "mcp_server_ids": None,
        "integration_providers": None,
        "url_host_patterns": None,
        "menu_paths": None,
    }
    out.update(p or {})
    return out


def can_use_integration(permissions: dict, provider: str | None) -> bool:
    """Whether the group may enable this integration provider (e.g. "microsoft",
    "exchange_onprem") on an agent. None = all allowed (opt-in restriction).
    Admin-safe: admins resolve to integration_providers=None via
    get_effective_permissions, so they are never restricted."""
    if not provider:
        return True
    allowed = permissions.get("integration_providers")
    return allowed is None or provider in allowed


def can_use_template(permissions: dict, template_id: int | None) -> bool:
    if template_id is None:
        return True
    allowed = permissions.get("template_ids")
    return allowed is None or template_id in allowed


# NOTE: AI-account authorization is DEFAULT-DENY and admin-aware — do NOT reintroduce a
# permissions-dict-only helper here (it cannot tell admin from member and would treat the
# default ai_account_ids=None as "all", re-opening cross-tenant access). Use
# ``app.api.ai_accounts._allowed_account_ids(user, db)`` at every AI-account gate instead.


# NOTE: Secret authorization is DEFAULT-DENY and admin-aware — do NOT reintroduce a
# permissions-dict-only helper (it can't tell admin from member and would treat the
# default secret_ids=None as "all", re-opening credential access). Gate via
# secrets.py::_assert_secret_allowed / list_secrets (admin bypass, None = none) instead.


def can_use_llm_provider(permissions: dict, provider_type: str | None) -> bool:
    if not provider_type:
        return True
    allowed = permissions.get("llm_providers")
    return allowed is None or provider_type in allowed


def can_use_model(permissions: dict, model: str | None) -> bool:
    """Whether the group may pick this model for an agent. None = all allowed
    (opt-in restriction). Admin-safe: admins resolve to models=None via
    get_effective_permissions, so they are never restricted."""
    if not model:
        return True
    allowed = permissions.get("models")
    return allowed is None or model in allowed


def can_access_menu(permissions: dict, path: str) -> bool:
    allowed = permissions.get("menu_paths")
    if allowed is None:
        return True
    return any(path == p or path.startswith(p.rstrip("/") + "/") for p in allowed)


def role_for_new_user(is_first: bool) -> UserRole:
    """Welche Rolle jemand bekommt, der sich selbst registriert.

    An EINER Stelle, weil es zwei Wege dorthin gibt (Passwort und SSO). Stuende die
    Regel zweimal da, koennte einer der beiden Wege spaeter davon abweichen — und
    zwar der, an den niemand denkt.

    Der allererste Nutzer wird IMMER Administrator: sonst waere eine frische
    Installation von der ersten Sekunde an ausgesperrt.
    """
    from app.config import settings as _settings

    if is_first:
        return UserRole.ADMIN
    wanted = str(getattr(_settings, "default_new_user_role", "unassigned") or "").strip().lower()
    try:
        role = UserRole(wanted)
    except ValueError:
        # Ein Tippfehler in der Einstellung darf nicht versehentlich Rechte
        # verteilen — im Zweifel nichts.
        return UserRole.UNASSIGNED
    # Administrator per Voreinstellung waere eine Selbstbedienungs-Falle.
    return UserRole.UNASSIGNED if role == UserRole.ADMIN else role


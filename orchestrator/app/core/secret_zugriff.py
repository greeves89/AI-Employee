"""Wer einen Key NUTZEN darf — an EINER Stelle.

Nutzen heisst: einem eigenen Agenten zuweisen, und der Agent bekommt ihn beim Start
als Umgebungsvariable. Erlaubt ist das

* einem **Admin** immer,
* dem **Besitzer** (``owner_id``) seines eigenen Keys,
* per **Rolle**, wenn ein Admin den Key in den Rollenrechten freigegeben hat
  (``secret_ids``; keine Liste = keiner),
* per **Person**, wenn der Besitzer oder ein Admin den Key an diese Person
  freigegeben hat (``agent_secret_shares``).

Ein Nutzer gibt nur an Personen frei, nie an Rollen — Rollen verwaltet der Admin.

Dieselbe Pruefung gilt beim Zuweisen (api/secrets.py) UND beim Einspielen in den
Agenten (agent_manager._get_secrets_env). Frueher prueften nur die Router; wurde
eine Freigabe entzogen, blieb der Key in bereits versorgten Agenten stecken.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_secret import AgentSecret, AgentSecretShare


def ist_admin(user) -> bool:
    from app.models.user import UserRole
    return getattr(user, "role", None) == UserRole.ADMIN


async def _rollen_ids(db: AsyncSession, user) -> set[int]:
    from app.core.permissions import get_effective_permissions
    erlaubt = (await get_effective_permissions(user, db)).get("secret_ids")
    return {int(x) for x in erlaubt} if erlaubt is not None else set()


async def personen_ids(db: AsyncSession, user_id: str) -> set[int]:
    zeilen = await db.execute(select(AgentSecretShare.secret_id).where(AgentSecretShare.user_id == user_id))
    return set(zeilen.scalars().all())


async def nutzbare_secret_ids(db: AsyncSession, user) -> set[int] | None:
    """Alle Keys, die ``user`` nutzen darf. ``None`` heisst: alle (Admin)."""
    if ist_admin(user):
        return None
    uid = str(getattr(user, "id", "") or "")
    eigene = set((await db.execute(select(AgentSecret.id).where(AgentSecret.owner_id == uid))).scalars().all())
    return eigene | await _rollen_ids(db, user) | await personen_ids(db, uid)


async def zugang(db: AsyncSession, user, secret: AgentSecret) -> str | None:
    """Warum ``user`` den Key nutzen darf: admin | eigen | rolle | person — oder None."""
    if ist_admin(user):
        return "admin"
    uid = str(getattr(user, "id", "") or "")
    if secret.owner_id and secret.owner_id == uid:
        return "eigen"
    if secret.id in await _rollen_ids(db, user):
        return "rolle"
    if secret.id in await personen_ids(db, uid):
        return "person"
    return None


#: Variablen, die ein FREMDER Key nie setzen darf: Sie steuern Laufzeit, Netzwerk oder
#: die Plattform selbst. Ein geteilter Key namens HTTPS_PROXY oder ANTHROPIC_BASE_URL
#: haette den Verkehr des Empfaenger-Agenten zum Teilenden umgeleitet (Sicherheits-
#: pruefung 03.10.2026). Eigene Keys und Firmen-Keys des Admins sind nicht betroffen.
_RESERVIERT = {
    "PATH", "HOME", "SHELL", "USER", "PWD", "TMPDIR",
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "NODE_OPTIONS", "NODE_EXTRA_CA_CERTS", "NODE_PATH", "PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "GIT_SSH_COMMAND",
    "ENV", "PROMPT_COMMAND", "PS4", "IFS",
}
_RESERVIERT_ANFANG = ("ANTHROPIC_", "CLAUDE_", "OPENAI_", "CODEX_", "LLM_", "AGENT_", "ORCHESTRATOR_",
                      "REDIS_", "LD_", "DYLD_", "DOCKER_", "MCP_", "AWS_", "AZURE_OPENAI_", "BEDROCK_", "VERTEX_",
                      "GOOGLE_", "GEMINI_", "HF_", "GIT_", "NPM_", "PIP_", "UV_", "YARN_", "BASH_")


def variable_reserviert(name: str) -> bool:
    n = (name or "").strip().upper()
    return n in _RESERVIERT or n.startswith(_RESERVIERT_ANFANG)

"""Zugriffs-Auflösung für App-Freigaben (#467) — EINE Quelle der Wahrheit.

Sowohl der App-Proxy (``docker_apps.proxy_app``) als auch die Apps-Übersicht
(``apps_overview``) fragen hier, ob jemand eine App sehen/öffnen darf. Die
Regeln stehen deshalb genau einmal hier und nicht doppelt in zwei Routern.

Grundsatz: **Default deny.** Ohne Besitz und ohne passende ``AppShare``-Zeile
ist Schluss. Freigaben öffnen nur den *Zugriffsweg* — das *Ziel* legen weiterhin
die SSRF-Gates im Proxy fest (Container muss zum ``agent-{id8}-``-Compose-Projekt
gehören). Steuernde Aktionen bleiben ausschließlich beim Besitzer.
"""

from __future__ import annotations

import hmac
import logging
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.app_share import AppShare, hash_share_token

logger = logging.getLogger(__name__)

#: Zugriffs-Ergebnisse, aufsteigend nach "wie privilegiert".
ACCESS_OWNER = "owner"
ACCESS_USER = "user"
ACCESS_AUTHENTICATED = "authenticated"
ACCESS_PUBLIC = "public"


def project_of_agent(agent_id: str) -> str:
    """Präfix, das alle Compose-Projekte dieses Agenten tragen."""
    return f"agent-{agent_id[:8]}-"


async def is_app_owner(agent_id: str, user, db: AsyncSession) -> bool:
    """Besitzt/verwaltet dieser Nutzer den Agenten hinter der App?

    Nutzt bewusst ``require_agent_access`` — dieselbe Prüfung wie überall sonst
    (Admin/Manager, Eigentümer, AgentAccess), damit hier keine zweite,
    abweichende Rechte-Logik entsteht.
    """
    if user is None:
        return False
    from app.dependencies import require_agent_access
    try:
        await require_agent_access(agent_id, user, db)
        return True
    except HTTPException:
        return False


async def _active_shares(project: str, db: AsyncSession) -> list[AppShare]:
    rows = (await db.execute(select(AppShare).where(AppShare.project == project))).scalars().all()
    now = datetime.now(timezone.utc)
    return [s for s in rows if not s.is_expired(now)]


async def agent_has_active_shares(agent_id: str, db: AsyncSession) -> bool:
    """Gibt es für diesen Agenten überhaupt eine gültige Freigabe?

    Billiges Vor-Gate: erlaubt es dem Proxy, einen Nicht-Besitzer abzuweisen,
    BEVOR er Docker nach einem Container fragt. Ohne das könnte ein Anonymer
    über 404/403-Unterschiede den Container-Namensraum abklopfen.
    """
    rows = (await db.execute(select(AppShare).where(AppShare.agent_id == agent_id))).scalars().all()
    now = datetime.now(timezone.utc)
    return any(not s.is_expired(now) for s in rows)


async def resolve_app_access(
    project: str,
    agent_id: str,
    user,
    token: str | None,
    db: AsyncSession,
) -> str:
    """Wie darf der Aufrufer auf diese App zugreifen? Wirft 401/403, wenn gar nicht.

    Reihenfolge: Besitzer → namentliche Freigabe → "alle Eingeloggten" →
    öffentlicher Link mit Token. 401 nur, wenn ein Login die Lage überhaupt
    ändern könnte (anonym, kein gültiger Token) — sonst 403.
    """
    if await is_app_owner(agent_id, user, db):
        return ACCESS_OWNER

    shares = await _active_shares(project, db)

    if user is not None:
        uid = str(getattr(user, "id", "") or "")
        for s in shares:
            if s.scope == ACCESS_USER and uid and str(s.user_id or "") == uid:
                return ACCESS_USER
        for s in shares:
            if s.scope == ACCESS_AUTHENTICATED:
                return ACCESS_AUTHENTICATED

    if token:
        # Konstantzeitiger Vergleich über den Hash — kein Byte-für-Byte-Abbruch, der
        # einem Angreifer verrät, wie weit er richtig geraten hat (CWE-208).
        probe = hash_share_token(token)
        for s in shares:
            if s.scope == ACCESS_PUBLIC and s.token_hash and hmac.compare_digest(s.token_hash, probe):
                return ACCESS_PUBLIC

    if user is None:
        # Anonym: ein Login kann helfen → 401, damit das Frontend zur Anmeldung
        # schickt statt eine Sackgasse anzuzeigen.
        raise HTTPException(status_code=401, detail="Not authenticated")
    raise HTTPException(status_code=403, detail="Diese App ist nicht für dich freigegeben.")


async def shared_projects_for_user(user, db: AsyncSession) -> dict[str, str]:
    """``{project: scope}`` aller Apps, die dieser eingeloggte Nutzer sehen darf,
    OHNE Besitzer zu sein. Basis für die Apps-Übersicht.

    Öffentliche Link-Freigaben tauchen hier bewusst NICHT auf: die sind an den
    Token gebunden, nicht an eine Person, und gehören niemandem in die Liste.
    """
    if user is None:
        return {}
    uid = str(getattr(user, "id", "") or "")
    rows = (await db.execute(
        select(AppShare).where(AppShare.scope.in_((ACCESS_USER, ACCESS_AUTHENTICATED)))
    )).scalars().all()
    now = datetime.now(timezone.utc)
    out: dict[str, str] = {}
    for s in rows:
        if s.is_expired(now):
            continue
        if s.scope == ACCESS_USER and (not uid or str(s.user_id or "") != uid):
            continue
        # Namentlich schlägt "alle Eingeloggten" — nur fürs Anzeigen relevant.
        if s.scope == ACCESS_USER or s.project not in out:
            out[s.project] = s.scope
    return out

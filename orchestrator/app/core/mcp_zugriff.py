"""Wer einen MCP-Server NUTZEN darf — an EINER Stelle.

Nutzen heisst: der Server haengt an den Agenten dieses Nutzers (Container UND
Sprachfront), er taucht in seiner Auswahl auf, und er darf ihn einem eigenen
Agenten zuweisen. Erlaubt ist das

* einem **Admin** immer,
* per **Rolle**, wenn die Rolle eine Liste fuehrt (``mcp_server_ids``) — dann
  genau diese Server,
* sonst nur den Servern, die ein Admin **allen Nutzern bereitgestellt** hat
  (``McpServer.fuer_alle``).

Frueher hiess „keine Liste" in der Rolle: alle Server. Ein per OAuth mit dem
Admin-Konto verbundener Server hing damit an den Agenten JEDES Nutzers — und jeder
dieser Agenten handelte dort als Admin (Markttest, #909). Bestehende Server sind
bei der Umstellung auf ``fuer_alle=true`` gesetzt worden (keine Verhaltensaenderung),
neue Server sind zunaechst nur fuer Admins da.

Dieselbe Pruefung gilt beim Auflisten (api/mcp_servers.py), beim Zuweisen
(api/agents.py), beim Einspielen in den Container und in der Sprachfront
(core/agent_mcp_servers.servers_for_agent). Muster: core/secret_zugriff.py.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.secret_zugriff import ist_admin
from app.models.mcp_server import McpServer


async def fuer_alle_ids(db: AsyncSession) -> set[int]:
    """Die Server, die ein Admin allen Nutzern bereitgestellt hat."""
    zeilen = await db.execute(select(McpServer.id).where(McpServer.fuer_alle.is_(True)))
    return set(zeilen.scalars().all())


async def nutzbare_mcp_server_ids(db: AsyncSession, user) -> set[int] | None:
    """Alle MCP-Server, die ``user`` nutzen darf. ``None`` heisst: alle (Admin)."""
    if ist_admin(user):
        return None
    from app.core.permissions import get_effective_permissions
    liste = (await get_effective_permissions(user, db)).get("mcp_server_ids")
    if liste is not None:
        return {int(x) for x in liste}
    return await fuer_alle_ids(db)


async def nutzbar_fuer_agent(db: AsyncSession, agent) -> set[int] | None:
    """Was am Agenten haengen darf: das, was sein Besitzer nutzen darf.

    Ein Agent ohne (auffindbaren) Besitzer zaehlt wie ein Nutzer ohne Rollenliste.
    """
    from app.models.user import User
    user_id = getattr(agent, "user_id", None)
    besitzer = await db.get(User, user_id) if user_id else None
    if besitzer is None:
        return await fuer_alle_ids(db)
    return await nutzbare_mcp_server_ids(db, besitzer)


async def agenten_betroffen_von_fuer_alle(db: AsyncSession, server: McpServer) -> list[str]:
    """Laufende Agenten, deren Server-Auswahl am Schalter ``fuer_alle`` haengt.

    Betroffen ist ein Agent, wenn sein Besitzer kein Admin ist und keine
    Rollenliste hat (sonst entscheidet die Liste bzw. nichts), wenn der Agent den
    Server nicht ausdruecklich abgewaehlt hat und der Server aktiv ist. Agenten
    ohne Besitzer zaehlen wie Nutzer ohne Rollenliste.
    """
    if not server.enabled:
        return []
    from app.core.permissions import get_effective_permissions
    from app.models.agent import Agent, AgentState
    from app.models.user import User

    agenten = (await db.execute(select(Agent).where(Agent.state == AgentState.RUNNING))).scalars().all()
    betroffen: list[str] = []
    unabhaengig: dict[str, bool] = {}
    for agent in agenten:
        auswahl = (agent.config or {}).get("mcp_servers")
        if auswahl is not None and server.id not in set(auswahl):
            continue
        if agent.user_id:
            if agent.user_id not in unabhaengig:
                besitzer = await db.get(User, agent.user_id)
                if besitzer is None:
                    unabhaengig[agent.user_id] = False  # wie ohne Besitzer
                elif ist_admin(besitzer):
                    unabhaengig[agent.user_id] = True  # Admin: unabhaengig vom Schalter
                else:
                    liste = (await get_effective_permissions(besitzer, db)).get("mcp_server_ids")
                    unabhaengig[agent.user_id] = liste is not None
            if unabhaengig[agent.user_id]:
                continue
        betroffen.append(agent.id)
    return betroffen

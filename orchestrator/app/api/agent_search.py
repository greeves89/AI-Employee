"""Agent-facing Websuche-API.

Der Agent-Container hat keine eigene Ahnung vom admin-konfigurierten Such-
Provider (Admin -> Websuche: DuckDuckGo/Brave/SerpApi) — er pflegte bis hier-
hin eine eigene, fest verdrahtete DuckDuckGo-Kopie. Dieser Endpoint ist
authentifiziert wie ``agent_apps`` (``verify_agent_token``, kein Scoping auf
eine Ressource noetig — Websuche ist nicht agent-spezifisch) und liest den
Provider zentral aus ``app.core.web_search.web_search_with_settings``, der
EINEN, gemeinsamen Weg fuer Sprachfront UND Agent-Container.

Daneben gibt es ``/news`` fuer den Nachrichtenindex. Das ist bewusst ein
EIGENES Werkzeug und kein Schalter an der Websuche: Beide liefern
Verschiedenes — der Nachrichtenindex nur Meldungen mit Datum, die Websuche
auch Dokumentation. Welches der beiden ein Agent braucht, haengt an seiner
Aufgabe, nicht an einer Betriebseinstellung.

Ob ein Agent den Nachrichtenindex ueberhaupt sieht, entscheidet der Admin
ueber die Berechtigung ``search_indexes`` seines Besitzers. Der Nutzer
konfiguriert nichts: Ist es freigegeben, meldet ``/capabilities`` es, und der
MCP-Server bindet das Werkzeug an — sonst taucht es gar nicht erst auf.
"""

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies import verify_agent_token

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/agent-search", tags=["agent-search"])


class AgentWebSearchRequest(BaseModel):
    query: str
    max_results: int = 5


@router.post("/web")
async def agent_web_search(
    body: AgentWebSearchRequest,
    auth: dict = Depends(verify_agent_token),
    db: AsyncSession = Depends(get_db),
):
    from app.core.web_search import web_search_with_settings

    results = await web_search_with_settings(body.query, body.max_results, db)
    return {"results": results}


async def _darf_index(agent_id: str, index: str, db: AsyncSession) -> bool:
    """Ob der BESITZER des Agenten diesen Suchindex nutzen darf.

    Die Berechtigung haengt am Menschen, nicht am Agenten — ein Agent ist nur
    das Werkzeug seines Besitzers und soll nicht mehr duerfen als dieser.
    """
    from sqlalchemy import select

    from app.core.permissions import can_use_search_index, get_effective_permissions
    from app.models.agent import Agent
    from app.models.user import User

    agent = (await db.execute(select(Agent).where(Agent.id == agent_id))).scalar_one_or_none()
    if agent is None:
        # Kein solcher Agent — es gibt niemanden, dessen Rechte gelten wuerden.
        return False
    if not agent.user_id:
        # Bewusst herrenlos (Plattform-Agent): Es gibt keinen Besitzer, dessen
        # Rechte man einschraenken koennte. Erlaubt, wie ueberall sonst auch,
        # wo keine Einschraenkung hinterlegt ist.
        return True
    user = (await db.execute(select(User).where(User.id == agent.user_id))).scalar_one_or_none()
    if user is None:
        # user_id gesetzt, aber der Nutzer existiert nicht mehr: ein haengender
        # Verweis, kein Entwurf. Hier NICHT durchlassen — sonst waere ein
        # geloeschter Nutzer der Weg zu mehr Rechten als der lebende hatte.
        logger.warning(
            "Agent %s verweist auf nicht vorhandenen Nutzer %s — Suchindex verweigert",
            agent_id, agent.user_id,
        )
        return False
    perms = await get_effective_permissions(user, db)
    return can_use_search_index(perms, index)


@router.get("/capabilities")
async def agent_search_capabilities(
    auth: dict = Depends(verify_agent_token),
    db: AsyncSession = Depends(get_db),
):
    """Welche Suchindizes dieser Agent nutzen darf.

    Der MCP-Server fragt das beim Start ab und bindet ``news_search`` nur an,
    wenn es hier ``true`` ist. So sieht ein Agent ohne Freigabe das Werkzeug
    gar nicht, statt es anzubieten und dann mit 403 abzuweisen.
    """
    from app.services.settings_service import SettingsService

    agent_id = auth.get("agent_id", "")
    hat_schluessel = bool(await SettingsService(db).get("web_search_api_key"))
    return {
        "web": True,
        "news": hat_schluessel and await _darf_index(agent_id, "news", db),
    }


@router.post("/news")
async def agent_news_search(
    body: AgentWebSearchRequest,
    auth: dict = Depends(verify_agent_token),
    db: AsyncSession = Depends(get_db),
):
    """Nachrichtensuche — Meldungen mit Datum und Herausgeber."""
    from fastapi import HTTPException

    from app.core.web_search import news_search_with_settings

    if not await _darf_index(auth.get("agent_id", ""), "news", db):
        raise HTTPException(status_code=403, detail="Nachrichtensuche ist fuer dich nicht freigegeben.")
    results = await news_search_with_settings(body.query, body.max_results, db)
    return {"results": results}

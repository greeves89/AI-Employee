"""Agent-facing Websuche-API.

Der Agent-Container hat keine eigene Ahnung vom admin-konfigurierten Such-
Provider (Admin -> Websuche: DuckDuckGo/Brave/SerpApi) — er pflegte bis hier-
hin eine eigene, fest verdrahtete DuckDuckGo-Kopie. Dieser Endpoint ist
authentifiziert wie ``agent_apps`` (``verify_agent_token``, kein Scoping auf
eine Ressource noetig — Websuche ist nicht agent-spezifisch) und liest den
Provider zentral aus ``app.core.web_search.web_search_with_settings``, der
EINEN, gemeinsamen Weg fuer Sprachfront UND Agent-Container.
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

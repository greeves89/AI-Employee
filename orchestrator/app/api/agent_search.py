"""Agent-facing Websuche-API.

Der Agent-Container hat keine eigene Ahnung vom admin-konfigurierten Such-
Provider (Admin -> Websuche: DuckDuckGo/Brave/SerpApi) — er pflegte bis hier-
hin eine eigene, fest verdrahtete DuckDuckGo-Kopie. Dieser Endpoint ist
authentifiziert wie ``agent_apps`` (``verify_agent_token``) und liest den
Provider zentral aus ``app.core.web_search`` — EINEN, gemeinsamen Weg fuer
Sprachfront UND Agent-Container.

Die Websuche IST inzwischen agent-spezifisch: Seit es neben der Websuche
einen Nachrichtenindex gibt (``brave_news``), ist die Providerwahl keine
Betriebs-, sondern eine Rolleneigenschaft. Der Nachrichtenindex liefert
ausschliesslich Meldungen — fuer einen Redaktions-Agenten richtig, fuer einen
Entwickler-Agenten, der Dokumentation sucht, unbrauchbar. Deshalb geht die
Agenten-ID mit in die Aufloesung, und der Aufrufer darf pro Anfrage per
``mode`` seine Absicht angeben.
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
    #: "news" | "web" — WONACH gesucht wird, nicht welcher Anbieter. Leer laesst
    #: die Einstellung des Agenten bzw. der Plattform unveraendert.
    mode: str | None = None


@router.post("/web")
async def agent_web_search(
    body: AgentWebSearchRequest,
    auth: dict = Depends(verify_agent_token),
    db: AsyncSession = Depends(get_db),
):
    from app.core.web_search import web_search_for_agent

    results = await web_search_for_agent(
        body.query, body.max_results, db, auth.get("agent_id"), body.mode,
    )
    return {"results": results}

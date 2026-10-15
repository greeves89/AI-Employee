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

from app.core.search_access import NEWS, agent_may_use_index
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
    from app.core.web_search import web_search_uses_news_index, web_search_with_settings

    # Ist brave_news eingestellt, ist die Websuche der Nachrichtenindex — dann
    # gilt dieselbe Freigabe wie fuer /news (Review zu #812, K2).
    allow_news = await web_search_uses_news_index(db) and await _darf_index(
        auth.get("agent_id", ""), NEWS, db)
    results = await web_search_with_settings(
        body.query, body.max_results, db, allow_news=allow_news,
    )
    return {"results": results}


async def _darf_index(agent_id: str, index: str, db: AsyncSession) -> bool:
    """Siehe ``app.core.search_access.agent_may_use_index`` — dieselbe
    Entscheidung fuer alle Zugaenge zum Index."""
    return await agent_may_use_index(agent_id, index, db)


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
    from app.core.web_search import brave_news_key
    from app.services.settings_service import SettingsService

    agent_id = auth.get("agent_id", "")
    # Dieselbe Aufloesung wie die Suche selbst: ein SerpApi-Schluessel macht
    # den Nachrichtenindex NICHT verfuegbar (Review zu #812, K1).
    hat_schluessel = bool(await brave_news_key(SettingsService(db)))
    return {
        "web": True,
        "news": hat_schluessel and await _darf_index(agent_id, NEWS, db),
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

    if not await _darf_index(auth.get("agent_id", ""), NEWS, db):
        raise HTTPException(status_code=403, detail="Nachrichtensuche ist fuer dich nicht freigegeben.")
    results = await news_search_with_settings(body.query, body.max_results, db)
    return {"results": results}

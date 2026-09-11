"""Compliance-API — semantische Gesetzes-Suche fuer die UI UND jeden Agenten.

Ein Endpunkt, zwei Aufrufer: der neue "Gesetze"-Reiter im Admin-Frontend, und
der neue ``gesetze_search``-Werkzeug-Aufruf jedes Agenten (Custom-LLM ueber
``OrchestratorAPIClient``, Claude Code/Codex ueber ``brain-server.mjs`` — beide
rufen denselben Weg mit ihrem eigenen Agenten-Token auf). Deshalb
``require_auth_or_agent`` statt eines reinen Nutzer-Logins.

Sucht ueber denselben pgvector-Semantik+Keyword-Pfad wie die Second-Brain-
Vaults (``vault_search.hybrid_search``), unter dem reservierten Brain-Label
``__gesetze_de__`` — siehe ``app.services.gesetz_crawler`` fuer die Indizierung.
"""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies import require_auth_or_agent
from app.services import gesetz_crawler, vault_search

router = APIRouter(prefix="/compliance", tags=["compliance"])


@router.get("/gesetze/search")
async def search_gesetze(
    q: str = Query(..., min_length=1),
    limit: int = Query(10, ge=1, le=50),
    user=Depends(require_auth_or_agent),
    db: AsyncSession = Depends(get_db),
):
    """Semantische Suche ueber die gecrawlten deutschen Bundesgesetze."""
    hits = await vault_search.hybrid_search(
        db, gesetz_crawler.BRAIN_LABEL, gesetz_crawler.HOST_PATH, q, limit
    )
    return {"query": q, "results": hits}


@router.get("/gesetze/status")
async def gesetze_status(request: Request, user=Depends(require_auth_or_agent)):
    """Stand des letzten Crawl-Laufs — fuer den Status-Hinweis im UI-Reiter."""
    crawler = getattr(request.app.state, "gesetz_crawler", None)
    return {
        "last_crawled_at": getattr(crawler, "last_crawled_at", None),
        "law_count": getattr(crawler, "law_count", 0),
    }

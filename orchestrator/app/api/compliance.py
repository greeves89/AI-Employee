"""Compliance-API — semantische Gesetzes-Suche fuer die UI UND jeden Agenten.

Ein Endpunkt, zwei Aufrufer: der neue "Gesetze"-Reiter im Admin-Frontend, und
der neue ``gesetze_search``-Werkzeug-Aufruf jedes Agenten (Custom-LLM ueber
``OrchestratorAPIClient``, Claude Code/Codex ueber ``brain-server.mjs`` — beide
rufen denselben Weg mit ihrem eigenen Agenten-Token auf). Deshalb
``require_auth_or_agent`` statt eines reinen Nutzer-Logins.

Sucht ueber denselben pgvector-Semantik+Keyword-Pfad wie die Second-Brain-
Vaults (``vault_search.hybrid_search``) — einmal je Brain-Label
(``__gesetze_de__``, ``__gesetze_eu__``), danach nach Score gemischt. Siehe
``app.services.gesetz_crawler`` fuer die Indizierung.
"""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import text as sa_text
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
    """Semantische Suche ueber Bundesrecht UND das kuratierte EU-Recht,
    gemeinsam nach Score sortiert."""
    de_hits, eu_hits = [
        await vault_search.hybrid_search(db, label, host_path, q, limit)
        for label, host_path in (
            (gesetz_crawler.BRAIN_LABEL, gesetz_crawler.HOST_PATH),
            (gesetz_crawler.EU_BRAIN_LABEL, gesetz_crawler.EU_HOST_PATH),
        )
    ]
    for h in de_hits:
        h["jurisdiction"] = "de"
    for h in eu_hits:
        h["jurisdiction"] = "eu"
    merged = sorted(de_hits + eu_hits, key=lambda h: h["score"], reverse=True)[:limit]
    return {"query": q, "results": merged}


@router.get("/gesetze/status")
async def gesetze_status(request: Request, user=Depends(require_auth_or_agent)):
    """Stand des letzten Crawl-Laufs je Quelle — fuer den Status-Hinweis im UI-Reiter."""
    crawler = getattr(request.app.state, "gesetz_crawler", None)
    return {
        "de": {
            "last_crawled_at": getattr(crawler, "last_crawled_at", None),
            "law_count": getattr(crawler, "law_count", 0),
        },
        "eu": {
            "last_crawled_at": getattr(crawler, "eu_last_crawled_at", None),
            "law_count": getattr(crawler, "eu_law_count", 0),
        },
    }


@router.get("/gesetze/list")
async def list_gesetze(
    jurisdiction: str = Query("eu", pattern="^(de|eu)$"),
    limit: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_auth_or_agent),
):
    """Durchklickbare Liste der indizierten Normen — eine Zeile je Datei, mit
    ihrer Ueberschrift (jurabk — Titel) aus dem ersten Textabschnitt. Fuer "eu"
    (die kuratierte, kleine Liste) ist das immer die vollstaendige Liste; fuer
    "de" (6130 Normen) ein nach Titel sortierter Ausschnitt — durchsuchen bleibt
    fuer die volle Bundesrechts-Menge der bessere Weg als endlos blaettern.
    """
    label = gesetz_crawler.EU_BRAIN_LABEL if jurisdiction == "eu" else gesetz_crawler.BRAIN_LABEL
    rows = (
        await db.execute(
            sa_text(
                "SELECT DISTINCT ON (path) path, heading "
                "FROM vault_chunks WHERE brain_label = :b AND chunk_idx = 0 "
                "ORDER BY path, chunk_idx ASC LIMIT :lim"
            ),
            {"b": label, "lim": limit},
        )
    ).all()
    items = [{"path": r.path, "title": r.heading or r.path} for r in rows]
    items.sort(key=lambda i: i["title"])
    return {"jurisdiction": jurisdiction, "items": items}

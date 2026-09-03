"""Web search — provider is admin-configurable (Admin -> Websuche).

Vorher gab es ZWEI unabhaengige DuckDuckGo-Implementierungen (hier und
``agent/app/tools/executor.py``), die trotz eines Kommentars "mirror sich"
tatsaechlich divergierten (POST vs. GET gegen den DDG-Endpunkt). Diese Datei
ist jetzt die EINZIGE Quelle: der Agent-Container ruft sie per HTTP auf
(``POST /api/v1/web-search``, siehe ``orchestrator/app/api/web_search.py``)
statt eine eigene Kopie zu pflegen.

Provider: ``duckduckgo`` (keyless, Standard) | ``brave`` | ``serp`` — Auswahl
+ API-Key liegen in den PlatformSettings (``web_search_provider``/
``web_search_api_key``, siehe ``settings_service.py``), nicht hier fest
verdrahtet.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import unquote

import httpx

logger = logging.getLogger(__name__)

_DDG_URL = "https://html.duckduckgo.com/html/"
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
_BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
_SERP_URL = "https://serpapi.com/search"


async def web_search(
    query: str, max_results: int = 5, provider: str = "duckduckgo", api_key: str | None = None,
) -> list[dict]:
    """Return up to ``max_results`` results as ``[{title, url, snippet}]``.

    Never raises — returns an empty list on any failure (jeder Aufrufer soll
    ehrlich "nichts gefunden" statt eines Stacktrace sehen). Ein Provider, der
    einen Key braucht aber keinen bekommen hat, faellt still auf DuckDuckGo
    zurueck statt komplett leer zu bleiben — das entspricht eher dem, was ein
    Admin erwartet, der den Key vergessen hat einzutragen.
    """
    query = (query or "").strip()
    if not query:
        return []
    max_results = max(1, min(int(max_results or 5), 10))

    provider = (provider or "duckduckgo").strip().lower()
    if provider == "brave" and api_key:
        return await _search_brave(query, max_results, api_key)
    if provider == "serp" and api_key:
        return await _search_serp(query, max_results, api_key)
    if provider in ("brave", "serp") and not api_key:
        logger.warning("web_search: provider=%s ohne API-Key konfiguriert, falle auf DuckDuckGo zurueck", provider)
    return await _search_duckduckgo(query, max_results)


async def _search_duckduckgo(query: str, max_results: int) -> list[dict]:
    try:
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=15, headers={"User-Agent": _UA}
        ) as client:
            # DDG's HTML endpoint only returns results for POST (form-encoded);
            # a GET yields a 202 landing page with no result markers.
            resp = await client.post(_DDG_URL, data={"q": query})
            resp.raise_for_status()
            html = resp.text
    except Exception:  # noqa: BLE001
        return []

    blocks = re.findall(
        r'class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?'
        r'class="result__snippet"[^>]*>(.*?)</(?:a|span)',
        html,
        re.DOTALL,
    )
    results: list[dict] = []
    for url, title, snippet in blocks[:max_results]:
        title = re.sub(r"<[^>]+>", "", title).strip()
        snippet = re.sub(r"<[^>]+>", "", snippet).strip()
        real_url = url
        if "uddg=" in url:
            m = re.search(r"uddg=([^&]+)", url)
            if m:
                real_url = unquote(m.group(1))
        results.append({"title": title, "url": real_url, "snippet": snippet})
    return results


async def _search_brave(query: str, max_results: int, api_key: str) -> list[dict]:
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                _BRAVE_URL,
                params={"q": query, "count": max_results},
                headers={"Accept": "application/json", "X-Subscription-Token": api_key},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception:  # noqa: BLE001
        logger.warning("Brave-Suche fehlgeschlagen", exc_info=True)
        return []
    items = (data.get("web") or {}).get("results") or []
    return [
        {"title": it.get("title", ""), "url": it.get("url", ""), "snippet": it.get("description", "")}
        for it in items[:max_results]
    ]


async def _search_serp(query: str, max_results: int, api_key: str) -> list[dict]:
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                _SERP_URL,
                params={"q": query, "engine": "google", "num": max_results, "api_key": api_key},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception:  # noqa: BLE001
        logger.warning("SerpApi-Suche fehlgeschlagen", exc_info=True)
        return []
    items = data.get("organic_results") or []
    return [
        {"title": it.get("title", ""), "url": it.get("link", ""), "snippet": it.get("snippet", "")}
        for it in items[:max_results]
    ]


async def web_search_with_settings(query: str, max_results: int, db) -> list[dict]:
    """Wie ``web_search``, liest Provider + Key aber selbst aus den
    PlatformSettings — der bequeme Weg fuer Aufrufer, die schon eine
    DB-Sitzung haben (Sprachfront, der neue HTTP-Endpunkt fuer den
    Agent-Container)."""
    from app.services.settings_service import SettingsService

    svc = SettingsService(db)
    provider = (await svc.get("web_search_provider")) or "duckduckgo"
    api_key = await svc.get("web_search_api_key")
    return await web_search(query, max_results, provider=provider, api_key=api_key)

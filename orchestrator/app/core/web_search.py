"""Keyless web search for the realtime voice interaction layer.

DuckDuckGo HTML endpoint — no API key, so it works on every deployment (Pi, SKBS)
out of the box. Mirrors the agent container's own ``web_search`` tool
(``agent/app/tools/executor.py``); the two runtimes are separate deployables and
cannot import each other, so the small DDG parser lives in both.
"""

from __future__ import annotations

import re
from urllib.parse import unquote

import httpx

_DDG_URL = "https://html.duckduckgo.com/html/"
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


async def web_search(query: str, max_results: int = 5) -> list[dict]:
    """Return up to ``max_results`` results as ``[{title, url, snippet}]``.

    Never raises — returns an empty list on any failure (the voice layer degrades
    gracefully to "nichts gefunden").
    """
    query = (query or "").strip()
    if not query:
        return []
    max_results = max(1, min(int(max_results or 5), 10))
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

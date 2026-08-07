"""Bildersuche — schluessellos, nach demselben Muster wie ``web_search``.

Der Agent nannte im Gespraech Bild-Adressen aus dem Gedaechtnis; die gab es nie (400/404),
und er meldete ein Problem beim Bildserver. Raten ist der falsche Weg: er soll den Begriff
eingeben und echte Treffer zurueckbekommen.

DuckDuckGo verlangt fuer die Bildersuche ein Sitzungs-Token (``vqd``), das nur in der
HTML-Seite steht — deshalb zwei Schritte: Token holen, dann die JSON-Schnittstelle fragen.
"""

import logging
import re
from urllib.parse import quote_plus

import httpx

logger = logging.getLogger(__name__)

_UA = (
    "AI-Employee/1.0 (self-hosted agent platform; "
    "+https://github.com/greeves89/AI-Employee)"
)
_VQD = re.compile(r'vqd=["\']?([\d-]+)["\']?')


async def image_search(query: str, max_results: int = 6) -> list[dict]:
    """``[{title, image_url, thumbnail, source_url, width, height}]`` — hoechstens
    ``max_results`` Treffer. Leere Liste, wenn nichts gefunden wurde oder die Suche
    gerade nicht erreichbar ist; der Aufrufer sagt das dann ehrlich."""
    q = (query or "").strip()
    if not q:
        return []
    headers = {"User-Agent": _UA, "Accept": "text/html,application/json"}
    try:
        async with httpx.AsyncClient(timeout=12.0, follow_redirects=True, headers=headers) as client:
            token_page = await client.get(f"https://duckduckgo.com/?q={quote_plus(q)}&iax=images&ia=images")
            match = _VQD.search(token_page.text)
            if not match:
                logger.warning("[ImageSearch] kein vqd-Token fuer %r", q[:60])
                return []
            resp = await client.get(
                "https://duckduckgo.com/i.js",
                params={"l": "de-de", "o": "json", "q": q, "vqd": match.group(1), "f": ",,,", "p": "1"},
                headers={**headers, "Referer": "https://duckduckgo.com/"},
            )
            data = resp.json()
    except Exception as e:  # noqa: BLE001 — Suche ist Beiwerk, kein Grund fuer einen Abbruch
        logger.warning("[ImageSearch] fehlgeschlagen fuer %r: %s", q[:60], e)
        return []

    out: list[dict] = []
    for item in (data.get("results") or [])[: max_results * 2]:
        url = str(item.get("image") or "").strip()
        if not url.startswith("https://"):
            continue                       # nur TLS — das Bild wird spaeter serverseitig geholt
        out.append({
            "title": str(item.get("title") or "")[:120],
            "image_url": url,
            "thumbnail": str(item.get("thumbnail") or ""),
            "source_url": str(item.get("url") or ""),
            "width": item.get("width"),
            "height": item.get("height"),
        })
        if len(out) >= max_results:
            break
    return out

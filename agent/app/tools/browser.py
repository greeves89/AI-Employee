"""Browser-Steuerung für Codex und Custom-LLM.

Claude Code bekommt sie über den Playwright-MCP, den ``main.py`` mit ``claude mcp add``
registriert. Genau da lag die Lücke: ``claude mcp add`` schreibt in die Konfiguration
der Claude-CLI, und die lesen die anderen beiden Laufzeiten nicht. Von drei Harnessen
konnte also nur einer im Browser arbeiten — und eine Fähigkeit gilt hier erst als
vorhanden, wenn sie überall vorhanden ist.

Ein Werkzeug mit ``action``-Parameter statt zwölf Einzelwerkzeugen, genau wie
``computer_use``: Der Werkzeugkatalog ist auf 128 Einträge begrenzt, und zwölf
Browser-Einträge hätten davon ein Zehntel verbraucht.

Abgegrenzt vom Desktop: ``computer_use`` steuert den Bildschirm des NUTZERS,
dieses hier einen Browser IM Container. Wer eine Seite im Namen des Nutzers bedienen
soll, nimmt weiterhin ``computer_use``.
"""

import asyncio
import logging

logger = logging.getLogger(__name__)

# Ein Browser je Agentenlauf, nicht je Aufruf: Chromium zu starten dauert ein bis zwei
# Sekunden, und ein Ablauf besteht fast immer aus mehreren Schritten auf derselben Seite.
_browser = None
_page = None
_lock = asyncio.Lock()

NAV_TIMEOUT_MS = 20_000
# Mehr als das liest kein Modell sinnvoll, und es fuellt nur das Kontextfenster.
MAX_TEXT_CHARS = 8_000

ACTIONS = (
    "navigate", "click", "type", "read_text", "read_links",
    "screenshot", "wait_for", "back", "close",
)


async def _ensure_page():
    """Browser und Seite bereitstellen (einmalig je Lauf)."""
    global _browser, _page
    if _page is not None and not _page.is_closed():
        return _page

    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    # Kopflos und ohne Sandbox: der Container laeuft ohnehin isoliert, und mit
    # Sandbox startet Chromium als root gar nicht erst.
    _browser = await pw.chromium.launch(
        headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"]
    )
    context = await _browser.new_context(viewport={"width": 1280, "height": 900})
    _page = await context.new_page()
    _page.set_default_timeout(NAV_TIMEOUT_MS)
    return _page


async def close_browser() -> None:
    """Am Ende eines Laufs aufräumen — ein offener Chromium hält Speicher fest."""
    global _browser, _page
    try:
        if _browser is not None:
            await _browser.close()
    except Exception:  # noqa: BLE001
        pass
    finally:
        _browser = None
        _page = None


async def run(params: dict) -> str:
    """Eine Browser-Aktion ausführen. Gibt immer Text zurück, nie eine Ausnahme.

    Ein Fehler wird BENANNT statt verschluckt: Sagt das Werkzeug nur „hat nicht
    geklappt", weicht das Modell auf ``bash`` und ``curl`` aus und holt sich HTML,
    das ohne JavaScript nichts enthält.
    """
    action = (params.get("action") or "").strip().lower()
    if action not in ACTIONS:
        return f"Error: unknown action '{action}'. Available: {', '.join(ACTIONS)}"

    if action == "close":
        await close_browser()
        return "Browser closed."

    async with _lock:
        try:
            page = await _ensure_page()
            return await _dispatch(page, action, params)
        except Exception as e:  # noqa: BLE001
            logger.warning("[Browser] %s fehlgeschlagen: %s", action, e)
            return f"Error: browser action '{action}' failed: {e}"


async def _dispatch(page, action: str, params: dict) -> str:
    if action == "navigate":
        url = (params.get("url") or "").strip()
        if not url:
            return "Error: 'url' is required for navigate."
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        await page.goto(url, wait_until="domcontentloaded")
        return f"Opened {page.url}\nTitle: {await page.title()}"

    if action == "click":
        selector = (params.get("selector") or "").strip()
        text = (params.get("text") or "").strip()
        if selector:
            await page.click(selector)
            return f"Clicked {selector}"
        if text:
            # Ueber den sichtbaren Text: ein Modell kennt die Beschriftung, nicht
            # den CSS-Pfad.
            await page.get_by_text(text, exact=False).first.click()
            return f"Clicked element containing '{text}'"
        return "Error: 'selector' or 'text' is required for click."

    if action == "type":
        selector = (params.get("selector") or "").strip()
        value = params.get("value") or ""
        if not selector:
            return "Error: 'selector' is required for type."
        await page.fill(selector, value)
        if params.get("submit"):
            await page.keyboard.press("Enter")
            return f"Typed into {selector} and pressed Enter."
        return f"Typed into {selector}."

    if action == "read_text":
        text = await page.inner_text("body")
        clean = "\n".join(line.strip() for line in text.splitlines() if line.strip())
        if len(clean) > MAX_TEXT_CHARS:
            clean = clean[:MAX_TEXT_CHARS] + "\n… (gekürzt)"
        return f"URL: {page.url}\n\n{clean}"

    if action == "read_links":
        links = await page.eval_on_selector_all(
            "a[href]",
            "els => els.slice(0, 100).map(e => ({text: e.innerText.trim(), href: e.href}))",
        )
        rows = [f"- {l['text'][:70] or '(ohne Text)'} → {l['href']}" for l in links if l.get("href")]
        return "\n".join(rows) or "No links found."

    if action == "screenshot":
        import base64

        raw = await page.screenshot(full_page=bool(params.get("full_page")))
        return ("Screenshot taken (base64 PNG):\n"
                + base64.b64encode(raw).decode()[:200_000])

    if action == "wait_for":
        selector = (params.get("selector") or "").strip()
        if not selector:
            return "Error: 'selector' is required for wait_for."
        await page.wait_for_selector(selector)
        return f"{selector} appeared."

    if action == "back":
        await page.go_back()
        return f"Back at {page.url}"

    return f"Error: action '{action}' not implemented."

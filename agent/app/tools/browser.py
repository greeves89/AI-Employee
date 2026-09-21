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
import os
import shutil

logger = logging.getLogger(__name__)

# Ein Browser je Agentenlauf, nicht je Aufruf: Chromium zu starten dauert ein bis zwei
# Sekunden, und ein Ablauf besteht fast immer aus mehreren Schritten auf derselben Seite.
_context = None
_pages: list = []
_aktiv = 0
_lock = asyncio.Lock()

NAV_TIMEOUT_MS = 20_000
# Mehr als das liest kein Modell sinnvoll, und es fuellt nur das Kontextfenster.
MAX_TEXT_CHARS = 8_000

#: Wo das Browserprofil liegt.
#:
#: Im Arbeitsbereich, nicht unter /tmp: Das Volume ueberlebt einen Neuaufbau des
#: Containers, /tmp nicht. Genau darum geht es — wer sich einmal anmeldet, soll
#: angemeldet bleiben, auch ueber ein Agenten-Update hinweg.
#:
#: ACHTUNG Speicherquote: Ein Chromium-Profil waechst mit Zwischenspeicher und
#: Sitzungsdaten. Es zaehlt auf das Kontingent des Arbeitsbereichs ein
#: (``services/disk_monitor.py``). Deshalb liegt der Zwischenspeicher bewusst
#: NICHT im Profil, sondern unter /tmp (siehe ``--disk-cache-dir``).
PROFIL_DIR = os.environ.get("BROWSER_PROFIL_DIR", "/workspace/.browser-profil")

#: Nur fuer die Entwicklung: sichtbarer Browser. Im Betrieb immer kopflos —
#: der Bildstrom fuer die Oberflaeche laeuft ueber CDP und braucht kein Fenster.
_KOPFLOS = os.environ.get("BROWSER_HEADLESS", "true").lower() != "false"

def _chromium_pfad() -> str | None:
    """Pfad zum Chromium des Abbilds -- oder None fuer Playwrights eigenes.

    Das Agenten-Abbild bringt das System-Chromium mit (``apt chromium``) und
    setzt ``PUPPETEER_EXECUTABLE_PATH``. Playwrights Python-Paket sucht
    dagegen ein selbst heruntergeladenes unter
    ``~/.cache/ms-playwright/...``, das dort nie ankommt -- schon gar nicht im
    Heimatverzeichnis des unprivilegierten Nutzers.

    Aufgefallen am 21.09.2026 beim ersten Livetest der Arbeitsflaeche: Der
    ganze Weg stand, und der Browser meldete
    ``Executable doesn't exist at .../chrome-headless-shell``. Daraus folgt
    mehr als ein Pfadfehler -- dieses Werkzeug kann im Container noch NIE
    gelaufen sein. Im Browser arbeiten konnte bis dahin nur Claude Code, weil
    der Playwright-MCP ein eigenes, mitgeliefertes Chromium benutzt.

    Lokal (Entwicklung, Tests) gibt es die Datei nicht; dann bleibt es beim
    mitgelieferten Browser, und nichts aendert sich.
    """
    # 1. Ausdrueckliche Vorgabe des Betreibers schlaegt alles.
    vorgabe = os.environ.get("BROWSER_EXECUTABLE")
    if vorgabe and os.path.exists(vorgabe):
        return vorgabe

    # 2. Liegen Playwrights EIGENE Browser bereit, nimm die. Sie passen zur
    #    Version des Python-Pakets; ein fremdes Chromium kann im
    #    Steuerprotokoll (CDP) abweichen. Seit dem gemeinsamen Ablageort im
    #    Abbild (PLAYWRIGHT_BROWSERS_PATH) ist das der Normalfall.
    eigene = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if eigene and os.path.isdir(eigene) and os.listdir(eigene):
        return None

    # 3. Rueckfall auf das Chromium des Abbilds.
    for kandidat in (os.environ.get("PUPPETEER_EXECUTABLE_PATH"), "/usr/bin/chromium"):
        if kandidat and os.path.exists(kandidat):
            return kandidat
    return None


#: Hoechstzahl gleichzeitiger Tabs. Jeder Tab ist ein eigener Renderer-Prozess;
#: auf kleinen Anlagen ist das die eigentliche Grenze, nicht die Logik.
MAX_TABS = int(os.environ.get("BROWSER_MAX_TABS", "8"))

ACTIONS = (
    "navigate", "click", "type", "read_text", "read_links",
    "screenshot", "wait_for", "back", "close",
    "new_tab", "list_tabs", "switch_tab", "close_tab",
)


def _profil_sperre_loesen() -> None:
    """Liegengebliebene Sperrdateien eines abgestuerzten Chromium entfernen.

    Ein persistentes Profil darf nur von EINEM Chromium benutzt werden; das
    sichert Chromium ueber ``SingletonLock`` ab. Wird der Container hart
    gestoppt — was hier regelmaessig passiert (Inaktivitaet, Speicherquote) —
    bleibt die Sperre liegen und der naechste Start scheitert dauerhaft an
    einem Profil, das gar niemand mehr benutzt.
    """
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        pfad = os.path.join(PROFIL_DIR, name)
        try:
            if os.path.islink(pfad) or os.path.exists(pfad):
                os.remove(pfad)
                logger.info("[Browser] Liegengebliebene Sperre entfernt: %s", name)
        except OSError:
            logger.debug("[Browser] Sperre %s nicht entfernbar", name, exc_info=True)


async def _ensure_context():
    """Browser-Kontext mit bestehenbleibendem Profil bereitstellen."""
    global _context
    if _context is not None:
        return _context

    from playwright.async_api import async_playwright

    os.makedirs(PROFIL_DIR, exist_ok=True)
    _profil_sperre_loesen()

    pw = await async_playwright().start()
    # Kopflos und ohne Sandbox: der Container laeuft ohnehin isoliert, und mit
    # Sandbox startet Chromium als root gar nicht erst.
    #
    # ``launch_persistent_context`` statt ``launch`` + ``new_context``: Nur so
    # landen Sitzungsmerkmale, Cookies und lokaler Speicher im Profil und
    # ueberleben den naechsten Lauf. Mit ``new_context`` ist nach jedem Lauf
    # alles wieder abgemeldet — der Grund, warum der Agent bisher keine
    # angemeldete Seite weiterbedienen konnte.
    argumente = [
        "--no-sandbox",
        "--disable-dev-shm-usage",
        # Zwischenspeicher aus dem Arbeitsbereich heraushalten, sonst frisst er
        # still das Speicherkontingent des Agenten.
        "--disk-cache-dir=/tmp/chromium-cache",
    ]
    pfad = _chromium_pfad()
    zusatz = {"executable_path": pfad} if pfad else {}
    try:
        _context = await pw.chromium.launch_persistent_context(
            PROFIL_DIR, headless=_KOPFLOS,
            viewport={"width": 1280, "height": 900}, args=argumente, **zusatz,
        )
    except Exception as e:  # noqa: BLE001
        # Zweiter Versuch mit frischem Profil: Ein beschaedigtes Profil darf den
        # Agenten nicht dauerhaft vom Browser aussperren. Die Anmeldungen sind
        # dann weg — besser als ein Werkzeug, das nie wieder startet.
        logger.warning("[Browser] Profil %s nicht benutzbar (%s) — wird neu angelegt",
                       PROFIL_DIR, e)
        shutil.rmtree(PROFIL_DIR, ignore_errors=True)
        os.makedirs(PROFIL_DIR, exist_ok=True)
        _context = await pw.chromium.launch_persistent_context(
            PROFIL_DIR, headless=_KOPFLOS,
            viewport={"width": 1280, "height": 900}, args=argumente, **zusatz,
        )

    _context.set_default_timeout(NAV_TIMEOUT_MS)
    return _context


async def _ensure_page():
    """Aktive Seite bereitstellen."""
    global _pages, _aktiv
    context = await _ensure_context()

    # Ein persistenter Kontext bringt bereits eine leere Seite mit.
    _pages = [p for p in context.pages if not p.is_closed()]
    if not _pages:
        _pages = [await context.new_page()]

    if _aktiv >= len(_pages):
        _aktiv = len(_pages) - 1
    return _pages[_aktiv]


async def close_browser() -> None:
    """Am Ende eines Laufs aufräumen — ein offener Chromium hält Speicher fest."""
    global _context, _pages, _aktiv
    try:
        if _context is not None:
            await _context.close()
    except Exception:  # noqa: BLE001
        pass
    finally:
        _context = None
        _pages = []
        _aktiv = 0


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


async def _tab_uebersicht() -> str:
    zeilen = []
    for i, pg in enumerate(_pages):
        try:
            titel = (await pg.title())[:60] or "(ohne Titel)"
        except Exception:  # noqa: BLE001 — eine Seite mitten im Laden hat noch keinen Titel
            titel = "(laedt)"
        marke = " <- aktiv" if i == _aktiv else ""
        zeilen.append(f"[{i}] {titel} - {pg.url}{marke}")
    return "\n".join(zeilen)


async def _dispatch(page, action: str, params: dict) -> str:
    global _pages, _aktiv

    if action == "list_tabs":
        return await _tab_uebersicht()

    if action == "new_tab":
        if len(_pages) >= MAX_TABS:
            return (f"Error: tab limit reached ({MAX_TABS}). Close a tab first "
                    f"(action 'close_tab').")
        context = await _ensure_context()
        neue = await context.new_page()
        neue.set_default_timeout(NAV_TIMEOUT_MS)
        _pages.append(neue)
        _aktiv = len(_pages) - 1
        url = (params.get("url") or "").strip()
        if url:
            if not url.startswith(("http://", "https://")):
                url = "https://" + url
            await neue.goto(url, wait_until="domcontentloaded")
        return f"Opened tab [{_aktiv}]." + (f" At {neue.url}" if url else "")

    if action == "switch_tab":
        i = params.get("index")
        if i is None:
            return "Error: 'index' is required for switch_tab. Use 'list_tabs' to see them."
        try:
            i = int(i)
        except (TypeError, ValueError):
            return f"Error: 'index' must be a number, got {i!r}."
        if not 0 <= i < len(_pages):
            return f"Error: no tab [{i}]. Open tabs:\n{await _tab_uebersicht()}"
        _aktiv = i
        await _pages[i].bring_to_front()
        return f"Switched to tab [{i}] - {_pages[i].url}"

    if action == "close_tab":
        i = params.get("index")
        i = _aktiv if i is None else int(i)
        if not 0 <= i < len(_pages):
            return f"Error: no tab [{i}]."
        if len(_pages) == 1:
            # Den letzten Tab zu schliessen wuerde den Kontext beenden und damit
            # die Anmeldung der laufenden Sitzung kappen. Stattdessen leeren.
            await _pages[0].goto("about:blank")
            return "Last tab kept and cleared (closing it would end the session)."
        await _pages[i].close()
        _pages.pop(i)
        _aktiv = min(_aktiv, len(_pages) - 1)
        return f"Closed tab [{i}]. Now active: [{_aktiv}] - {_pages[_aktiv].url}"

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

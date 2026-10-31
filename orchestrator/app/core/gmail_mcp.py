"""Gmail fuer Agenten — am Server, nicht im Agenten.

Bisher holte sich das E-Mail-Werkzeug im Container (``agent/mcp/email-server.mjs``)
das rohe Google- bzw. Microsoft-Token ueber ``/integrations/{provider}/for-agent``
und sprach Gmail und Graph direkt an. Damit lag ein Token mit vollem Postfachzugriff
im Agenten; mit Bash und curl sendete er Mails an jeder Regel vorbei, und der
Microsoft-Weg sendete sogar ohne curl direkt — am Nur-Lesen-Schalter, am Modus
„Entwurf“ und an der Mail-Freigabe vorbei.

Jetzt gilt: Microsoft laeuft ausschliesslich ueber ``/mcp/msgraph`` (mit allen
Schaltern), Gmail ueber diesen Server. Das Token verlaesst den Orchestrator nicht.
Senden richtet sich nach der Faehigkeit „E-Mail / M365 senden“ der Rechte-Matrix:

* ``allow`` — senden,
* ``ask`` — Entwurf im Gmail-Postfach des Besitzers anlegen; er sendet selbst,
* ``deny`` — ablehnen.

Werkzeugnamen und Parameter sind dieselben wie im alten Container-Werkzeug.
"""

from __future__ import annotations

import base64
import logging
import re
from collections.abc import Awaitable, Callable

import httpx

from app.core.log_redaction import scrub_log
from app.core.msgraph_mcp import MCP_VERSION, mcp_error, mcp_result, tool_result

logger = logging.getLogger(__name__)

GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
SENDEN = frozenset({"email_send", "email_reply"})
VERAENDERN = frozenset({"email_mark_read"})

GMAIL_TOOLS = [
    {
        "name": "email_list",
        "description": "List recent emails from the connected Gmail inbox. Returns sender, subject, date, and snippet.",
        "inputSchema": {"type": "object", "properties": {
            "max_results": {"type": "number", "description": "Max emails to return (default: 10, max: 50)."},
            "filter": {"type": "string", "description": "Filter: 'unread', 'starred', or a search query like 'from:boss@example.com'."},
        }},
    },
    {
        "name": "email_read",
        "description": "Read the full content of an email by its ID.",
        "inputSchema": {"type": "object", "properties": {
            "message_id": {"type": "string", "description": "The email ID from email_list."},
        }, "required": ["message_id"]},
    },
    {
        "name": "email_send",
        "description": "Send a new email. Depending on the agent's rights it is sent, or saved as a draft for the owner to send.",
        "inputSchema": {"type": "object", "properties": {
            "to": {"type": "string", "description": "Recipient email address."},
            "subject": {"type": "string", "description": "Email subject."},
            "body": {"type": "string", "description": "Plain text email body."},
        }, "required": ["to", "subject", "body"]},
    },
    {
        "name": "email_reply",
        "description": "Reply to an existing email thread. Depending on the agent's rights it is sent, or saved as a draft.",
        "inputSchema": {"type": "object", "properties": {
            "message_id": {"type": "string", "description": "The ID of the message to reply to."},
            "body": {"type": "string", "description": "Reply text."},
        }, "required": ["message_id", "body"]},
    },
    {
        "name": "email_search",
        "description": "Search emails with a Gmail query (e.g. 'invoice from:finance@example.com after:2024/01/01').",
        "inputSchema": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Search query."},
            "max_results": {"type": "number", "description": "Max results (default: 10)."},
        }, "required": ["query"]},
    },
    {
        "name": "email_mark_read",
        "description": "Mark an email as read.",
        "inputSchema": {"type": "object", "properties": {
            "message_id": {"type": "string", "description": "The email ID to mark as read."},
        }, "required": ["message_id"]},
    },
]

_PFLICHT = {t["name"]: t["inputSchema"].get("required", []) for t in GMAIL_TOOLS}
_KENNUNG = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


async def _gmail(methode: str, pfad: str, token: str, **kw) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.request(methode, f"{GMAIL}{pfad}", headers={"Authorization": f"Bearer {token}"}, **kw)
    if r.status_code >= 400:
        raise RuntimeError(f"Gmail API {r.status_code}: {r.text[:300]}")
    return r.json() if r.content else {}


def _kopf(kopfzeilen: list, name: str) -> str:
    return next((h.get("value", "") for h in kopfzeilen or [] if h.get("name", "").lower() == name.lower()), "")


def _text(payload: dict | None) -> str:
    if not payload:
        return ""
    daten = (payload.get("body") or {}).get("data")
    if daten:
        return base64.urlsafe_b64decode(daten + "==").decode("utf-8", "replace")
    teile = payload.get("parts") or []
    for mime in ("text/plain", "text/html"):
        for t in teile:
            if t.get("mimeType") == mime and (t.get("body") or {}).get("data"):
                return _text(t)
    for t in teile:
        innen = _text(t)
        if innen:
            return innen
    return ""


def _roh(an: str, betreff: str, text: str, antwort_auf: str | None = None) -> str:
    """RFC-822-Nachricht. Zeilenumbrueche in Kopfzeilen sind verboten — sonst setzte
    der Agent weitere Kopfzeilen (Bcc an beliebige Adressen)."""
    for wert in (an, betreff, antwort_auf or ""):
        if "\r" in wert or "\n" in wert:
            raise ValueError("Empfänger und Betreff dürfen keine Zeilenumbrüche enthalten.")
    kopf = [f"To: {an}", f"Subject: {betreff}", "MIME-Version: 1.0", "Content-Type: text/plain; charset=utf-8"]
    if antwort_auf:
        kopf += [f"In-Reply-To: {antwort_auf}", f"References: {antwort_auf}"]
    return base64.urlsafe_b64encode(("\r\n".join(kopf) + "\r\n\r\n" + text).encode()).decode()


def _id(args: dict) -> str:
    kennung = str(args.get("message_id") or "")
    if not _KENNUNG.match(kennung):
        raise ValueError("Ungültige message_id.")
    return kennung


async def _werkzeug(name: str, args: dict, token: str, senden: str) -> str:
    grenze = min(int(args.get("max_results") or 10), 50)

    if name in ("email_list", "email_search"):
        q = args.get("query") or args.get("filter") or ""
        q = {"unread": "is:unread", "starred": "is:starred"}.get(q, q)
        params = {"maxResults": grenze, **({"q": q} if q else {})}
        liste = (await _gmail("GET", "/messages", token, params=params)).get("messages") or []
        if not liste:
            return "No emails found."
        zeilen = []
        for m in liste[:grenze]:
            d = await _gmail("GET", f"/messages/{m['id']}", token, params=[
                ("format", "metadata"), ("metadataHeaders", "From"),
                ("metadataHeaders", "Subject"), ("metadataHeaders", "Date")])
            k = (d.get("payload") or {}).get("headers") or []
            zeilen.append(f"ID: {d.get('id')} | From: {_kopf(k, 'From')} | Subject: {_kopf(k, 'Subject')} | "
                          f"Date: {_kopf(k, 'Date')}\nSnippet: {d.get('snippet', '')}")
        return f"{len(zeilen)} emails:\n\n" + "\n\n---\n\n".join(zeilen)

    if name == "email_read":
        d = await _gmail("GET", f"/messages/{_id(args)}", token, params={"format": "full"})
        k = (d.get("payload") or {}).get("headers") or []
        return (f"From: {_kopf(k, 'From')}\nTo: {_kopf(k, 'To')}\nSubject: {_kopf(k, 'Subject')}\n"
                f"Date: {_kopf(k, 'Date')}\n\n{_text(d.get('payload'))}")

    if name == "email_mark_read":
        await _gmail("POST", f"/messages/{_id(args)}/modify", token, json={"removeLabelIds": ["UNREAD"]})
        return f"Email {args['message_id']} marked as read."

    # Senden / Antworten
    thread = None
    if name == "email_reply":
        orig = await _gmail("GET", f"/messages/{_id(args)}", token, params=[
            ("format", "metadata"), ("metadataHeaders", "Subject"),
            ("metadataHeaders", "From"), ("metadataHeaders", "Message-ID")])
        k = (orig.get("payload") or {}).get("headers") or []
        betreff = _kopf(k, "Subject")
        an, betreff = _kopf(k, "From"), betreff if betreff.startswith("Re:") else f"Re: {betreff}"
        roh, thread = _roh(an, betreff, str(args["body"]), _kopf(k, "Message-ID")), orig.get("threadId")
    else:
        an, betreff = str(args["to"]), str(args["subject"])
        roh = _roh(an, betreff, str(args["body"]))
    nachricht = {"raw": roh, **({"threadId": thread} if thread else {})}

    if senden == "ask":
        d = await _gmail("POST", "/drafts", token, json={"message": nachricht})
        return (f"NICHT gesendet: Entwurf an {an} („{betreff}“) liegt im Gmail-Postfach "
                f"(Entwurfs-ID: {d.get('id', '?')}). Senden braucht die Freigabe des Besitzers — "
                "sag ihm, dass der Entwurf bereitliegt.")
    await _gmail("POST", "/messages/send", token, json=nachricht)
    return f"Email sent to {an}." if name == "email_send" else "Reply sent."


async def handle_mcp_request(body: dict, resolve_token: Callable[[], Awaitable[str | None]], *,
                             senden: str) -> tuple[dict, int]:
    """Eine MCP-Nachricht. ``senden`` ist der Matrix-Zustand von ``email_m365``."""
    methode = body.get("method", "")
    id_ = body.get("id")

    if methode == "initialize":
        return mcp_result(id_, {"protocolVersion": MCP_VERSION, "capabilities": {"tools": {}},
                                "serverInfo": {"name": "mcp-gmail", "version": "1.0.0"}}), 200
    if methode == "notifications/initialized":
        return {}, 200
    if methode == "ping":
        return mcp_result(id_, {}), 200
    if methode == "tools/list":
        verborgen = (SENDEN | VERAENDERN) if senden == "deny" else frozenset()
        return mcp_result(id_, {"tools": [t for t in GMAIL_TOOLS if t["name"] not in verborgen]}), 200
    if methode != "tools/call":
        return mcp_error(id_, -32601, f"Method not found: {methode}"), 404

    params = body.get("params") or {}
    name, args = params.get("name", ""), params.get("arguments") or {}
    if name not in _PFLICHT:
        return mcp_result(id_, tool_result(f"Unknown tool: {name}", is_error=True)), 200
    if senden == "deny" and name in SENDEN | VERAENDERN:
        return mcp_result(id_, tool_result(
            "Dieser Agent darf laut seiner Rechte-Matrix keine E-Mails senden oder verändern "
            "(„E-Mail / M365 senden“ steht auf Verboten).", is_error=True)), 200
    fehlt = [f for f in _PFLICHT[name] if args.get(f) in (None, "")]
    if fehlt:
        return mcp_result(id_, tool_result(f"Error: missing required argument(s): {', '.join(fehlt)}.", is_error=True)), 200
    try:
        token = await resolve_token()
    except Exception as e:  # noqa: BLE001 — Erneuerung kann an Google scheitern
        logger.warning("Gmail-Token nicht verfügbar: %s", scrub_log(e))
        token = None
    if not token:
        return mcp_result(id_, tool_result(
            "Google account not connected. Ask the user to connect Google in Settings → Integrations.",
            is_error=True)), 200
    try:
        return mcp_result(id_, tool_result(await _werkzeug(name, args, token, senden))), 200
    except (ValueError, RuntimeError) as e:
        return mcp_result(id_, tool_result(f"Error: {e}", is_error=True)), 200
    except Exception as e:
        logger.exception("Gmail-Werkzeug %s fehlgeschlagen", scrub_log(name))
        return mcp_result(id_, tool_result(f"Error: {e}", is_error=True)), 200

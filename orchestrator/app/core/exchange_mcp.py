"""Shared on-prem Exchange (EWS) MCP core — tool definitions, EWS calls, dispatch.

Counterpart to ``app.core.msgraph_mcp`` but for an **on-prem Exchange** server
(Exchange Web Services), NOT Microsoft 365 / Graph. Used by:
  - app/api/mcp_exchange.py — per-AGENT transport (agent HMAC token; the mailbox
    is the agent OWNER's, resolved by the owner's email).

Per-user by design: every call acts on **one specific user's mailbox** — the agent
owner's — via EWS impersonation keyed on that user's primary SMTP address. Three
admin-selectable auth modes (``exchange_auth_mode``):
  - ``service_account``: a service account (Basic/NTLM) with the
    ApplicationImpersonation RBAC role impersonates the user (no per-user secret).
  - ``modern_auth``: app-only OAuth2 (same Entra app as SSO) + impersonation.
  - ``basic``: the user's own Exchange credentials (delegate access; per-user
    secret stored encrypted in OAuthIntegration).

exchangelib is synchronous; all blocking EWS work runs in ``asyncio.to_thread``.
The library is imported lazily inside ``_build_account`` so this module (tool
catalog, gating, dispatch) imports and is testable without exchangelib installed.
"""

import asyncio
import json
import logging
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

MCP_VERSION = "2025-06-18"

# Well-known folders we allow as move targets / list sources (never interpolate
# arbitrary folder names — same hardening idea as the Graph MCP).
_ALLOWED_FOLDERS = {"inbox", "sentitems", "drafts", "deleteditems", "junkemail", "outbox"}


def _folder(value) -> str:
    v = (str(value) if value else "inbox").strip().lower().replace(" ", "")
    return v if v in _ALLOWED_FOLDERS else "inbox"


# ---------------------------------------------------------------------------
# Tool definitions (mail + calendar; shapes mirror the Graph MCP for a
# consistent agent experience)
# ---------------------------------------------------------------------------

EXCHANGE_TOOLS = [
    {
        "name": "ex_whoami",
        "description": "Show which on-prem Exchange mailbox this agent is connected to (connection/auth test).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "ex_list_emails",
        "description": "List emails from a mailbox folder (newest first).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "folder": {"type": "string", "description": "inbox (default), sentitems, drafts, deleteditems, junkemail."},
                "limit": {"type": "number", "description": "Max emails. Default 15, max 50."},
            },
        },
    },
    {
        "name": "ex_read_email",
        "description": "Read the full body of one email by its ID (from ex_list_emails).",
        "inputSchema": {
            "type": "object",
            "properties": {"email_id": {"type": "string", "description": "Email ID from ex_list_emails."}},
            "required": ["email_id"],
        },
    },
    {
        "name": "ex_send_email",
        "description": "Send a new email.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient address(es), comma-separated."},
                "subject": {"type": "string", "description": "Subject."},
                "body": {"type": "string", "description": "Plain-text body."},
                "cc": {"type": "string", "description": "Optional CC address(es), comma-separated."},
            },
            "required": ["to", "subject", "body"],
        },
    },
    {
        "name": "ex_reply_email",
        "description": "Reply to an email.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "email_id": {"type": "string", "description": "Email ID from ex_list_emails."},
                "body": {"type": "string", "description": "Reply text."},
                "reply_all": {"type": "boolean", "description": "Reply to all recipients (default false)."},
            },
            "required": ["email_id", "body"],
        },
    },
    {
        "name": "ex_forward_email",
        "description": "Forward an email to one or more recipients.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "email_id": {"type": "string", "description": "Email ID from ex_list_emails."},
                "to": {"type": "string", "description": "Recipient address(es), comma-separated."},
                "comment": {"type": "string", "description": "Optional comment above the forwarded message."},
            },
            "required": ["email_id", "to"],
        },
    },
    {
        "name": "ex_delete_email",
        "description": "Delete an email (moves it to Deleted Items).",
        "inputSchema": {
            "type": "object",
            "properties": {"email_id": {"type": "string", "description": "Email ID from ex_list_emails."}},
            "required": ["email_id"],
        },
    },
    {
        "name": "ex_move_email",
        "description": "Move an email to another folder (inbox, archive=deleteditems, junkemail, drafts, sentitems).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "email_id": {"type": "string", "description": "Email ID from ex_list_emails."},
                "folder": {"type": "string", "description": "Destination: inbox, deleteditems, junkemail, drafts, sentitems."},
            },
            "required": ["email_id", "folder"],
        },
    },
    {
        "name": "ex_mark_email_read",
        "description": "Mark an email as read or unread.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "email_id": {"type": "string", "description": "Email ID from ex_list_emails."},
                "read": {"type": "boolean", "description": "true = read (default), false = unread."},
            },
            "required": ["email_id"],
        },
    },
    {
        "name": "ex_list_calendar_events",
        "description": "List upcoming calendar events for the next N days.",
        "inputSchema": {
            "type": "object",
            "properties": {"days": {"type": "number", "description": "Look-ahead window in days. Default 7, max 31."}},
        },
    },
    {
        "name": "ex_create_calendar_event",
        "description": "Create a calendar event / meeting.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "Event title."},
                "start": {"type": "string", "description": "Start, ISO 8601 (e.g. 2026-07-01T09:00:00)."},
                "end": {"type": "string", "description": "End, ISO 8601."},
                "attendees": {"type": "string", "description": "Optional required attendees, comma-separated emails."},
                "location": {"type": "string", "description": "Optional location."},
                "body": {"type": "string", "description": "Optional description."},
            },
            "required": ["subject", "start", "end"],
        },
    },
    {
        "name": "ex_update_calendar_event",
        "description": "Update/reschedule a calendar event.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string", "description": "Event ID from ex_list_calendar_events."},
                "subject": {"type": "string", "description": "Optional new title."},
                "start": {"type": "string", "description": "Optional new start, ISO 8601."},
                "end": {"type": "string", "description": "Optional new end, ISO 8601."},
                "location": {"type": "string", "description": "Optional new location."},
                "body": {"type": "string", "description": "Optional new description."},
            },
            "required": ["event_id"],
        },
    },
    {
        "name": "ex_cancel_calendar_event",
        "description": "Cancel/delete a calendar event (notifies attendees if you are the organizer).",
        "inputSchema": {
            "type": "object",
            "properties": {"event_id": {"type": "string", "description": "Event ID from ex_list_calendar_events."}},
            "required": ["event_id"],
        },
    },
]

# Tools that send/modify data. Hidden + refused in read-only mode.
WRITE_TOOLS = {
    "ex_send_email",
    "ex_reply_email",
    "ex_forward_email",
    "ex_delete_email",
    "ex_move_email",
    "ex_mark_email_read",
    "ex_create_calendar_event",
    "ex_update_calendar_event",
    "ex_cancel_calendar_event",
}


# ---------------------------------------------------------------------------
# JSON-RPC helpers
# ---------------------------------------------------------------------------

def mcp_result(id_, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def mcp_error(id_, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


def tool_result(content: str, is_error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": content}], "isError": is_error}


# ---------------------------------------------------------------------------
# EWS account construction (lazy exchangelib import → testable without the lib)
# ---------------------------------------------------------------------------

def _build_account(ctx: dict):
    """Build an exchangelib Account for the agent OWNER's mailbox (per-user).

    ``ctx`` carries the admin connection config + the resolved user email. The
    auth mode decides credentials; impersonation pins every call to the user's
    own mailbox (``primary_smtp_address``)."""
    from exchangelib import (  # noqa: PLC0415 — lazy on purpose
        Account, Configuration, Credentials, IMPERSONATION, DELEGATE,
    )

    mode = ctx.get("mode") or "service_account"
    server = ctx["server"]
    email = ctx["user_email"]

    if mode == "modern_auth":
        from exchangelib import OAuth2Credentials, Identity, OAUTH2  # noqa: PLC0415
        creds = OAuth2Credentials(
            client_id=ctx["client_id"],
            client_secret=ctx["client_secret"],
            tenant_id=ctx["tenant_id"],
            identity=Identity(primary_smtp_address=email),
        )
        config = Configuration(server=server, credentials=creds, auth_type=OAUTH2)
        return Account(primary_smtp_address=email, config=config,
                       access_type=IMPERSONATION, autodiscover=False)

    if mode == "basic":
        # The user's own credentials → delegate access to their own mailbox.
        creds = Credentials(username=ctx.get("basic_user") or email, password=ctx["basic_password"])
        config = Configuration(server=server, credentials=creds)
        return Account(primary_smtp_address=email, config=config,
                       access_type=DELEGATE, autodiscover=False)

    # default: service_account + impersonation
    creds = Credentials(username=ctx["sa_user"], password=ctx["sa_password"])
    config = Configuration(server=server, credentials=creds)
    return Account(primary_smtp_address=email, config=config,
                   access_type=IMPERSONATION, autodiscover=False)


def _folder_obj(account, name: str):
    return {
        "inbox": account.inbox,
        "sentitems": account.sent,
        "drafts": account.drafts,
        "deleteditems": account.trash,
        "junkemail": account.junk,
        "outbox": account.outbox,
    }.get(name, account.inbox)


def _token(item) -> str:
    """Compound id token (id|changekey) — changekey is needed to re-fetch/modify."""
    return f"{item.id}|{getattr(item, 'changekey', '') or ''}"


def _fetch_item(account, token: str):
    from exchangelib import Item  # noqa: PLC0415
    eid, _, eck = str(token).partition("|")
    items = list(account.fetch(ids=[Item(id=eid, changekey=eck or None)]))
    if not items:
        raise RuntimeError("Item not found.")
    it = items[0]
    if isinstance(it, Exception):
        raise RuntimeError("Item not found.")
    return it


def _to_ews(account, value: str):
    import datetime as _dt  # noqa: PLC0415
    from exchangelib import EWSDateTime  # noqa: PLC0415
    naive = _dt.datetime.fromisoformat(str(value))
    ews = EWSDateTime.from_datetime(naive.replace(tzinfo=None))
    return account.default_timezone.localize(ews)


# ---------------------------------------------------------------------------
# Tool execution (SYNC — run via asyncio.to_thread; the test seam)
# ---------------------------------------------------------------------------

def _run_tool(name: str, args: dict, ctx: dict, write_enabled: bool) -> str:
    from exchangelib import Message, Mailbox, CalendarItem, Attendee  # noqa: PLC0415

    account = _build_account(ctx)

    if name == "ex_whoami":
        return f"Connected to on-prem Exchange as {account.primary_smtp_address} (auth: {ctx.get('mode')})."

    if name == "ex_list_emails":
        folder = _folder_obj(account, _folder(args.get("folder")))
        limit = min(int(args.get("limit", 15)), 50)
        items = folder.all().only("subject", "sender", "datetime_received", "is_read", "changekey").order_by("-datetime_received")[:limit]
        lines = []
        for m in items:
            sender = getattr(getattr(m, "sender", None), "email_address", "") or ""
            when = m.datetime_received.strftime("%Y-%m-%d %H:%M") if m.datetime_received else ""
            flag = "" if m.is_read else "[UNREAD] "
            lines.append(f"• {flag}{m.subject} — {sender} ({when}) ID: {_token(m)}")
        return "\n".join(lines) if lines else "No emails."

    if name == "ex_read_email":
        m = _fetch_item(account, args["email_id"])
        body = (m.text_body or str(m.body) or "")[:12000]
        sender = getattr(getattr(m, "sender", None), "email_address", "") or ""
        return f"From: {sender}\nSubject: {m.subject}\n\n{body}"

    if name == "ex_list_calendar_events":
        import datetime as _dt  # noqa: PLC0415
        from exchangelib import EWSDateTime  # noqa: PLC0415
        days = min(int(args.get("days", 7)), 31)
        start = account.default_timezone.localize(EWSDateTime.now().replace(tzinfo=None))
        end = start + _dt.timedelta(days=days)
        lines = []
        for ev in account.calendar.view(start=start, end=end):
            s = ev.start.strftime("%Y-%m-%d %H:%M") if ev.start else ""
            e = ev.end.strftime("%H:%M") if ev.end else ""
            loc = f" @ {ev.location}" if getattr(ev, "location", None) else ""
            lines.append(f"• {ev.subject} ({s}–{e}){loc} ID: {_token(ev)}")
        return "\n".join(lines) if lines else f"No events in the next {days} days."

    # ----- write tools below -----
    if not write_enabled:
        return "This agent has read-only Exchange access."

    if name == "ex_send_email":
        m = Message(
            account=account,
            subject=str(args["subject"]),
            body=str(args["body"]),
            to_recipients=[Mailbox(email_address=a.strip()) for a in str(args["to"]).split(",") if a.strip()],
        )
        if args.get("cc"):
            m.cc_recipients = [Mailbox(email_address=a.strip()) for a in str(args["cc"]).split(",") if a.strip()]
        m.send_and_save()
        return f"E-Mail an {args['to']} gesendet."

    if name == "ex_reply_email":
        m = _fetch_item(account, args["email_id"])
        if args.get("reply_all"):
            m.reply_all(subject=f"RE: {m.subject}", body=str(args["body"]))
        else:
            m.reply(subject=f"RE: {m.subject}", body=str(args["body"]))
        return "Antwort gesendet."

    if name == "ex_forward_email":
        m = _fetch_item(account, args["email_id"])
        m.forward(
            subject=f"FW: {m.subject}",
            body=str(args.get("comment", "")),
            to_recipients=[Mailbox(email_address=a.strip()) for a in str(args["to"]).split(",") if a.strip()],
        )
        return f"E-Mail an {args['to']} weitergeleitet."

    if name == "ex_delete_email":
        m = _fetch_item(account, args["email_id"])
        m.move_to_trash()
        return "E-Mail gelöscht (in 'Gelöschte Elemente')."

    if name == "ex_move_email":
        m = _fetch_item(account, args["email_id"])
        m.move(_folder_obj(account, _folder(args["folder"])))
        return f"E-Mail nach '{_folder(args['folder'])}' verschoben."

    if name == "ex_mark_email_read":
        m = _fetch_item(account, args["email_id"])
        read = args.get("read", True)
        m.is_read = read if isinstance(read, bool) else str(read).lower() in ("true", "1", "yes")
        m.save(update_fields=["is_read"])
        return f"E-Mail als {'gelesen' if m.is_read else 'ungelesen'} markiert."

    if name == "ex_create_calendar_event":
        ev = CalendarItem(
            account=account,
            folder=account.calendar,
            subject=str(args["subject"]),
            start=_to_ews(account, args["start"]),
            end=_to_ews(account, args["end"]),
        )
        if args.get("location"):
            ev.location = str(args["location"])
        if args.get("body"):
            ev.body = str(args["body"])
        if args.get("attendees"):
            ev.required_attendees = [
                Attendee(mailbox=Mailbox(email_address=a.strip()))
                for a in str(args["attendees"]).split(",") if a.strip()
            ]
        ev.save(send_meeting_invitations="SendToAllAndSaveCopy")
        return f"Termin '{args['subject']}' erstellt. ID: {_token(ev)}"

    if name == "ex_update_calendar_event":
        ev = _fetch_item(account, args["event_id"])
        fields = []
        if args.get("subject"):
            ev.subject = str(args["subject"]); fields.append("subject")
        if args.get("location"):
            ev.location = str(args["location"]); fields.append("location")
        if args.get("body"):
            ev.body = str(args["body"]); fields.append("body")
        if args.get("start"):
            ev.start = _to_ews(account, args["start"]); fields.append("start")
        if args.get("end"):
            ev.end = _to_ews(account, args["end"]); fields.append("end")
        if not fields:
            return "Error: nothing to update — provide subject/start/end/location/body."
        ev.save(update_fields=fields, send_meeting_invitations_or_cancellations="SendToAllAndSaveCopy")
        return "Termin aktualisiert."

    if name == "ex_cancel_calendar_event":
        ev = _fetch_item(account, args["event_id"])
        try:
            ev.cancel()  # organizer → sends cancellation
        except Exception:
            ev.move_to_trash()  # attendee → just remove from own calendar
        return "Termin abgesagt/entfernt."

    return f"Unknown tool: {name}"


# ---------------------------------------------------------------------------
# JSON-RPC dispatch (shared by the transport)
# ---------------------------------------------------------------------------

async def handle_mcp_request(
    body: dict,
    resolve_context: Callable[[], Awaitable[dict | None]],
    *,
    write_enabled: bool = False,
) -> tuple[dict, int]:
    """Handle one MCP JSON-RPC message. ``resolve_context`` yields the per-user
    EWS connection context (admin config + agent-owner email + secrets) or None
    when Exchange is not configured / the user has no mailbox mapping.

    ``write_enabled`` gates WRITE_TOOLS: hidden from tools/list and refused in
    tools/call when False."""
    method = body.get("method", "")
    id_ = body.get("id")

    if method == "initialize":
        return mcp_result(id_, {
            "protocolVersion": MCP_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "mcp-exchange-onprem", "version": "1.0.0"},
        }), 200

    if method == "notifications/initialized":
        return {}, 200

    if method == "ping":
        return mcp_result(id_, {}), 200

    if method == "tools/list":
        tools = EXCHANGE_TOOLS if write_enabled else [t for t in EXCHANGE_TOOLS if t["name"] not in WRITE_TOOLS]
        return mcp_result(id_, {"tools": tools}), 200

    if method == "tools/call":
        params = body.get("params", {})
        tool_name = params.get("name", "")
        args = params.get("arguments", {})
        # Refuse write/send tools in read-only mode BEFORE resolving any context.
        if tool_name in WRITE_TOOLS and not write_enabled:
            return mcp_result(id_, tool_result(
                "This agent has read-only Exchange access. Enable Read+Write in the "
                "agent's Integrations settings to use write tools.",
                is_error=True,
            )), 200
        ctx = await resolve_context()
        if not ctx:
            return mcp_result(id_, tool_result(
                "On-prem Exchange not configured for this user. Ask an admin to set up the "
                "Exchange connection (Admin → Settings) and ensure your account has a mailbox.",
                is_error=True,
            )), 200
        try:
            result_text = await asyncio.to_thread(_run_tool, tool_name, args, ctx, write_enabled)
            return mcp_result(id_, tool_result(result_text)), 200
        except Exception as e:
            # Full detail (message + stacktrace) goes to the server log ONLY. To the
            # client we return just the exception CLASS name — a safe, bounded error
            # category (ErrorAccessDenied / ErrorImpersonateUserDenied /
            # ErrorNonExistentMailbox / UnauthorizedError / TransportError …) that
            # pinpoints the cause WITHOUT leaking server URLs, mailbox addresses,
            # tenant IDs or other internals that the free-text message can contain.
            logger.error("Exchange tool error [%s]: %s", tool_name, e, exc_info=True)
            return mcp_result(id_, tool_result(
                f"Exchange request failed ({type(e).__name__}). Check the mailbox "
                "permissions / impersonation rights and the EWS server connection; "
                "full details are in the server log.",
                is_error=True,
            )), 200

    return mcp_error(id_, -32601, f"Method not found: {method}"), 404

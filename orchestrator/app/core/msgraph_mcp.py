"""Shared MS Graph MCP core — tool definitions, Graph calls, and JSON-RPC dispatch.

Used by BOTH transports so the tool logic lives in exactly one place:
  - app/api/mcp_msgraph.py  — per-AGENT endpoint (auth: agent HMAC token, token
    resolved from the agent's owner's Microsoft OAuthIntegration)
  - app/api/mcp_msgraph_external.py — per-USER endpoint for external LLM clients
    (auth: our OAuth 2.1 access token, token resolved from that user's integration)

The only thing that differs between the two is HOW the Microsoft access token is
resolved — so the transports pass a ``resolve_token`` coroutine into
``handle_mcp_request`` and everything else is shared.
"""

import json
import logging
import re
from typing import Awaitable, Callable
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
MCP_VERSION = "2025-06-18"

# --- Input hardening for LLM-supplied tool arguments ------------------------
# Mail folders are a fixed set — never interpolate arbitrary folder strings.
_ALLOWED_FOLDERS = {
    "inbox", "sentitems", "drafts", "deleteditems", "junkemail",
    "archive", "clutter", "outbox",
}
# KQL metacharacters stripped from $search field values to prevent breaking out
# of the quoted search expression (KQL injection within the user's own mailbox).
_KQL_BAD = re.compile(r'[":()\\*?~^|&]')


def _gid(value) -> str:
    """URL-encode a Graph resource ID for safe path interpolation.

    Encoding the slashes/dots blocks path traversal (e.g. ``../users/victim``)
    while leaving legitimate base64-ish Graph IDs intact (Graph decodes them)."""
    return quote(str(value), safe="")


def _folder(value) -> str:
    v = (str(value) if value else "inbox").strip().lower()
    return v if v in _ALLOWED_FOLDERS else "inbox"


def _kql(value) -> str:
    return _KQL_BAD.sub("", str(value))


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

MSGRAPH_TOOLS = [
    {
        "name": "ms_get_user_info",
        "description": "Get the Microsoft account profile of the connected user (name, email, job title).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "ms_list_emails",
        "description": "List or SEARCH emails from the user's mailbox. Filter by free text (subject+body), by sender, and/or by subject. Defaults to inbox, newest first.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "folder": {"type": "string", "description": "Folder name (inbox, sentitems, drafts). Default: inbox."},
                "limit": {"type": "number", "description": "Max emails to return (1-50). Default: 10."},
                "search": {"type": "string", "description": "Free-text query across subject and body."},
                "sender": {"type": "string", "description": "Filter by sender — name or email address (e.g. 'alice@contoso.com' or 'Alice')."},
                "subject": {"type": "string", "description": "Filter by words in the subject line."},
            },
        },
    },
    {
        "name": "ms_read_email",
        "description": "Read the full content of a specific email by its ID.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "email_id": {"type": "string", "description": "The email ID (from ms_list_emails)."},
            },
            "required": ["email_id"],
        },
    },
    {
        "name": "ms_send_email",
        "description": "Send an email from the user's Microsoft mailbox.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient email address (or comma-separated list)."},
                "subject": {"type": "string", "description": "Email subject."},
                "body": {"type": "string", "description": "Email body (plain text or HTML)."},
                "body_type": {"type": "string", "description": "Content type: 'Text' or 'HTML'. Default: Text."},
                "cc": {"type": "string", "description": "Optional CC recipients (comma-separated)."},
            },
            "required": ["to", "subject", "body"],
        },
    },
    {
        "name": "ms_reply_email",
        "description": "Reply to an existing email.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "email_id": {"type": "string", "description": "The email ID to reply to."},
                "body": {"type": "string", "description": "Reply body text."},
                "reply_all": {"type": "boolean", "description": "Reply to all recipients. Default: false."},
            },
            "required": ["email_id", "body"],
        },
    },
    {
        "name": "ms_list_calendar_events",
        "description": "List upcoming calendar events for the user.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "days_ahead": {"type": "number", "description": "How many days ahead to look (1-90). Default: 7."},
                "limit": {"type": "number", "description": "Max events to return. Default: 20."},
            },
        },
    },
    {
        "name": "ms_create_calendar_event",
        "description": "Create a calendar event in the user's Microsoft calendar.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "Event title."},
                "start": {"type": "string", "description": "Start datetime in ISO 8601 (e.g. 2026-04-25T10:00:00)."},
                "end": {"type": "string", "description": "End datetime in ISO 8601."},
                "timezone": {"type": "string", "description": "IANA timezone (e.g. Europe/Berlin). Default: UTC."},
                "body": {"type": "string", "description": "Event description (optional)."},
                "location": {"type": "string", "description": "Location (optional)."},
                "attendees": {"type": "string", "description": "Comma-separated attendee emails (optional)."},
                "online_meeting": {"type": "boolean", "description": "Create Teams meeting link. Default: false."},
            },
            "required": ["subject", "start", "end"],
        },
    },
    {
        "name": "ms_list_teams",
        "description": "List all Microsoft Teams the user is a member of.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "ms_list_channels",
        "description": "List channels in a Microsoft Team.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "team_id": {"type": "string", "description": "The Team ID (from ms_list_teams)."},
            },
            "required": ["team_id"],
        },
    },
    {
        "name": "ms_send_teams_message",
        "description": "Send a message to a Microsoft Teams channel.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "team_id": {"type": "string", "description": "The Team ID."},
                "channel_id": {"type": "string", "description": "The Channel ID."},
                "message": {"type": "string", "description": "Message text (supports basic HTML)."},
            },
            "required": ["team_id", "channel_id", "message"],
        },
    },
    {
        "name": "ms_list_chats",
        "description": "List recent 1:1 and group chats in Microsoft Teams.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "number", "description": "Max chats to return. Default: 20."},
            },
        },
    },
    {
        "name": "ms_send_chat_message",
        "description": "Send a message to a Teams 1:1 or group chat.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "chat_id": {"type": "string", "description": "The chat ID (from ms_list_chats)."},
                "message": {"type": "string", "description": "Message text."},
            },
            "required": ["chat_id", "message"],
        },
    },
    {
        "name": "ms_list_tasks",
        "description": "List tasks from Microsoft To-Do. Returns all task lists with their tasks.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "list_id": {"type": "string", "description": "Optional: specific task list ID to read."},
                "include_completed": {"type": "boolean", "description": "Include completed tasks. Default: false."},
            },
        },
    },
    {
        "name": "ms_create_task",
        "description": "Create a task in Microsoft To-Do.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Task title."},
                "list_id": {"type": "string", "description": "Task list ID (from ms_list_tasks). Uses default list if omitted."},
                "due_date": {"type": "string", "description": "Due date in YYYY-MM-DD format (optional)."},
                "body": {"type": "string", "description": "Task notes/description (optional)."},
                "importance": {"type": "string", "description": "Priority: low, normal, high. Default: normal."},
            },
            "required": ["title"],
        },
    },
    {
        "name": "ms_search_files",
        "description": "Search for files in OneDrive and SharePoint.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query (filename, content, etc.)."},
                "limit": {"type": "number", "description": "Max results. Default: 10."},
            },
            "required": ["query"],
        },
    },
]


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
# Graph API
# ---------------------------------------------------------------------------

async def _graph(method: str, path: str, token: str, **kwargs) -> dict:
    """Make an MS Graph API call and return parsed JSON."""
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.request(
            method,
            f"{GRAPH_BASE}{path}",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            **kwargs,
        )
        if resp.status_code == 204:
            return {}
        data = resp.json()
        if resp.status_code >= 400:
            err = data.get("error", {}) if isinstance(data, dict) else {}
            # Log full detail server-side; return only a generic status to the client
            # (Graph messages can leak tenant domains, UPNs, object IDs).
            logger.warning("Graph API %s on %s: %s", resp.status_code, path, str(err.get("message", ""))[:300])
            raise RuntimeError(f"Microsoft Graph request failed (HTTP {resp.status_code}).")
        return data


def _fmt_size(b: int) -> str:
    if b < 1024:
        return f"{b}B"
    if b < 1024 ** 2:
        return f"{b // 1024}KB"
    return f"{b // (1024 ** 2)}MB"


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

async def handle_tool(name: str, args: dict, token: str) -> str:

    if name == "ms_get_user_info":
        data = await _graph("GET", "/me", token)
        return f"Name: {data.get('displayName')}\nEmail: {data.get('mail') or data.get('userPrincipalName')}\nTitle: {data.get('jobTitle', '—')}\nDepartment: {data.get('department', '—')}"

    elif name == "ms_list_emails":
        folder = _folder(args.get("folder", "inbox"))
        limit = min(int(args.get("limit", 10)), 50)
        search = _kql((args.get("search") or "").strip())
        sender = _kql((args.get("sender") or "").strip())
        subject = _kql((args.get("subject") or "").strip())
        params: dict = {"$top": limit, "$select": "id,subject,from,receivedDateTime,isRead,bodyPreview"}
        # Build a KQL $search from the filters (from:/subject:/free-text).
        # Graph forbids $orderby together with $search, so only sort otherwise.
        expr = []
        if subject:
            expr.append(f"subject:{subject}")
        if sender:
            expr.append(f"from:{sender}")
        if search:
            expr.append(search)
        if expr:
            params["$search"] = '"' + " AND ".join(expr) + '"'
        else:
            params["$orderby"] = "receivedDateTime desc"
        data = await _graph("GET", f"/me/mailFolders/{folder}/messages", token, params=params)
        emails = data.get("value", [])
        if not emails:
            return "No emails found."
        lines = []
        for e in emails:
            read = "" if e.get("isRead") else " [UNREAD]"
            sender = e.get("from", {}).get("emailAddress", {})
            lines.append(f"ID: {e['id']}\nFrom: {sender.get('name', '')} <{sender.get('address', '')}>{read}\nSubject: {e.get('subject', '')}\nDate: {e.get('receivedDateTime', '')[:19]}\nPreview: {e.get('bodyPreview', '')[:120]}\n---")
        return "\n".join(lines)

    elif name == "ms_read_email":
        data = await _graph("GET", f"/me/messages/{_gid(args['email_id'])}", token,
                            params={"$select": "id,subject,from,toRecipients,ccRecipients,receivedDateTime,body"})
        sender = data.get("from", {}).get("emailAddress", {})
        to = ", ".join(r["emailAddress"]["address"] for r in data.get("toRecipients", []))
        body = data.get("body", {}).get("content", "")
        body_clean = re.sub(r"<[^>]+>", "", body).strip()[:3000]
        return f"Subject: {data.get('subject')}\nFrom: {sender.get('name')} <{sender.get('address')}>\nTo: {to}\nDate: {data.get('receivedDateTime', '')[:19]}\n\n{body_clean}"

    elif name == "ms_send_email":
        recipients = [{"emailAddress": {"address": a.strip()}} for a in args["to"].split(",")]
        payload: dict = {
            "message": {
                "subject": args["subject"],
                "body": {"contentType": args.get("body_type", "Text"), "content": args["body"]},
                "toRecipients": recipients,
            },
            "saveToSentItems": True,
        }
        if args.get("cc"):
            payload["message"]["ccRecipients"] = [{"emailAddress": {"address": a.strip()}} for a in args["cc"].split(",")]
        await _graph("POST", "/me/sendMail", token, content=json.dumps(payload))
        return f"Email sent to {args['to']}."

    elif name == "ms_reply_email":
        endpoint = "/me/messages/{}/replyAll" if args.get("reply_all") else "/me/messages/{}/reply"
        payload = {"comment": args["body"]}
        await _graph("POST", endpoint.format(_gid(args["email_id"])), token, content=json.dumps(payload))
        return "Reply sent."

    elif name == "ms_list_calendar_events":
        from datetime import datetime, timedelta, timezone
        days = min(int(args.get("days_ahead", 7)), 90)
        limit = min(int(args.get("limit", 20)), 50)
        now = datetime.now(timezone.utc)
        end = now + timedelta(days=days)
        params = {
            "startDateTime": now.isoformat(),
            "endDateTime": end.isoformat(),
            "$top": limit,
            "$select": "id,subject,start,end,location,organizer,isOnlineMeeting,onlineMeetingUrl",
            "$orderby": "start/dateTime",
        }
        data = await _graph("GET", "/me/calendarView", token, params=params)
        events = data.get("value", [])
        if not events:
            return f"No events in the next {days} days."
        lines = []
        for e in events:
            start = e.get("start", {}).get("dateTime", "")[:16]
            end_dt = e.get("end", {}).get("dateTime", "")[:16]
            loc = e.get("location", {}).get("displayName", "")
            online = " (Teams)" if e.get("isOnlineMeeting") else ""
            lines.append(f"• {e.get('subject')}\n  {start} → {end_dt}{(' | ' + loc) if loc else ''}{online}\n  ID: {e['id']}")
        return "\n".join(lines)

    elif name == "ms_create_calendar_event":
        tz = args.get("timezone", "UTC")
        payload: dict = {
            "subject": args["subject"],
            "start": {"dateTime": args["start"], "timeZone": tz},
            "end": {"dateTime": args["end"], "timeZone": tz},
        }
        if args.get("body"):
            payload["body"] = {"contentType": "Text", "content": args["body"]}
        if args.get("location"):
            payload["location"] = {"displayName": args["location"]}
        if args.get("attendees"):
            payload["attendees"] = [
                {"emailAddress": {"address": a.strip()}, "type": "required"}
                for a in args["attendees"].split(",")
            ]
        if args.get("online_meeting"):
            payload["isOnlineMeeting"] = True
            payload["onlineMeetingProvider"] = "teamsForBusiness"
        data = await _graph("POST", "/me/events", token, content=json.dumps(payload))
        url = data.get("onlineMeetingUrl", "")
        return f"Event created: '{args['subject']}' on {args['start']}.{(' Teams link: ' + url) if url else ''} ID: {data.get('id')}"

    elif name == "ms_list_teams":
        data = await _graph("GET", "/me/joinedTeams", token, params={"$select": "id,displayName,description"})
        teams = data.get("value", [])
        if not teams:
            return "No Teams found."
        return "\n".join(f"• {t['displayName']} (ID: {t['id']})\n  {t.get('description', '')[:80]}" for t in teams)

    elif name == "ms_list_channels":
        data = await _graph("GET", f"/teams/{_gid(args['team_id'])}/channels", token,
                            params={"$select": "id,displayName,description"})
        channels = data.get("value", [])
        if not channels:
            return "No channels found."
        return "\n".join(f"• {c['displayName']} (ID: {c['id']})" for c in channels)

    elif name == "ms_send_teams_message":
        payload = {"body": {"content": args["message"]}}
        data = await _graph("POST", f"/teams/{_gid(args['team_id'])}/channels/{_gid(args['channel_id'])}/messages",
                            token, content=json.dumps(payload))
        return f"Message sent. ID: {data.get('id')}"

    elif name == "ms_list_chats":
        limit = min(int(args.get("limit", 20)), 50)
        data = await _graph("GET", "/me/chats", token,
                            params={"$top": limit, "$select": "id,chatType,topic,lastUpdatedDateTime"})
        chats = data.get("value", [])
        if not chats:
            return "No chats found."
        return "\n".join(f"• {c.get('topic') or c['chatType']} (ID: {c['id']}) — {c.get('lastUpdatedDateTime', '')[:10]}" for c in chats)

    elif name == "ms_send_chat_message":
        payload = {"body": {"content": args["message"]}}
        data = await _graph("POST", f"/me/chats/{_gid(args['chat_id'])}/messages", token, content=json.dumps(payload))
        return f"Chat message sent. ID: {data.get('id')}"

    elif name == "ms_list_tasks":
        lists_data = await _graph("GET", "/me/todo/lists", token, params={"$select": "id,displayName"})
        task_lists = lists_data.get("value", [])
        if not task_lists:
            return "No task lists found."
        if args.get("list_id"):
            task_lists = [t for t in task_lists if t["id"] == args["list_id"]] or task_lists[:1]
        result_lines = []
        include_completed = args.get("include_completed", False)
        for tl in task_lists[:5]:
            params: dict = {"$select": "id,title,status,importance,dueDateTime,body"}
            if not include_completed:
                params["$filter"] = "status ne 'completed'"
            tasks_data = await _graph("GET", f"/me/todo/lists/{tl['id']}/tasks", token, params=params)
            tasks = tasks_data.get("value", [])
            result_lines.append(f"**{tl['displayName']}** (List ID: {tl['id']})")
            if not tasks:
                result_lines.append("  (no tasks)")
            for t in tasks[:20]:
                due = t.get("dueDateTime", {}).get("dateTime", "")[:10] if t.get("dueDateTime") else ""
                result_lines.append(f"  • [{t.get('status', '')}] {t.get('title')} {('due: ' + due) if due else ''} (ID: {t['id']})")
        return "\n".join(result_lines)

    elif name == "ms_create_task":
        list_id = args.get("list_id")
        if not list_id:
            lists_data = await _graph("GET", "/me/todo/lists", token, params={"$select": "id,wellknownListName"})
            task_lists = lists_data.get("value", [])
            default = next((t for t in task_lists if t.get("wellknownListName") == "defaultList"), None)
            list_id = default["id"] if default else (task_lists[0]["id"] if task_lists else None)
        if not list_id:
            return "Error: no task list found."
        payload: dict = {
            "title": args["title"],
            "importance": args.get("importance", "normal"),
            "status": "notStarted",
        }
        if args.get("due_date"):
            payload["dueDateTime"] = {"dateTime": f"{args['due_date']}T00:00:00", "timeZone": "UTC"}
        if args.get("body"):
            payload["body"] = {"content": args["body"], "contentType": "text"}
        data = await _graph("POST", f"/me/todo/lists/{_gid(list_id)}/tasks", token, content=json.dumps(payload))
        return f"Task created: '{data.get('title')}' (ID: {data.get('id')})"

    elif name == "ms_search_files":
        limit = min(int(args.get("limit", 10)), 25)
        # Escape single quotes for the OData search(q='...') literal.
        q = str(args["query"]).replace("'", "''")
        data = await _graph("GET", f"/me/drive/root/search(q='{q}')",
                            token, params={"$top": limit, "$select": "id,name,webUrl,size,lastModifiedDateTime"})
        files = data.get("value", [])
        if not files:
            return f"No files found for '{args['query']}'."
        return "\n".join(
            f"• {f['name']} ({_fmt_size(f.get('size', 0))})\n  Modified: {f.get('lastModifiedDateTime', '')[:10]}\n  URL: {f.get('webUrl', '')}"
            for f in files
        )

    else:
        return f"Unknown tool: {name}"


# ---------------------------------------------------------------------------
# JSON-RPC dispatch (shared by both transports)
# ---------------------------------------------------------------------------

async def handle_mcp_request(
    body: dict,
    resolve_token: Callable[[], Awaitable[str | None]],
) -> tuple[dict, int]:
    """Handle one MCP JSON-RPC message; ``resolve_token`` yields the caller's
    Microsoft access token (or None if not connected). Returns ``(json, status)``."""
    method = body.get("method", "")
    id_ = body.get("id")

    if method == "initialize":
        return mcp_result(id_, {
            "protocolVersion": MCP_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "mcp-msgraph", "version": "1.0.0"},
        }), 200

    if method == "notifications/initialized":
        return {}, 200

    if method == "ping":
        return mcp_result(id_, {}), 200

    if method == "tools/list":
        return mcp_result(id_, {"tools": MSGRAPH_TOOLS}), 200

    if method == "tools/call":
        params = body.get("params", {})
        tool_name = params.get("name", "")
        args = params.get("arguments", {})
        token = await resolve_token()
        if not token:
            return mcp_result(id_, tool_result(
                "Microsoft account not connected. Connect your Microsoft 365 account first, "
                "then retry.",
                is_error=True,
            )), 200
        try:
            result_text = await handle_tool(tool_name, args, token)
            return mcp_result(id_, tool_result(result_text)), 200
        except RuntimeError as e:
            return mcp_result(id_, tool_result(str(e), is_error=True)), 200
        except Exception as e:
            logger.error(f"MS Graph tool error [{tool_name}]: {e}", exc_info=True)
            return mcp_result(id_, tool_result(f"Error: {e}", is_error=True)), 200

    return mcp_error(id_, -32601, f"Method not found: {method}"), 404

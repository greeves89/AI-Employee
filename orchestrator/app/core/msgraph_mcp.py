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
        "name": "ms_list_chat_messages",
        "description": "Read recent messages of a Teams 1:1 or group chat — use this to read or summarize a conversation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "chat_id": {"type": "string", "description": "The chat ID (from ms_list_chats)."},
                "limit": {"type": "number", "description": "Max messages, newest first. Default: 25, max 50."},
            },
            "required": ["chat_id"],
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
    {
        "name": "ms_list_channel_messages",
        "description": "Read recent messages of a Microsoft Teams channel — use this to read or summarize a channel conversation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "team_id": {"type": "string", "description": "The Team ID (from ms_list_teams)."},
                "channel_id": {"type": "string", "description": "The Channel ID (from ms_list_channels)."},
                "limit": {"type": "number", "description": "Max messages, newest first. Default: 25, max 50."},
            },
            "required": ["team_id", "channel_id"],
        },
    },
    {
        "name": "ms_list_planner_plans",
        "description": "List Microsoft Planner plans the user has access to.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "ms_list_planner_tasks",
        "description": "List Microsoft Planner tasks — for a specific plan, or all tasks assigned to the user.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "plan_id": {"type": "string", "description": "Optional: specific plan ID (from ms_list_planner_plans). Omit to list tasks assigned to you."},
                "limit": {"type": "number", "description": "Max tasks to return. Default: 25, max 50."},
            },
        },
    },
    {
        "name": "ms_create_planner_task",
        "description": "Create a task in a Microsoft Planner plan.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "plan_id": {"type": "string", "description": "The plan ID (from ms_list_planner_plans)."},
                "title": {"type": "string", "description": "Task title."},
                "bucket_id": {"type": "string", "description": "Optional: bucket ID to place the task in."},
                "due_date": {"type": "string", "description": "Optional due date in YYYY-MM-DD format."},
            },
            "required": ["plan_id", "title"],
        },
    },
    {
        "name": "ms_search_people",
        "description": "Find people relevant to the user (colleagues, frequent contacts) by name or keyword — use this to resolve a person's name to an email address.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Name or keyword to search for (e.g. 'Daniel Alisch')."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "ms_search",
        "description": "Universal Microsoft Search across the user's content (emails, calendar events, files, Teams chat messages). Use this for broad 'find anything about X' queries.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query string."},
                "types": {"type": "array", "items": {"type": "string"}, "description": "Entity types to search. Default: message, event, driveItem, chatMessage."},
                "limit": {"type": "number", "description": "Max results. Default: 15, max 25."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "ms_graph_get",
        "description": "Advanced/fallback: read-only GET on ANY Microsoft Graph v1.0 endpoint (relative path like /me/messages). Bounded by your delegated permissions. Use only when no specific tool fits.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative Graph v1.0 path starting with / (e.g. /me/messages)."},
                "params": {"type": "object", "description": "Optional query parameters (e.g. {\"$top\": 5})."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "ms_list_folder",
        "description": "List the contents (files and folders) of a OneDrive folder.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Folder path relative to the drive root, e.g. 'Projekte/2026'. Omit for the root."},
            },
        },
    },
    {
        "name": "ms_read_file_content",
        "description": "Read the text content of a OneDrive file (best for .txt/.md/.csv/.json). Returns up to ~12k chars.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to the drive root, e.g. 'Notizen/protokoll.md'."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "ms_create_folder",
        "description": "Create a new folder in OneDrive.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name of the new folder."},
                "parent_path": {"type": "string", "description": "Parent folder path relative to root (e.g. 'Projekte'). Omit to create in the root."},
            },
            "required": ["name"],
        },
    },
    {
        "name": "ms_upload_text_file",
        "description": "Create or overwrite a text file in OneDrive with the given content.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Target file path relative to root, e.g. 'Notizen/todo.md'."},
                "content": {"type": "string", "description": "The text content to write."},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "ms_share_file",
        "description": "Create a sharing link for a OneDrive file or folder.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path of the file/folder relative to root."},
                "link_type": {"type": "string", "description": "'view' (read-only) or 'edit'. Default: view."},
                "scope": {"type": "string", "description": "'organization' (only your tenant) or 'anonymous'. Default: organization."},
            },
            "required": ["path"],
        },
    },
    # --- Mail: delete + re-exposed write handlers -------------------------------
    {
        "name": "ms_delete_email",
        "description": "Delete an email (moves it to Deleted Items).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "email_id": {"type": "string", "description": "The message ID (from ms_list_emails)."},
            },
            "required": ["email_id"],
        },
    },
    {
        "name": "ms_forward_email",
        "description": "Forward an email to one or more recipients.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "email_id": {"type": "string", "description": "The message ID (from ms_list_emails)."},
                "to": {"type": "string", "description": "Recipient email address(es), comma-separated."},
                "comment": {"type": "string", "description": "Optional comment added above the forwarded message."},
            },
            "required": ["email_id", "to"],
        },
    },
    {
        "name": "ms_move_email",
        "description": "Move an email to a well-known folder (inbox, archive, deleteditems, junkemail, drafts, sentitems).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "email_id": {"type": "string", "description": "The message ID (from ms_list_emails)."},
                "folder": {"type": "string", "description": "Destination: inbox, archive, deleteditems, junkemail, drafts, sentitems."},
            },
            "required": ["email_id", "folder"],
        },
    },
    {
        "name": "ms_mark_email_read",
        "description": "Mark an email as read or unread.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "email_id": {"type": "string", "description": "The message ID (from ms_list_emails)."},
                "read": {"type": "boolean", "description": "true = read (default), false = unread."},
            },
            "required": ["email_id"],
        },
    },
    # --- Calendar: update + respond + cancel -----------------------------------
    {
        "name": "ms_update_calendar_event",
        "description": "Update/reschedule a calendar event (subject, start, end, location or body).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string", "description": "The event ID (from ms_list_calendar_events)."},
                "subject": {"type": "string", "description": "Optional new subject/title."},
                "start": {"type": "string", "description": "Optional new start, ISO 8601 (e.g. 2026-07-01T09:00:00)."},
                "end": {"type": "string", "description": "Optional new end, ISO 8601."},
                "timezone": {"type": "string", "description": "Timezone for start/end (default UTC), e.g. Europe/Berlin."},
                "location": {"type": "string", "description": "Optional new location."},
                "body": {"type": "string", "description": "Optional new description/body text."},
            },
            "required": ["event_id"],
        },
    },
    {
        "name": "ms_respond_event",
        "description": "Respond to a meeting invitation (accept, decline or tentative).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string", "description": "The event ID (from ms_list_calendar_events)."},
                "response": {"type": "string", "description": "One of: accept, decline, tentative."},
                "comment": {"type": "string", "description": "Optional message sent with the response."},
            },
            "required": ["event_id", "response"],
        },
    },
    {
        "name": "ms_cancel_event",
        "description": "Cancel an event you organize (notifies attendees) — or remove it from your calendar if you are not the organizer.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string", "description": "The event ID (from ms_list_calendar_events)."},
                "comment": {"type": "string", "description": "Optional cancellation message to attendees."},
            },
            "required": ["event_id"],
        },
    },
    # --- To-Do: update + complete + delete -------------------------------------
    {
        "name": "ms_update_task",
        "description": "Update a Microsoft To-Do task (title, due date or status).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "list_id": {"type": "string", "description": "The To-Do list ID (from ms_list_tasks)."},
                "task_id": {"type": "string", "description": "The task ID (from ms_list_tasks)."},
                "title": {"type": "string", "description": "Optional new title."},
                "due_date": {"type": "string", "description": "Optional due date in YYYY-MM-DD."},
                "status": {"type": "string", "description": "Optional: notStarted, inProgress or completed."},
            },
            "required": ["list_id", "task_id"],
        },
    },
    {
        "name": "ms_complete_task",
        "description": "Mark a Microsoft To-Do task as completed.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "list_id": {"type": "string", "description": "The To-Do list ID (from ms_list_tasks)."},
                "task_id": {"type": "string", "description": "The task ID (from ms_list_tasks)."},
            },
            "required": ["list_id", "task_id"],
        },
    },
    {
        "name": "ms_delete_task",
        "description": "Delete a Microsoft To-Do task.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "list_id": {"type": "string", "description": "The To-Do list ID (from ms_list_tasks)."},
                "task_id": {"type": "string", "description": "The task ID (from ms_list_tasks)."},
            },
            "required": ["list_id", "task_id"],
        },
    },
    # --- Planner: update + delete (full CRUD) ----------------------------------
    {
        "name": "ms_update_planner_task",
        "description": "Update a Microsoft Planner task — title, due date, completion (percent_complete 0/50/100) or bucket. Use percent_complete=100 to mark it done.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "The Planner task ID (from ms_list_planner_tasks)."},
                "title": {"type": "string", "description": "Optional new title."},
                "due_date": {"type": "string", "description": "Optional due date in YYYY-MM-DD."},
                "percent_complete": {"type": "number", "description": "Optional completion: 0 (open), 50 (in progress) or 100 (done)."},
                "bucket_id": {"type": "string", "description": "Optional: move the task to this bucket."},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "ms_delete_planner_task",
        "description": "Delete a Microsoft Planner task.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "The Planner task ID (from ms_list_planner_tasks)."},
            },
            "required": ["task_id"],
        },
    },
    # --- OneDrive: delete + move/rename ----------------------------------------
    {
        "name": "ms_delete_item",
        "description": "Delete a OneDrive file or folder (moves it to the OneDrive recycle bin).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path of the file/folder relative to root, e.g. 'Projekte/alt.txt'."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "ms_move_item",
        "description": "Rename and/or move a OneDrive file or folder.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Current path relative to root."},
                "new_name": {"type": "string", "description": "Optional new name."},
                "new_parent_path": {"type": "string", "description": "Optional new parent folder path relative to root (empty = root)."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "ms_copy_item",
        "description": "COPY a OneDrive file or folder into another folder (the original stays). Use this to duplicate a file — do NOT try a raw Graph copy call.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path of the source file/folder relative to root, e.g. 'Präsentationen/deck.pptx'."},
                "dest_parent_path": {"type": "string", "description": "Destination folder path relative to root (empty = root), e.g. 'Agent_Ordner'."},
                "new_name": {"type": "string", "description": "Optional name for the copy (defaults to the source name)."},
            },
            "required": ["path", "dest_parent_path"],
        },
    },
    # --- Contacts: full CRUD ---------------------------------------------------
    {
        "name": "ms_list_contacts",
        "description": "List the user's personal Outlook contacts.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "number", "description": "Max contacts to return. Default: 25, max 50."},
            },
        },
    },
    {
        "name": "ms_create_contact",
        "description": "Create a personal Outlook contact.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "given_name": {"type": "string", "description": "First name."},
                "surname": {"type": "string", "description": "Optional last name."},
                "email": {"type": "string", "description": "Optional email address."},
                "mobile": {"type": "string", "description": "Optional mobile phone number."},
                "company": {"type": "string", "description": "Optional company name."},
            },
            "required": ["given_name"],
        },
    },
    {
        "name": "ms_update_contact",
        "description": "Update an existing Outlook contact.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "contact_id": {"type": "string", "description": "The contact ID (from ms_list_contacts)."},
                "given_name": {"type": "string", "description": "Optional new first name."},
                "surname": {"type": "string", "description": "Optional new last name."},
                "email": {"type": "string", "description": "Optional new email address."},
                "mobile": {"type": "string", "description": "Optional new mobile phone number."},
                "company": {"type": "string", "description": "Optional new company name."},
            },
            "required": ["contact_id"],
        },
    },
    {
        "name": "ms_delete_contact",
        "description": "Delete an Outlook contact.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "contact_id": {"type": "string", "description": "The contact ID (from ms_list_contacts)."},
            },
            "required": ["contact_id"],
        },
    },
]

# Tools that CREATE/SEND/MODIFY Microsoft data. Everything else in MSGRAPH_TOOLS
# is read-only. Agents in read-only mode (and the external per-user transport)
# never see or get to call these.
WRITE_TOOLS = {
    "ms_send_email",
    "ms_reply_email",
    "ms_forward_email",
    "ms_delete_email",
    "ms_move_email",
    "ms_mark_email_read",
    "ms_create_calendar_event",
    "ms_update_calendar_event",
    "ms_respond_event",
    "ms_cancel_event",
    "ms_send_teams_message",
    "ms_send_chat_message",
    "ms_create_task",
    "ms_update_task",
    "ms_complete_task",
    "ms_delete_task",
    "ms_create_planner_task",
    "ms_update_planner_task",
    "ms_delete_planner_task",
    # OneDrive write
    "ms_create_folder",
    "ms_upload_text_file",
    "ms_share_file",
    "ms_delete_item",
    "ms_move_item",
    "ms_copy_item",
    # Contacts write
    "ms_create_contact",
    "ms_update_contact",
    "ms_delete_contact",
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
# Graph API
# ---------------------------------------------------------------------------

async def _graph(method: str, path: str, token: str, **kwargs) -> dict:
    """Make an MS Graph API call and return parsed JSON.

    Extra request headers (e.g. ``If-Match`` for Planner PATCH/DELETE, which Graph
    rejects without the task's ETag) can be passed via ``headers=`` — they are
    merged onto the auth/content-type defaults."""
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    headers.update(kwargs.pop("headers", None) or {})
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.request(
            method,
            f"{GRAPH_BASE}{path}",
            headers=headers,
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


async def _planner_etag(task_id, token: str) -> str:
    """Fetch a Planner task's ``@odata.etag`` — Graph requires it as the
    ``If-Match`` header for any Planner PATCH/DELETE, otherwise it 412s."""
    data = await _graph("GET", f"/planner/tasks/{_gid(task_id)}", token)
    return data.get("@odata.etag", "")


def _fmt_size(b: int) -> str:
    if b < 1024:
        return f"{b}B"
    if b < 1024 ** 2:
        return f"{b // 1024}KB"
    return f"{b // (1024 ** 2)}MB"


def _strip_html(s) -> str:
    return re.sub(r"<[^>]+>", "", s or "").replace("&nbsp;", " ").strip()


def _drive_path(path) -> str:
    """Encode a OneDrive item path for the ``/drive/root:/{path}:`` addressing.

    Strips surrounding slashes and URL-encodes each segment (so spaces, umlauts
    etc. are safe) while keeping ``/`` as the segment separator — blocks traversal
    because each segment is encoded, not the slashes."""
    p = str(path or "").strip().strip("/")
    # Drop "." / ".." segments — otherwise URL normalisation of the
    # /root:/{path}: address could traverse out of the drive.
    return "/".join(quote(seg, safe="") for seg in p.split("/") if seg and seg not in (".", ".."))


async def _graph_bytes(method: str, path: str, token: str, content: bytes | None = None,
                       content_type: str = "text/plain") -> httpx.Response:
    """Graph call for RAW (non-JSON) bodies/responses — file content up/download.

    Unlike ``_graph`` this does not force ``application/json`` and returns the raw
    response (caller reads ``.text`` / ``.json()`` as needed)."""
    headers = {"Authorization": f"Bearer {token}"}
    if content is not None:
        headers["Content-Type"] = content_type
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.request(method, f"{GRAPH_BASE}{path}", headers=headers, content=content)
        if resp.status_code >= 400:
            logger.warning("Graph API %s on %s: %s", resp.status_code, path, resp.text[:200])
            raise RuntimeError(f"Microsoft Graph request failed (HTTP {resp.status_code}).")
        return resp


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

async def handle_tool(name: str, args: dict, token: str, *, draft_mail: bool = False) -> str:

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
        message: dict = {
            "subject": args["subject"],
            "body": {"contentType": args.get("body_type", "Text"), "content": args["body"]},
            "toRecipients": recipients,
        }
        if args.get("cc"):
            message["ccRecipients"] = [{"emailAddress": {"address": a.strip()}} for a in args["cc"].split(",")]
        if draft_mail:
            # Create an Outlook draft instead of sending — leaves the final send
            # to the human.
            await _graph("POST", "/me/messages", token, content=json.dumps(message))
            return f"Email-Entwurf in Outlook erstellt (NICHT gesendet): '{args['subject']}'."
        payload: dict = {"message": message, "saveToSentItems": True}
        await _graph("POST", "/me/sendMail", token, content=json.dumps(payload))
        return f"Email sent to {args['to']}."

    elif name == "ms_reply_email":
        if draft_mail:
            # createReply produces a reply draft in the mailbox — not sent.
            await _graph("POST", f"/me/messages/{_gid(args['email_id'])}/createReply", token)
            return "Antwort-Entwurf erstellt (NICHT gesendet)."
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
        # NOTE: nested "$expand=members($select=...)" makes Graph return HTTP 400
        # here — expand members plainly and pick displayName from the full object.
        data = await _graph("GET", "/me/chats", token,
                            params={"$top": limit, "$expand": "members"})
        chats = data.get("value", [])
        if not chats:
            return "No chats found."
        lines = []
        for c in chats:
            names = ", ".join(m.get("displayName", "") for m in c.get("members", []) if m.get("displayName"))
            label = c.get("topic") or names or c.get("chatType", "chat")
            lines.append(f"• {label} (ID: {c['id']}) — {c.get('lastUpdatedDateTime', '')[:10]}")
        return "\n".join(lines)

    elif name == "ms_list_chat_messages":
        limit = min(int(args.get("limit", 25)), 50)
        data = await _graph("GET", f"/me/chats/{_gid(args['chat_id'])}/messages", token,
                            params={"$top": limit})
        messages = data.get("value", [])
        lines = []
        for m in messages:
            sender = m.get("from", {}).get("user", {}).get("displayName") or "—"
            when = m.get("createdDateTime", "")[:16]
            text = _strip_html(m.get("body", {}).get("content", ""))
            if not text:
                continue
            lines.append(f"[{when}] {sender}: {text[:500]}")
        if not lines:
            return "No messages found."
        return "\n".join(lines)

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

    elif name == "ms_list_channel_messages":
        limit = min(int(args.get("limit", 25)), 50)
        data = await _graph("GET",
                            f"/teams/{_gid(args['team_id'])}/channels/{_gid(args['channel_id'])}/messages",
                            token, params={"$top": limit})
        messages = data.get("value", [])
        lines = []
        for m in messages:
            sender = m.get("from", {}).get("user", {}).get("displayName") or "—"
            when = m.get("createdDateTime", "")[:16]
            text = _strip_html(m.get("body", {}).get("content", ""))
            if not text:
                continue
            lines.append(f"[{when}] {sender}: {text[:500]}")
        if not lines:
            return "No messages found."
        return "\n".join(lines)

    elif name == "ms_list_planner_plans":
        data = await _graph("GET", "/me/planner/plans", token, params={"$select": "id,title"})
        plans = data.get("value", [])
        if not plans:
            return "No Planner plans found."
        return "\n".join(f"• {p.get('title')} (ID: {p['id']})" for p in plans)

    elif name == "ms_list_planner_tasks":
        limit = min(int(args.get("limit", 25)), 50)
        if args.get("plan_id"):
            data = await _graph("GET", f"/planner/plans/{_gid(args['plan_id'])}/tasks", token)
        else:
            data = await _graph("GET", "/me/planner/tasks", token)
        tasks = data.get("value", [])
        if not tasks:
            return "No Planner tasks found."
        lines = []
        for t in tasks[:limit]:
            pct = t.get("percentComplete", 0)
            state = "done" if pct == 100 else ("open" if pct == 0 else "in progress")
            due = (t.get("dueDateTime") or "")[:10]
            lines.append(f"• [{state}] {t.get('title')} {('due: ' + due) if due else ''} (ID: {t['id']})")
        return "\n".join(lines)

    elif name == "ms_create_planner_task":
        payload: dict = {"planId": args["plan_id"], "title": args["title"]}
        if args.get("bucket_id"):
            payload["bucketId"] = args["bucket_id"]
        if args.get("due_date"):
            payload["dueDateTime"] = f"{args['due_date']}T00:00:00Z"
        data = await _graph("POST", "/planner/tasks", token, content=json.dumps(payload))
        return f"Planner task created: '{args['title']}' (ID: {data.get('id')})"

    elif name == "ms_search_people":
        data = await _graph("GET", "/me/people", token, params={
            "$search": '"' + str(args["query"]) + '"',
            "$top": 10,
            "$select": "displayName,scoredEmailAddresses,jobTitle",
        })
        people = data.get("value", [])
        if not people:
            return f"No people found for '{args['query']}'."
        lines = []
        for p in people:
            emails = p.get("scoredEmailAddresses", [])
            email = emails[0].get("address", "") if emails else ""
            title = p.get("jobTitle", "") or ""
            lines.append(f"• {p.get('displayName', '')} <{email}>{(' — ' + title) if title else ''}")
        return "\n".join(lines)

    elif name == "ms_search":
        types = args.get("types") or ["message", "event", "driveItem", "chatMessage"]
        limit = min(int(args.get("limit", 15)), 25)
        body = {"requests": [{
            "entityTypes": types,
            "query": {"queryString": str(args["query"])},
            "size": limit,
        }]}
        data = await _graph("POST", "/search/query", token, content=json.dumps(body))
        hits = []
        for container in (data.get("value") or [{}])[0].get("hitsContainers", []) or []:
            hits.extend(container.get("hits", []) or [])
        if not hits:
            return "No results."
        lines = []
        for h in hits:
            res = h.get("resource", {}) or {}
            title = res.get("name") or res.get("subject") or res.get("displayName") or ""
            summary = _strip_html(h.get("summary", ""))[:300]
            url = res.get("webUrl", "")
            parts = [p for p in [title, summary] if p]
            line = "• " + " — ".join(parts) if parts else "• (no title)"
            if url:
                line += f"\n  URL: {url}"
            lines.append(line)
        return "\n".join(lines)

    elif name == "ms_graph_get":
        path = str(args.get("path", ""))
        # Must start with "/" + alphanumeric → blocks protocol-relative "//host",
        # backslash tricks, scheme ("://") and traversal ("..") — keeps the call
        # pinned to graph.microsoft.com.
        if not re.match(r"^/[A-Za-z0-9]", path) or "://" in path or ".." in path or "\\" in path:
            return "Error: path must be a relative Graph path like /me/messages (no scheme, no '..', no '//')."
        data = await _graph("GET", path, token, params=args.get("params") or {})
        return json.dumps(data, ensure_ascii=False)[:2000]

    elif name == "ms_list_folder":
        rel = _drive_path(args.get("path", ""))
        endpoint = f"/me/drive/root:/{rel}:/children" if rel else "/me/drive/root/children"
        data = await _graph("GET", endpoint, token,
                            params={"$select": "name,size,folder,file,lastModifiedDateTime,webUrl", "$top": 100})
        items = data.get("value", [])
        if not items:
            return "Ordner ist leer oder nicht gefunden."
        lines = []
        for it in items:
            if it.get("folder"):
                lines.append(f"[DIR ] {it.get('name')} ({it['folder'].get('childCount', '?')} Einträge)")
            else:
                lines.append(f"[FILE] {it.get('name')} ({_fmt_size(it.get('size', 0))}) — {it.get('webUrl','')}")
        return "\n".join(lines)

    elif name == "ms_read_file_content":
        rel = _drive_path(args["path"])
        if not rel:
            return "Error: path required."
        resp = await _graph_bytes("GET", f"/me/drive/root:/{rel}:/content", token)
        raw = resp.content
        if len(raw) > 2_000_000:
            return "Datei zu groß zum Lesen (>2MB)."
        try:
            return raw.decode("utf-8")[:12000]
        except UnicodeDecodeError:
            return f"Datei ist kein reiner Text ({resp.headers.get('content-type', 'binär')}) — Inhalt hier nicht extrahierbar."

    elif name == "ms_create_folder":
        parent = _drive_path(args.get("parent_path", ""))
        endpoint = f"/me/drive/root:/{parent}:/children" if parent else "/me/drive/root/children"
        body = {"name": str(args["name"]), "folder": {}, "@microsoft.graph.conflictBehavior": "rename"}
        data = await _graph("POST", endpoint, token, content=json.dumps(body))
        return f"Ordner erstellt: '{data.get('name')}' (ID: {data.get('id')})\nURL: {data.get('webUrl', '')}"

    elif name == "ms_upload_text_file":
        rel = _drive_path(args["path"])
        if not rel:
            return "Error: path required."
        resp = await _graph_bytes("PUT", f"/me/drive/root:/{rel}:/content", token,
                                  content=str(args["content"]).encode("utf-8"), content_type="text/plain")
        data = resp.json()
        return f"Datei gespeichert: '{data.get('name')}' (ID: {data.get('id')})\nURL: {data.get('webUrl', '')}"

    elif name == "ms_share_file":
        rel = _drive_path(args["path"])
        ltype = "edit" if str(args.get("link_type", "view")).lower() == "edit" else "view"
        scope = "anonymous" if str(args.get("scope", "organization")).lower() == "anonymous" else "organization"
        data = await _graph("POST", f"/me/drive/root:/{rel}:/createLink", token,
                            content=json.dumps({"type": ltype, "scope": scope}))
        link = (data.get("link") or {}).get("webUrl", "")
        return f"Freigabe-Link ({ltype}, {scope}): {link}"

    elif name == "ms_forward_email":
        recipients = [{"emailAddress": {"address": a.strip()}} for a in str(args["to"]).split(",") if a.strip()]
        body = {"comment": args.get("comment", ""), "toRecipients": recipients}
        await _graph("POST", f"/me/messages/{_gid(args['email_id'])}/forward", token, content=json.dumps(body))
        return f"E-Mail weitergeleitet an {args['to']}."

    elif name == "ms_move_email":
        dest = _folder(args["folder"])
        await _graph("POST", f"/me/messages/{_gid(args['email_id'])}/move", token,
                    content=json.dumps({"destinationId": dest}))
        return f"E-Mail nach '{dest}' verschoben."

    elif name == "ms_mark_email_read":
        read = args.get("read", True)
        read = read if isinstance(read, bool) else str(read).lower() in ("true", "1", "yes")
        await _graph("PATCH", f"/me/messages/{_gid(args['email_id'])}", token,
                    content=json.dumps({"isRead": read}))
        return f"E-Mail als {'gelesen' if read else 'ungelesen'} markiert."

    elif name == "ms_respond_event":
        r = str(args["response"]).lower()
        action = {"accept": "accept", "decline": "decline", "tentative": "tentativelyAccept"}.get(r)
        if not action:
            return "Error: response must be accept, decline or tentative."
        body: dict = {"sendResponse": True}
        if args.get("comment"):
            body["comment"] = args["comment"]
        await _graph("POST", f"/me/events/{_gid(args['event_id'])}/{action}", token, content=json.dumps(body))
        return f"Termin '{r}' beantwortet."

    elif name == "ms_cancel_event":
        try:
            body = {"comment": args["comment"]} if args.get("comment") else {}
            await _graph("POST", f"/me/events/{_gid(args['event_id'])}/cancel", token, content=json.dumps(body))
            return "Termin abgesagt (Teilnehmer benachrichtigt)."
        except RuntimeError:
            # Not the organizer → just remove it from the user's own calendar.
            await _graph("DELETE", f"/me/events/{_gid(args['event_id'])}", token)
            return "Termin aus dem Kalender entfernt."

    elif name == "ms_complete_task":
        await _graph("PATCH", f"/me/todo/lists/{_gid(args['list_id'])}/tasks/{_gid(args['task_id'])}", token,
                    content=json.dumps({"status": "completed"}))
        return "Aufgabe als erledigt markiert."

    elif name == "ms_delete_email":
        await _graph("DELETE", f"/me/messages/{_gid(args['email_id'])}", token)
        return "E-Mail gelöscht (in 'Gelöschte Elemente' verschoben)."

    elif name == "ms_update_calendar_event":
        tz = args.get("timezone", "UTC")
        payload = {}
        if args.get("subject"):
            payload["subject"] = str(args["subject"])
        if args.get("location"):
            payload["location"] = {"displayName": str(args["location"])}
        if args.get("body"):
            payload["body"] = {"contentType": "Text", "content": str(args["body"])}
        if args.get("start"):
            payload["start"] = {"dateTime": str(args["start"]), "timeZone": tz}
        if args.get("end"):
            payload["end"] = {"dateTime": str(args["end"]), "timeZone": tz}
        if not payload:
            return "Error: nothing to update — provide subject, start, end, location or body."
        await _graph("PATCH", f"/me/events/{_gid(args['event_id'])}", token, content=json.dumps(payload))
        return "Termin aktualisiert."

    elif name == "ms_update_task":
        payload = {}
        if args.get("title"):
            payload["title"] = str(args["title"])
        if args.get("status"):
            st = str(args["status"])
            payload["status"] = st if st in ("notStarted", "inProgress", "completed") else "notStarted"
        if args.get("due_date"):
            payload["dueDateTime"] = {"dateTime": f"{args['due_date']}T00:00:00", "timeZone": "UTC"}
        if not payload:
            return "Error: nothing to update — provide title, due_date or status."
        await _graph("PATCH", f"/me/todo/lists/{_gid(args['list_id'])}/tasks/{_gid(args['task_id'])}", token,
                    content=json.dumps(payload))
        return "Aufgabe aktualisiert."

    elif name == "ms_delete_task":
        await _graph("DELETE", f"/me/todo/lists/{_gid(args['list_id'])}/tasks/{_gid(args['task_id'])}", token)
        return "Aufgabe gelöscht."

    elif name == "ms_update_planner_task":
        etag = await _planner_etag(args["task_id"], token)
        if not etag:
            return "Error: Planner task not found."
        payload = {}
        if args.get("title"):
            payload["title"] = str(args["title"])
        if args.get("bucket_id"):
            payload["bucketId"] = str(args["bucket_id"])
        if args.get("due_date"):
            payload["dueDateTime"] = f"{args['due_date']}T00:00:00Z"
        if args.get("percent_complete") is not None:
            try:
                pct = int(args["percent_complete"])
            except (TypeError, ValueError):
                pct = 0
            payload["percentComplete"] = max(0, min(100, pct))
        if not payload:
            return "Error: nothing to update — provide title, due_date, percent_complete or bucket_id."
        await _graph("PATCH", f"/planner/tasks/{_gid(args['task_id'])}", token,
                    headers={"If-Match": etag}, content=json.dumps(payload))
        return "Planner-Aufgabe aktualisiert."

    elif name == "ms_delete_planner_task":
        etag = await _planner_etag(args["task_id"], token)
        if not etag:
            return "Error: Planner task not found."
        await _graph("DELETE", f"/planner/tasks/{_gid(args['task_id'])}", token,
                    headers={"If-Match": etag})
        return "Planner-Aufgabe gelöscht."

    elif name == "ms_delete_item":
        rel = _drive_path(args["path"])
        if not rel:
            return "Error: path required."
        await _graph("DELETE", f"/me/drive/root:/{rel}", token)
        return f"Gelöscht: '{args['path']}' (in den OneDrive-Papierkorb verschoben)."

    elif name == "ms_move_item":
        rel = _drive_path(args["path"])
        if not rel:
            return "Error: path required."
        payload = {}
        if args.get("new_name"):
            payload["name"] = str(args["new_name"])
        if "new_parent_path" in args:
            np = _drive_path(args.get("new_parent_path", ""))
            payload["parentReference"] = {"path": f"/drive/root:/{np}" if np else "/drive/root:"}
        if not payload:
            return "Error: provide new_name and/or new_parent_path."
        data = await _graph("PATCH", f"/me/drive/root:/{rel}", token, content=json.dumps(payload))
        return f"Verschoben/umbenannt: '{data.get('name')}'\nURL: {data.get('webUrl', '')}"

    elif name == "ms_copy_item":
        rel = _drive_path(args["path"])
        if not rel:
            return "Error: path required."
        dest_rel = _drive_path(args.get("dest_parent_path", ""))
        # Graph copy needs a parentReference with driveId + folder id — a path-only
        # reference is exactly what returns HTTP 400. Resolve both explicitly.
        drive = await _graph("GET", "/me/drive", token)
        dest = await _graph("GET", f"/me/drive/root:/{dest_rel}" if dest_rel else "/me/drive/root", token)
        body = {"parentReference": {"driveId": drive.get("id"), "id": dest.get("id")}}
        if args.get("new_name"):
            body["name"] = str(args["new_name"])
        # Copy is ASYNC → 202 Accepted, empty body + Location monitor URL. _graph
        # would choke on the empty body, so make the raw call here.
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                f"{GRAPH_BASE}/me/drive/root:/{rel}:/copy",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                content=json.dumps(body),
            )
        if resp.status_code not in (200, 202):
            logger.warning("Graph copy %s on %s: %s", resp.status_code, rel, resp.text[:300])
            return f"Kopieren fehlgeschlagen (HTTP {resp.status_code})."
        src_name = str(args["path"]).rstrip("/").split("/")[-1]
        as_name = f" als '{args['new_name']}'" if args.get("new_name") else ""
        return (f"Kopiert: '{src_name}' → '{args.get('dest_parent_path') or 'root'}'{as_name} "
                "(Graph kopiert asynchron, i.d.R. in Sekunden fertig).")

    elif name == "ms_list_contacts":
        limit = min(int(args.get("limit", 25)), 50)
        data = await _graph("GET", "/me/contacts", token, params={
            "$select": "id,displayName,emailAddresses,mobilePhone,companyName",
            "$top": limit,
        })
        contacts = data.get("value", [])
        if not contacts:
            return "No contacts found."
        lines = []
        for c in contacts:
            emails = c.get("emailAddresses", [])
            email = emails[0].get("address", "") if emails else ""
            comp = c.get("companyName", "") or ""
            lines.append(f"• {c.get('displayName', '')} <{email}>{(' — ' + comp) if comp else ''} (ID: {c['id']})")
        return "\n".join(lines)

    elif name == "ms_create_contact":
        payload = {"givenName": str(args["given_name"])}
        if args.get("surname"):
            payload["surname"] = str(args["surname"])
        if args.get("company"):
            payload["companyName"] = str(args["company"])
        if args.get("mobile"):
            payload["mobilePhone"] = str(args["mobile"])
        if args.get("email"):
            payload["emailAddresses"] = [{"address": str(args["email"]), "name": payload["givenName"]}]
        data = await _graph("POST", "/me/contacts", token, content=json.dumps(payload))
        return f"Kontakt erstellt: '{data.get('displayName')}' (ID: {data.get('id')})"

    elif name == "ms_update_contact":
        payload = {}
        if args.get("given_name"):
            payload["givenName"] = str(args["given_name"])
        if args.get("surname"):
            payload["surname"] = str(args["surname"])
        if args.get("company"):
            payload["companyName"] = str(args["company"])
        if args.get("mobile"):
            payload["mobilePhone"] = str(args["mobile"])
        if args.get("email"):
            payload["emailAddresses"] = [{"address": str(args["email"])}]
        if not payload:
            return "Error: nothing to update."
        await _graph("PATCH", f"/me/contacts/{_gid(args['contact_id'])}", token, content=json.dumps(payload))
        return "Kontakt aktualisiert."

    elif name == "ms_delete_contact":
        await _graph("DELETE", f"/me/contacts/{_gid(args['contact_id'])}", token)
        return "Kontakt gelöscht."

    else:
        return f"Unknown tool: {name}"


# ---------------------------------------------------------------------------
# JSON-RPC dispatch (shared by both transports)
# ---------------------------------------------------------------------------

async def handle_mcp_request(
    body: dict,
    resolve_token: Callable[[], Awaitable[str | None]],
    *,
    write_enabled: bool = False,
    draft_mail: bool = False,
) -> tuple[dict, int]:
    """Handle one MCP JSON-RPC message; ``resolve_token`` yields the caller's
    Microsoft access token (or None if not connected). Returns ``(json, status)``.

    ``write_enabled`` gates the write/send tools (``WRITE_TOOLS``): when False they
    are hidden from tools/list and refused in tools/call. ``draft_mail`` routes
    outbound mail to drafts instead of sending."""
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
        tools = MSGRAPH_TOOLS if write_enabled else [t for t in MSGRAPH_TOOLS if t["name"] not in WRITE_TOOLS]
        return mcp_result(id_, {"tools": tools}), 200

    if method == "tools/call":
        params = body.get("params", {})
        tool_name = params.get("name", "")
        args = params.get("arguments", {})
        # Refuse write/send tools in read-only mode BEFORE resolving any token.
        if tool_name in WRITE_TOOLS and not write_enabled:
            return mcp_result(id_, tool_result(
                "This agent has read-only Microsoft access. Enable Read+Write in the "
                "agent's Integrations settings to use write tools.",
                is_error=True,
            )), 200
        token = await resolve_token()
        if not token:
            return mcp_result(id_, tool_result(
                "Microsoft account not connected. Connect your Microsoft 365 account first, "
                "then retry.",
                is_error=True,
            )), 200
        try:
            result_text = await handle_tool(tool_name, args, token, draft_mail=draft_mail)
            return mcp_result(id_, tool_result(result_text)), 200
        except RuntimeError as e:
            return mcp_result(id_, tool_result(str(e), is_error=True)), 200
        except Exception as e:
            logger.error(f"MS Graph tool error [{tool_name}]: {e}", exc_info=True)
            return mcp_result(id_, tool_result(f"Error: {e}", is_error=True)), 200

    return mcp_error(id_, -32601, f"Method not found: {method}"), 404

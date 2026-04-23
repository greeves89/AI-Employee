#!/usr/bin/env node
/**
 * MCP MS Graph Server — Microsoft 365 access for agents.
 *
 * Fetches the user's Microsoft OAuth token from the orchestrator and calls
 * Microsoft Graph API for Mail, Calendar, Teams, Planner, To-Do, OneDrive.
 *
 * Environment:
 *   ORCHESTRATOR_URL - Base URL of the orchestrator
 *   AGENT_ID         - ID of the agent using this server
 *   AGENT_TOKEN      - HMAC token for agent auth
 */

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";

const API = `${process.env.ORCHESTRATOR_URL || "http://orchestrator:8000"}/api/v1`;
const AGENT_ID = process.env.AGENT_ID || "unknown";
const AGENT_TOKEN = process.env.AGENT_TOKEN || "";
const GRAPH = "https://graph.microsoft.com/v1.0";

let _cachedToken = null;
let _tokenExpiry = 0;

async function getToken() {
  const now = Date.now();
  if (_cachedToken && now < _tokenExpiry) return _cachedToken;

  const res = await fetch(`${API}/integrations/microsoft/for-agent`, {
    headers: {
      Authorization: `Bearer ${AGENT_TOKEN}`,
      "X-Agent-ID": AGENT_ID,
    },
  });
  if (!res.ok) throw new Error(`No Microsoft token: ${res.status}`);
  const data = await res.json();
  _cachedToken = data.token;
  _tokenExpiry = now + 4 * 60 * 1000; // 4 min cache (token valid 60 min)
  return _cachedToken;
}

async function graph(method, path, body) {
  const token = await getToken();
  const opts = {
    method,
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
  };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(`${GRAPH}${path}`, opts);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Graph ${method} ${path} → ${res.status}: ${text}`);
  }
  if (res.status === 204) return null;
  return res.json();
}

// ─── Tool definitions ────────────────────────────────────────────────────────

const TOOLS = [
  // Mail
  {
    name: "list_emails",
    description: "List recent emails from Outlook inbox. Returns sender, subject, preview, and received time.",
    inputSchema: {
      type: "object",
      properties: {
        folder: { type: "string", description: "Folder name or well-known name: inbox, sentitems, drafts, deleteditems. Default: inbox" },
        top: { type: "number", description: "Number of emails to return (1-50, default 20)" },
        filter: { type: "string", description: "OData filter expression, e.g. 'isRead eq false'" },
      },
    },
  },
  {
    name: "read_email",
    description: "Read the full content of an email by ID.",
    inputSchema: {
      type: "object",
      properties: {
        message_id: { type: "string", description: "The email message ID" },
      },
      required: ["message_id"],
    },
  },
  {
    name: "send_email",
    description: "Send an email via Outlook.",
    inputSchema: {
      type: "object",
      properties: {
        to: { type: "array", items: { type: "string" }, description: "Recipient email addresses" },
        subject: { type: "string" },
        body: { type: "string", description: "Email body (plain text or HTML)" },
        is_html: { type: "boolean", description: "Whether body is HTML (default false)" },
        cc: { type: "array", items: { type: "string" }, description: "CC addresses" },
      },
      required: ["to", "subject", "body"],
    },
  },
  {
    name: "reply_email",
    description: "Reply to an email.",
    inputSchema: {
      type: "object",
      properties: {
        message_id: { type: "string" },
        body: { type: "string" },
        reply_all: { type: "boolean", description: "Reply all (default false)" },
      },
      required: ["message_id", "body"],
    },
  },
  // Calendar
  {
    name: "list_calendar_events",
    description: "List upcoming calendar events.",
    inputSchema: {
      type: "object",
      properties: {
        start: { type: "string", description: "ISO 8601 start datetime (default: now)" },
        end: { type: "string", description: "ISO 8601 end datetime (default: 7 days from now)" },
        top: { type: "number", description: "Max events to return (default 20)" },
      },
    },
  },
  {
    name: "create_calendar_event",
    description: "Create a calendar event.",
    inputSchema: {
      type: "object",
      properties: {
        subject: { type: "string" },
        start: { type: "string", description: "ISO 8601 datetime" },
        end: { type: "string", description: "ISO 8601 datetime" },
        body: { type: "string", description: "Event description" },
        location: { type: "string" },
        attendees: { type: "array", items: { type: "string" }, description: "Attendee email addresses" },
        is_online: { type: "boolean", description: "Create as Teams meeting" },
      },
      required: ["subject", "start", "end"],
    },
  },
  {
    name: "update_calendar_event",
    description: "Update an existing calendar event.",
    inputSchema: {
      type: "object",
      properties: {
        event_id: { type: "string" },
        subject: { type: "string" },
        start: { type: "string" },
        end: { type: "string" },
        body: { type: "string" },
        location: { type: "string" },
      },
      required: ["event_id"],
    },
  },
  {
    name: "delete_calendar_event",
    description: "Delete a calendar event.",
    inputSchema: {
      type: "object",
      properties: { event_id: { type: "string" } },
      required: ["event_id"],
    },
  },
  // Teams
  {
    name: "list_teams",
    description: "List Microsoft Teams the user has joined.",
    inputSchema: { type: "object", properties: {} },
  },
  {
    name: "list_channels",
    description: "List channels in a Microsoft Team.",
    inputSchema: {
      type: "object",
      properties: { team_id: { type: "string" } },
      required: ["team_id"],
    },
  },
  {
    name: "send_teams_message",
    description: "Send a message to a Teams channel.",
    inputSchema: {
      type: "object",
      properties: {
        team_id: { type: "string" },
        channel_id: { type: "string" },
        message: { type: "string" },
      },
      required: ["team_id", "channel_id", "message"],
    },
  },
  {
    name: "list_teams_chats",
    description: "List recent Teams chats (1:1 and group).",
    inputSchema: {
      type: "object",
      properties: { top: { type: "number", description: "Max chats (default 20)" } },
    },
  },
  {
    name: "send_teams_chat_message",
    description: "Send a message in a Teams chat.",
    inputSchema: {
      type: "object",
      properties: {
        chat_id: { type: "string", description: "Chat ID from list_teams_chats" },
        message: { type: "string" },
      },
      required: ["chat_id", "message"],
    },
  },
  // Planner
  {
    name: "list_planner_plans",
    description: "List Planner plans the user owns or is a member of.",
    inputSchema: { type: "object", properties: {} },
  },
  {
    name: "list_planner_tasks",
    description: "List Planner tasks assigned to the user.",
    inputSchema: {
      type: "object",
      properties: { plan_id: { type: "string", description: "Optional: filter by plan ID" } },
    },
  },
  {
    name: "create_planner_task",
    description: "Create a new task in a Planner plan.",
    inputSchema: {
      type: "object",
      properties: {
        plan_id: { type: "string" },
        title: { type: "string" },
        bucket_id: { type: "string", description: "Optional bucket/column ID" },
        due_date: { type: "string", description: "ISO 8601 date" },
        assigned_to: { type: "array", items: { type: "string" }, description: "User IDs to assign" },
      },
      required: ["plan_id", "title"],
    },
  },
  {
    name: "update_planner_task",
    description: "Update a Planner task (e.g., mark complete).",
    inputSchema: {
      type: "object",
      properties: {
        task_id: { type: "string" },
        etag: { type: "string", description: "Required @odata.etag from get/list" },
        percent_complete: { type: "number", description: "0=not started, 50=in progress, 100=complete" },
        title: { type: "string" },
        due_date: { type: "string" },
      },
      required: ["task_id", "etag"],
    },
  },
  // To-Do
  {
    name: "list_todo_lists",
    description: "List Microsoft To-Do task lists.",
    inputSchema: { type: "object", properties: {} },
  },
  {
    name: "list_todo_tasks",
    description: "List tasks in a To-Do list.",
    inputSchema: {
      type: "object",
      properties: {
        list_id: { type: "string" },
        filter: { type: "string", description: "OData filter, e.g. 'status ne \\'completed\\''" },
      },
      required: ["list_id"],
    },
  },
  {
    name: "create_todo_task",
    description: "Create a task in a To-Do list.",
    inputSchema: {
      type: "object",
      properties: {
        list_id: { type: "string" },
        title: { type: "string" },
        body: { type: "string", description: "Task notes" },
        due_date: { type: "string", description: "ISO 8601 date" },
        importance: { type: "string", enum: ["low", "normal", "high"] },
      },
      required: ["list_id", "title"],
    },
  },
  {
    name: "complete_todo_task",
    description: "Mark a To-Do task as completed.",
    inputSchema: {
      type: "object",
      properties: {
        list_id: { type: "string" },
        task_id: { type: "string" },
      },
      required: ["list_id", "task_id"],
    },
  },
  // OneDrive
  {
    name: "list_onedrive_files",
    description: "List files and folders in OneDrive.",
    inputSchema: {
      type: "object",
      properties: {
        path: { type: "string", description: "Folder path (default: root)" },
        top: { type: "number", description: "Max items (default 50)" },
      },
    },
  },
  {
    name: "search_onedrive",
    description: "Search for files in OneDrive by name or content.",
    inputSchema: {
      type: "object",
      properties: { query: { type: "string" } },
      required: ["query"],
    },
  },
  {
    name: "read_onedrive_file",
    description: "Download and return the text content of a OneDrive file (text/plain, .md, .json, .csv, etc.).",
    inputSchema: {
      type: "object",
      properties: { item_id: { type: "string", description: "File item ID from list/search" } },
      required: ["item_id"],
    },
  },
];

// ─── Tool handlers ────────────────────────────────────────────────────────────

async function handleTool(name, args) {
  switch (name) {
    // Mail
    case "list_emails": {
      const folder = args.folder || "inbox";
      const top = Math.min(args.top || 20, 50);
      let url = `/me/mailFolders/${folder}/messages?$top=${top}&$select=id,subject,from,receivedDateTime,isRead,bodyPreview`;
      if (args.filter) url += `&$filter=${encodeURIComponent(args.filter)}`;
      url += "&$orderby=receivedDateTime desc";
      const data = await graph("GET", url);
      return data.value.map((m) => ({
        id: m.id,
        subject: m.subject,
        from: m.from?.emailAddress,
        received: m.receivedDateTime,
        isRead: m.isRead,
        preview: m.bodyPreview,
      }));
    }

    case "read_email": {
      return graph("GET", `/me/messages/${args.message_id}?$select=id,subject,from,to,cc,receivedDateTime,body`);
    }

    case "send_email": {
      const msg = {
        message: {
          subject: args.subject,
          body: { contentType: args.is_html ? "HTML" : "Text", content: args.body },
          toRecipients: args.to.map((a) => ({ emailAddress: { address: a } })),
        },
      };
      if (args.cc?.length) {
        msg.message.ccRecipients = args.cc.map((a) => ({ emailAddress: { address: a } }));
      }
      await graph("POST", "/me/sendMail", msg);
      return { status: "sent" };
    }

    case "reply_email": {
      const endpoint = args.reply_all
        ? `/me/messages/${args.message_id}/replyAll`
        : `/me/messages/${args.message_id}/reply`;
      await graph("POST", endpoint, { message: { body: { contentType: "Text", content: args.body } } });
      return { status: "replied" };
    }

    // Calendar
    case "list_calendar_events": {
      const start = args.start || new Date().toISOString();
      const end = args.end || new Date(Date.now() + 7 * 86400000).toISOString();
      const top = args.top || 20;
      const data = await graph(
        "GET",
        `/me/calendarView?startDateTime=${encodeURIComponent(start)}&endDateTime=${encodeURIComponent(end)}&$top=${top}&$select=id,subject,start,end,location,isOnlineMeeting,organizer&$orderby=start/dateTime`
      );
      return data.value;
    }

    case "create_calendar_event": {
      const body = {
        subject: args.subject,
        start: { dateTime: args.start, timeZone: "UTC" },
        end: { dateTime: args.end, timeZone: "UTC" },
      };
      if (args.body) body.body = { contentType: "Text", content: args.body };
      if (args.location) body.location = { displayName: args.location };
      if (args.attendees?.length) {
        body.attendees = args.attendees.map((e) => ({ emailAddress: { address: e }, type: "required" }));
      }
      if (args.is_online) body.isOnlineMeeting = true;
      return graph("POST", "/me/events", body);
    }

    case "update_calendar_event": {
      const patch = {};
      if (args.subject) patch.subject = args.subject;
      if (args.start) patch.start = { dateTime: args.start, timeZone: "UTC" };
      if (args.end) patch.end = { dateTime: args.end, timeZone: "UTC" };
      if (args.body) patch.body = { contentType: "Text", content: args.body };
      if (args.location) patch.location = { displayName: args.location };
      return graph("PATCH", `/me/events/${args.event_id}`, patch);
    }

    case "delete_calendar_event": {
      await graph("DELETE", `/me/events/${args.event_id}`);
      return { status: "deleted" };
    }

    // Teams
    case "list_teams": {
      const data = await graph("GET", "/me/joinedTeams?$select=id,displayName,description");
      return data.value;
    }

    case "list_channels": {
      const data = await graph("GET", `/teams/${args.team_id}/channels?$select=id,displayName,description`);
      return data.value;
    }

    case "send_teams_message": {
      const data = await graph("POST", `/teams/${args.team_id}/channels/${args.channel_id}/messages`, {
        body: { content: args.message },
      });
      return { id: data.id, status: "sent" };
    }

    case "list_teams_chats": {
      const top = args.top || 20;
      const data = await graph("GET", `/me/chats?$top=${top}&$expand=members&$select=id,topic,chatType,lastMessagePreview`);
      return data.value;
    }

    case "send_teams_chat_message": {
      const data = await graph("POST", `/me/chats/${args.chat_id}/messages`, {
        body: { content: args.message },
      });
      return { id: data.id, status: "sent" };
    }

    // Planner
    case "list_planner_plans": {
      // Get user's groups first, then their plans
      const me = await graph("GET", "/me?$select=id");
      const data = await graph("GET", `/me/planner/plans?$select=id,title,owner`);
      return data.value;
    }

    case "list_planner_tasks": {
      if (args.plan_id) {
        const data = await graph("GET", `/planner/plans/${args.plan_id}/tasks?$select=id,title,percentComplete,dueDateTime,assignments,bucketId`);
        return data.value;
      }
      const data = await graph("GET", "/me/planner/tasks?$select=id,title,percentComplete,dueDateTime,planId,bucketId");
      return data.value;
    }

    case "create_planner_task": {
      const body = { planId: args.plan_id, title: args.title };
      if (args.bucket_id) body.bucketId = args.bucket_id;
      if (args.due_date) body.dueDateTime = new Date(args.due_date).toISOString();
      if (args.assigned_to?.length) {
        body.assignments = {};
        for (const uid of args.assigned_to) {
          body.assignments[uid] = { "@odata.type": "microsoft.graph.plannerAssignment", orderHint: " !" };
        }
      }
      return graph("POST", "/planner/tasks", body);
    }

    case "update_planner_task": {
      const patch = {};
      if (args.percent_complete !== undefined) patch.percentComplete = args.percent_complete;
      if (args.title) patch.title = args.title;
      if (args.due_date) patch.dueDateTime = new Date(args.due_date).toISOString();
      const token = await getToken();
      const res = await fetch(`${GRAPH}/planner/tasks/${args.task_id}`, {
        method: "PATCH",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
          "If-Match": args.etag,
        },
        body: JSON.stringify(patch),
      });
      if (!res.ok) throw new Error(`Planner PATCH → ${res.status}: ${await res.text()}`);
      return { status: "updated" };
    }

    // To-Do
    case "list_todo_lists": {
      const data = await graph("GET", "/me/todo/lists?$select=id,displayName,isOwner,wellknownListName");
      return data.value;
    }

    case "list_todo_tasks": {
      let url = `/me/todo/lists/${args.list_id}/tasks?$select=id,title,status,importance,dueDateTime,body`;
      if (args.filter) url += `&$filter=${encodeURIComponent(args.filter)}`;
      const data = await graph("GET", url);
      return data.value;
    }

    case "create_todo_task": {
      const body = { title: args.title };
      if (args.body) body.body = { content: args.body, contentType: "text" };
      if (args.due_date) body.dueDateTime = { dateTime: args.due_date + "T00:00:00", timeZone: "UTC" };
      if (args.importance) body.importance = args.importance;
      return graph("POST", `/me/todo/lists/${args.list_id}/tasks`, body);
    }

    case "complete_todo_task": {
      return graph("PATCH", `/me/todo/lists/${args.list_id}/tasks/${args.task_id}`, {
        status: "completed",
      });
    }

    // OneDrive
    case "list_onedrive_files": {
      const top = args.top || 50;
      const path = args.path ? `/me/drive/root:/${args.path}:/children` : `/me/drive/root/children`;
      const data = await graph("GET", `${path}?$top=${top}&$select=id,name,size,lastModifiedDateTime,file,folder`);
      return data.value;
    }

    case "search_onedrive": {
      const data = await graph(
        "GET",
        `/me/drive/root/search(q='${encodeURIComponent(args.query)}')?$select=id,name,size,lastModifiedDateTime,file,webUrl`
      );
      return data.value;
    }

    case "read_onedrive_file": {
      const token = await getToken();
      const metaRes = await fetch(`${GRAPH}/me/drive/items/${args.item_id}?$select=name,size,file`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!metaRes.ok) throw new Error(`Drive meta → ${metaRes.status}`);
      const meta = await metaRes.json();
      if (!meta.file) throw new Error("Item is not a file");
      const contentRes = await fetch(`${GRAPH}/me/drive/items/${args.item_id}/content`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!contentRes.ok) throw new Error(`Drive content → ${contentRes.status}`);
      const text = await contentRes.text();
      return { name: meta.name, size: meta.size, content: text.slice(0, 50000) };
    }

    default:
      throw new Error(`Unknown tool: ${name}`);
  }
}

// ─── MCP Server setup ─────────────────────────────────────────────────────────

const server = new Server(
  { name: "msgraph", version: "1.0.0" },
  { capabilities: { tools: {} } }
);

server.setRequestHandler(ListToolsRequestSchema, async () => ({ tools: TOOLS }));

server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const { name, arguments: args } = request.params;
  try {
    const result = await handleTool(name, args || {});
    return {
      content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
    };
  } catch (err) {
    return {
      content: [{ type: "text", text: `Error: ${err.message}` }],
      isError: true,
    };
  }
});

const transport = new StdioServerTransport();
await server.connect(transport);

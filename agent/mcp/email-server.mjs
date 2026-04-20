#!/usr/bin/env node
/**
 * MCP Email Server — Gmail + Outlook access for agents.
 *
 * Fetches OAuth tokens from the orchestrator (user-connected integrations)
 * and calls Gmail API or Microsoft Graph depending on what's connected.
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

async function orchestratorCall(path, options = {}) {
  const res = await fetch(`${API}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${AGENT_TOKEN}`,
      "X-Agent-ID": AGENT_ID,
      ...(options.headers || {}),
    },
  });
  if (!res.ok) throw new Error(`API ${res.status}: ${await res.text()}`);
  return res.json();
}

// Resolve which email provider is connected and return its token + type
async function resolveProvider() {
  // Try Google first, then Microsoft
  for (const provider of ["google", "microsoft"]) {
    try {
      const data = await orchestratorCall(`/integrations/${provider}/for-agent`);
      if (data.token) return { token: data.token, provider };
    } catch {
      // Not connected — try next
    }
  }
  throw new Error(
    "No email integration connected. Ask the user to connect Google or Microsoft in Settings → Integrations."
  );
}

// --- Gmail API helpers ---
async function gmailCall(path, token, options = {}) {
  const res = await fetch(`https://gmail.googleapis.com/gmail/v1/users/me${path}`, {
    ...options,
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json", ...(options.headers || {}) },
  });
  if (!res.ok) throw new Error(`Gmail API ${res.status}: ${await res.text()}`);
  return res.json();
}

function gmailDecodeBody(payload) {
  // Recursively find text/plain or text/html body
  if (!payload) return "";
  if (payload.body?.data) {
    return Buffer.from(payload.body.data, "base64url").toString("utf-8");
  }
  if (payload.parts) {
    const plain = payload.parts.find((p) => p.mimeType === "text/plain");
    const html = payload.parts.find((p) => p.mimeType === "text/html");
    const part = plain || html;
    if (part?.body?.data) return Buffer.from(part.body.data, "base64url").toString("utf-8");
    // Nested multipart
    for (const p of payload.parts) {
      const nested = gmailDecodeBody(p);
      if (nested) return nested;
    }
  }
  return "";
}

function gmailHeader(headers, name) {
  return headers?.find((h) => h.name.toLowerCase() === name.toLowerCase())?.value || "";
}

function gmailMakeRaw(to, subject, body, replyToMsgId, threadId) {
  const headers = [
    `To: ${to}`,
    `Subject: ${subject}`,
    "MIME-Version: 1.0",
    "Content-Type: text/plain; charset=utf-8",
  ];
  if (replyToMsgId) headers.push(`In-Reply-To: ${replyToMsgId}`, `References: ${replyToMsgId}`);
  const raw = headers.join("\r\n") + "\r\n\r\n" + body;
  return Buffer.from(raw).toString("base64url");
}

// --- Microsoft Graph API helpers ---
async function msCall(path, token, options = {}) {
  const res = await fetch(`https://graph.microsoft.com/v1.0/me${path}`, {
    ...options,
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json", ...(options.headers || {}) },
  });
  if (!res.ok) throw new Error(`Graph API ${res.status}: ${await res.text()}`);
  if (res.status === 204) return {};
  return res.json();
}

// --- Tool definitions ---
const server = new Server(
  { name: "email", version: "1.0.0" },
  { capabilities: { tools: {} } }
);

server.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: [
    {
      name: "email_list",
      description: "List recent emails from the connected inbox (Gmail or Outlook). Returns sender, subject, date, and snippet.",
      inputSchema: {
        type: "object",
        properties: {
          max_results: { type: "number", description: "Max emails to return (default: 10, max: 50)." },
          filter: { type: "string", description: "Filter: 'unread', 'starred', or a search query like 'from:boss@company.com'." },
        },
      },
    },
    {
      name: "email_read",
      description: "Read the full content of an email by its ID.",
      inputSchema: {
        type: "object",
        properties: {
          message_id: { type: "string", description: "The email ID from email_list." },
        },
        required: ["message_id"],
      },
    },
    {
      name: "email_send",
      description: "Send a new email.",
      inputSchema: {
        type: "object",
        properties: {
          to: { type: "string", description: "Recipient email address." },
          subject: { type: "string", description: "Email subject." },
          body: { type: "string", description: "Plain text email body." },
        },
        required: ["to", "subject", "body"],
      },
    },
    {
      name: "email_reply",
      description: "Reply to an existing email thread.",
      inputSchema: {
        type: "object",
        properties: {
          message_id: { type: "string", description: "The ID of the message to reply to." },
          body: { type: "string", description: "Reply text." },
        },
        required: ["message_id", "body"],
      },
    },
    {
      name: "email_search",
      description: "Search emails with a query (e.g. 'invoice from:finance@company.com after:2024/01/01').",
      inputSchema: {
        type: "object",
        properties: {
          query: { type: "string", description: "Search query." },
          max_results: { type: "number", description: "Max results (default: 10)." },
        },
        required: ["query"],
      },
    },
    {
      name: "email_mark_read",
      description: "Mark an email as read.",
      inputSchema: {
        type: "object",
        properties: {
          message_id: { type: "string", description: "The email ID to mark as read." },
        },
        required: ["message_id"],
      },
    },
  ],
}));

server.setRequestHandler(CallToolRequestSchema, async (req) => {
  const { name, arguments: args } = req.params;

  let provider, token;
  try {
    ({ provider, token } = await resolveProvider());
  } catch (e) {
    return { content: [{ type: "text", text: `Error: ${e.message}` }], isError: true };
  }

  try {
    if (provider === "google") {
      return await handleGmail(name, args, token);
    } else {
      return await handleOutlook(name, args, token);
    }
  } catch (e) {
    return { content: [{ type: "text", text: `Error: ${e.message}` }], isError: true };
  }
});

async function handleGmail(name, args, token) {
  const limit = Math.min(args?.max_results || 10, 50);

  if (name === "email_list" || name === "email_search") {
    let q = args?.filter || args?.query || "";
    if (args?.filter === "unread") q = "is:unread";
    else if (args?.filter === "starred") q = "is:starred";

    const params = new URLSearchParams({ maxResults: limit });
    if (q) params.set("q", q);
    const list = await gmailCall(`/messages?${params}`, token);
    if (!list.messages?.length) return { content: [{ type: "text", text: "No emails found." }] };

    const previews = await Promise.all(
      list.messages.slice(0, limit).map((m) =>
        gmailCall(`/messages/${m.id}?format=metadata&metadataHeaders=From&metadataHeaders=Subject&metadataHeaders=Date`, token)
      )
    );
    const lines = previews.map((m) => {
      const h = m.payload?.headers || [];
      return `ID: ${m.id} | From: ${gmailHeader(h, "From")} | Subject: ${gmailHeader(h, "Subject")} | Date: ${gmailHeader(h, "Date")}\nSnippet: ${m.snippet || ""}`;
    });
    return { content: [{ type: "text", text: `${lines.length} emails:\n\n${lines.join("\n\n---\n\n")}` }] };
  }

  if (name === "email_read") {
    const msg = await gmailCall(`/messages/${args.message_id}?format=full`, token);
    const h = msg.payload?.headers || [];
    const body = gmailDecodeBody(msg.payload);
    return {
      content: [{
        type: "text",
        text: `From: ${gmailHeader(h, "From")}\nTo: ${gmailHeader(h, "To")}\nSubject: ${gmailHeader(h, "Subject")}\nDate: ${gmailHeader(h, "Date")}\n\n${body}`,
      }],
    };
  }

  if (name === "email_send") {
    const raw = gmailMakeRaw(args.to, args.subject, args.body);
    await gmailCall("/messages/send", token, { method: "POST", body: JSON.stringify({ raw }) });
    return { content: [{ type: "text", text: `Email sent to ${args.to}.` }] };
  }

  if (name === "email_reply") {
    const orig = await gmailCall(`/messages/${args.message_id}?format=metadata&metadataHeaders=Subject&metadataHeaders=From&metadataHeaders=Message-ID`, token);
    const h = orig.payload?.headers || [];
    const origSubject = gmailHeader(h, "Subject");
    const origFrom = gmailHeader(h, "From");
    const origMsgId = gmailHeader(h, "Message-ID");
    const subject = origSubject.startsWith("Re:") ? origSubject : `Re: ${origSubject}`;
    const raw = gmailMakeRaw(origFrom, subject, args.body, origMsgId);
    await gmailCall("/messages/send", token, {
      method: "POST",
      body: JSON.stringify({ raw, threadId: orig.threadId }),
    });
    return { content: [{ type: "text", text: "Reply sent." }] };
  }

  if (name === "email_mark_read") {
    await gmailCall(`/messages/${args.message_id}/modify`, token, {
      method: "POST",
      body: JSON.stringify({ removeLabelIds: ["UNREAD"] }),
    });
    return { content: [{ type: "text", text: `Email ${args.message_id} marked as read.` }] };
  }

  return { content: [{ type: "text", text: `Unknown tool: ${name}` }], isError: true };
}

async function handleOutlook(name, args, token) {
  const limit = Math.min(args?.max_results || 10, 50);

  if (name === "email_list") {
    let filter = "";
    if (args?.filter === "unread") filter = "isRead eq false";
    else if (args?.filter === "starred") filter = "flag/flagStatus eq 'flagged'";
    const params = new URLSearchParams({ $top: limit, $select: "id,from,subject,receivedDateTime,bodyPreview,isRead" });
    if (filter) params.set("$filter", filter);
    const data = await msCall(`/messages?${params}`, token);
    if (!data.value?.length) return { content: [{ type: "text", text: "No emails found." }] };
    const lines = data.value.map((m) =>
      `ID: ${m.id}\nFrom: ${m.from?.emailAddress?.address} | Subject: ${m.subject} | Date: ${m.receivedDateTime}\nSnippet: ${m.bodyPreview}`
    );
    return { content: [{ type: "text", text: `${lines.length} emails:\n\n${lines.join("\n\n---\n\n")}` }] };
  }

  if (name === "email_search") {
    const params = new URLSearchParams({ $search: `"${args.query}"`, $top: limit, $select: "id,from,subject,receivedDateTime,bodyPreview" });
    const data = await msCall(`/messages?${params}`, token);
    if (!data.value?.length) return { content: [{ type: "text", text: "No results." }] };
    const lines = data.value.map((m) =>
      `ID: ${m.id}\nFrom: ${m.from?.emailAddress?.address} | Subject: ${m.subject} | Date: ${m.receivedDateTime}\nSnippet: ${m.bodyPreview}`
    );
    return { content: [{ type: "text", text: `${lines.length} results:\n\n${lines.join("\n\n---\n\n")}` }] };
  }

  if (name === "email_read") {
    const m = await msCall(`/messages/${args.message_id}?$select=from,toRecipients,subject,receivedDateTime,body`, token);
    const body = m.body?.contentType === "html"
      ? m.body.content.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim()
      : m.body?.content || "";
    return {
      content: [{
        type: "text",
        text: `From: ${m.from?.emailAddress?.address}\nTo: ${m.toRecipients?.map((r) => r.emailAddress?.address).join(", ")}\nSubject: ${m.subject}\nDate: ${m.receivedDateTime}\n\n${body}`,
      }],
    };
  }

  if (name === "email_send") {
    await msCall("/sendMail", token, {
      method: "POST",
      body: JSON.stringify({
        message: {
          subject: args.subject,
          body: { contentType: "Text", content: args.body },
          toRecipients: [{ emailAddress: { address: args.to } }],
        },
      }),
    });
    return { content: [{ type: "text", text: `Email sent to ${args.to}.` }] };
  }

  if (name === "email_reply") {
    await msCall(`/messages/${args.message_id}/reply`, token, {
      method: "POST",
      body: JSON.stringify({ comment: args.body }),
    });
    return { content: [{ type: "text", text: "Reply sent." }] };
  }

  if (name === "email_mark_read") {
    await msCall(`/messages/${args.message_id}`, token, {
      method: "PATCH",
      body: JSON.stringify({ isRead: true }),
    });
    return { content: [{ type: "text", text: `Email ${args.message_id} marked as read.` }] };
  }

  return { content: [{ type: "text", text: `Unknown tool: ${name}` }], isError: true };
}

const transport = new StdioServerTransport();
await server.connect(transport);

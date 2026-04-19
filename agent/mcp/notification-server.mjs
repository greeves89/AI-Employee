#!/usr/bin/env node
/**
 * MCP Notification Server - Send notifications and request approvals.
 *
 * Allows agents to notify the user (via Web UI, Telegram) and request
 * approval for critical actions. Can be used with any MCP client.
 *
 * Environment:
 *   ORCHESTRATOR_URL - Base URL of the orchestrator (default: http://orchestrator:8000)
 *   AGENT_ID         - ID of the agent using this server
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

function wrapData(source, content) {
  return `[EXTERNAL-DATA source="${source}"]\n${content}\n[/EXTERNAL-DATA]`;
}

async function apiCall(path, options = {}) {
  const url = `${API}${path}`;
  const res = await fetch(url, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "Authorization": `Bearer ${AGENT_TOKEN}`,
      "X-Agent-ID": AGENT_ID,
      ...(options.headers || {}),
    },
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`API ${res.status}: ${text}`);
  }
  return res.json();
}

const server = new Server(
  { name: "mcp-notifications", version: "1.0.0" },
  { capabilities: { tools: {} } }
);

// --- List available tools ---
server.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: [
    {
      name: "notify_user",
      description:
        "Send a notification to the user. The notification appears in the Web UI " +
        "notification center. High/urgent priority notifications are also forwarded to Telegram. " +
        "Use this when you complete tasks, encounter errors, or have important updates.",
      inputSchema: {
        type: "object",
        properties: {
          title: {
            type: "string",
            description: "Short notification title (e.g. 'Task completed', 'Error in deployment').",
          },
          message: {
            type: "string",
            description: "Detailed notification message. Can be multi-line.",
          },
          priority: {
            type: "string",
            enum: ["low", "normal", "high", "urgent"],
            description:
              "Notification priority. low=informational, normal=standard, " +
              "high=sent to Telegram too, urgent=Telegram + flashing badge. Default: normal.",
          },
          type: {
            type: "string",
            enum: ["info", "warning", "error", "success"],
            description:
              "Notification type for visual styling. info=blue, warning=amber, " +
              "error=red, success=green. Default: info.",
          },
        },
        required: ["title"],
      },
    },
    {
      name: "send_telegram",
      description:
        "Send a direct message to the user via Telegram. Use this for live progress updates, " +
        "intermediate results, and status messages DURING work. Unlike notify_user (which goes " +
        "to the notification center), this goes DIRECTLY to Telegram as a chat message. " +
        "Use this frequently to keep the user informed about what you're doing: " +
        "e.g. 'Step 1/3 done: Database schema created', 'Building frontend...', " +
        "'Found 3 issues, fixing now'. The user expects regular updates!",
      inputSchema: {
        type: "object",
        properties: {
          message: {
            type: "string",
            description:
              "The message to send via Telegram. Supports basic formatting. " +
              "Keep it concise but informative. Use emojis for visual structure.",
          },
        },
        required: ["message"],
      },
    },
    {
      name: "request_approval",
      description:
        "Request explicit user approval before taking a critical action. " +
        "This creates a special notification with clickable options in the UI. " +
        "ALWAYS use this before: sending emails, deleting files, making purchases, " +
        "calling external APIs with side effects, or any irreversible action. " +
        "The approval notification is sent with high priority (Telegram included).",
      inputSchema: {
        type: "object",
        properties: {
          question: {
            type: "string",
            description:
              "The question to ask the user (e.g. 'Shall I send this email to john@example.com?').",
          },
          options: {
            type: "array",
            items: { type: "string" },
            minItems: 2,
            maxItems: 5,
            description:
              "The options to present to the user (e.g. ['Send now', 'Edit first', 'Cancel']). " +
              "First option is visually highlighted as the primary action.",
          },
          context: {
            type: "string",
            description:
              "Additional context to help the user decide (e.g. email body preview, file list, cost estimate).",
          },
        },
        required: ["question", "options"],
      },
    },
  ],
}));

// --- Handle tool calls ---
server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const { name, arguments: args } = request.params;

  switch (name) {
    case "notify_user": {
      const result = await apiCall("/notifications/", {
        method: "POST",
        body: JSON.stringify({
          agent_id: AGENT_ID,
          type: args.type || "info",
          title: args.title,
          message: args.message || "",
          priority: args.priority || "normal",
        }),
      });
      return {
        content: [
          {
            type: "text",
            text: `Notification sent (id: ${result.id}, priority: ${result.priority}). ` +
              (result.priority === "high" || result.priority === "urgent"
                ? "Also forwarded to Telegram."
                : "Visible in Web UI."),
          },
        ],
      };
    }

    case "send_telegram": {
      const result = await apiCall(`/agents/${AGENT_ID}/telegram/send`, {
        method: "POST",
        body: JSON.stringify({
          message: args.message,
        }),
      });
      return {
        content: [
          {
            type: "text",
            text: result.sent_to > 0
              ? `Telegram message sent to ${result.sent_to} user(s).`
              : "No authorized Telegram users found. Message not delivered.",
          },
        ],
      };
    }

    case "request_approval": {
      const result = await apiCall("/notifications/", {
        method: "POST",
        body: JSON.stringify({
          agent_id: AGENT_ID,
          type: "approval",
          title: args.question,
          message: args.context || "",
          priority: "high",
          meta: { options: args.options },
        }),
      });

      // Poll for user response — up to 5 minutes
      const startTime = Date.now();
      const maxWait = 5 * 60 * 1000; // 5 minutes
      let userChoice = null;
      while (Date.now() - startTime < maxWait) {
        await new Promise(r => setTimeout(r, 3000));
        try {
          const poll = await apiCall(`/notifications/${result.id}/result`);
          if (poll.status === "responded" && poll.choice) {
            userChoice = poll.choice;
            break;
          }
        } catch (e) {
          // continue polling
        }
      }

      if (userChoice) {
        return {
          content: [{
            type: "text",
            text: `User responded. ${wrapData("user-approval", `choice: "${userChoice}"`)} Proceed according to the user's choice above.`,
          }],
        };
      } else {
        return {
          content: [{
            type: "text",
            text: `User did not respond within 5 minutes. Do NOT proceed with the action. Ask again later or abort the task.`,
          }],
        };
      }
    }

    default:
      throw new Error(`Unknown tool: ${name}`);
  }
});

// --- Start ---
const transport = new StdioServerTransport();
await server.connect(transport);

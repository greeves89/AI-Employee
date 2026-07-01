#!/usr/bin/env node
/**
 * MCP Read-Logs Server - lets an agent read its own container logs to diagnose
 * and improve itself.
 *
 * The orchestrator is the only component with docker access; this tool never
 * touches the docker socket. Scope is enforced server-side: an agent reads its
 * OWN logs, and a team lead may additionally read its team members' logs. Output
 * is secret-redacted and every read is audit-logged by the orchestrator.
 *
 * Environment:
 *   ORCHESTRATOR_URL - Base URL of the orchestrator (default: http://orchestrator:8000)
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

// Wraps externally-sourced content so the model treats logs as data, not
// instructions (logs can contain attacker-controlled strings).
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
  { name: "mcp-read-logs", version: "1.0.0" },
  { capabilities: { tools: {} } }
);

// --- List available tools ---
server.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: [
    {
      name: "read_logs",
      description:
        "Read the recent container logs of yourself (or, if you are a team lead, " +
        "a member of your team) to diagnose failures and improve your own setup. " +
        "Secrets are redacted and every read is audit-logged. " +
        "Typical use: after a task or tool call fails, read the last lines to see " +
        "the real error (e.g. a 401, a stack trace, a missing env var) and then " +
        "open a GitHub issue or PR to fix it.",
      inputSchema: {
        type: "object",
        properties: {
          target_agent_id: {
            type: "string",
            description:
              "Optional. Whose logs to read. Defaults to yourself. Only a team " +
              "lead may pass a team member's agent id; anything else is rejected.",
          },
          tail: {
            type: "number",
            minimum: 1,
            maximum: 1000,
            description: "How many trailing log lines to return (default 200, max 1000).",
          },
          since_minutes: {
            type: "number",
            minimum: 1,
            maximum: 1440,
            description:
              "Optional. Only return log lines from the last N minutes (max 1440 = 24h).",
          },
        },
      },
    },
  ],
}));

// --- Handle tool calls ---
server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const { name, arguments: args } = request.params;

  switch (name) {
    case "read_logs": {
      const params = new URLSearchParams();
      if (args.target_agent_id) params.set("target_agent_id", args.target_agent_id);
      params.set("tail", String(args.tail || 200));
      if (args.since_minutes) params.set("since_minutes", String(args.since_minutes));

      const result = await apiCall(`/agents/logs?${params}`);
      const logs = (result.logs || "").trim();
      const who = result.agent_id === AGENT_ID ? "your own container" : `agent ${result.agent_id}`;
      if (!logs) {
        return {
          content: [{ type: "text", text: `No recent log lines for ${who}.` }],
        };
      }
      return {
        content: [
          {
            type: "text",
            text: `Logs for ${who} (last ${result.tail} lines, secrets redacted):\n\n${wrapData("container-logs", logs)}`,
          },
        ],
      };
    }

    default:
      throw new Error(`Unknown tool: ${name}`);
  }
});

// --- Start ---
const transport = new StdioServerTransport();
await server.connect(transport);

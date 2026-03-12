#!/usr/bin/env node
/**
 * MCP Knowledge Server - Shared knowledge base for all agents.
 *
 * Provides agents with read/write access to a central, Obsidian-style
 * knowledge base with [[backlinks]] and #tags. All agents share the
 * same knowledge pool.
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
  { name: "mcp-knowledge", version: "1.0.0" },
  { capabilities: { tools: {} } }
);

// --- List available tools ---
server.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: [
    {
      name: "knowledge_write",
      description:
        "Write to the shared knowledge base. Creates or updates an entry by title. " +
        "ALL agents share this knowledge base — use it for company-wide information, " +
        "project documentation, decisions, processes, contacts, and learnings. " +
        "Use [[Title]] syntax to link between entries. Use #tags for categorization. " +
        "If an entry with the same title exists, it will be updated.",
      inputSchema: {
        type: "object",
        properties: {
          title: {
            type: "string",
            description:
              "Title of the knowledge entry (e.g. 'Company Brand Guidelines', " +
              "'Project Alpha Architecture', 'Meeting Notes 2025-01-15'). " +
              "Use clear, descriptive titles. This is the link target for [[backlinks]].",
          },
          content: {
            type: "string",
            description:
              "Markdown content. Use [[Title]] to link to other entries. " +
              "Use #tags inline for categorization. " +
              "Example: 'The [[Brand Guidelines]] define our voice. See also [[Target Audience]]. #marketing #brand'",
          },
          tags: {
            type: "array",
            items: { type: "string" },
            description:
              "Tags for categorization (e.g. ['project', 'decision', 'contact']). " +
              "Tags from #hashtags in content are also extracted automatically.",
          },
        },
        required: ["title", "content"],
      },
    },
    {
      name: "knowledge_search",
      description:
        "Search the shared knowledge base by keyword and/or tag. " +
        "Use this to find existing company knowledge before creating new entries. " +
        "Returns matching entries with their content, tags, and backlinks.",
      inputSchema: {
        type: "object",
        properties: {
          query: {
            type: "string",
            description: "Search term to find in titles and content. Leave empty to list recent entries.",
          },
          tag: {
            type: "string",
            description: "Filter by tag (e.g. 'project', 'decision', 'contact').",
          },
        },
      },
    },
    {
      name: "knowledge_read",
      description:
        "Read a specific knowledge entry by its exact title. " +
        "Use this when you know the title (e.g. from a [[backlink]] reference).",
      inputSchema: {
        type: "object",
        properties: {
          title: {
            type: "string",
            description: "Exact title of the knowledge entry to read.",
          },
        },
        required: ["title"],
      },
    },
  ],
}));

// --- Handle tool calls ---
server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const { name, arguments: args } = request.params;

  switch (name) {
    case "knowledge_write": {
      const result = await apiCall("/knowledge/agent/write", {
        method: "POST",
        body: JSON.stringify({
          title: args.title,
          content: args.content,
          tags: args.tags || [],
        }),
      });
      const backlinks = result.backlinks || [];
      return {
        content: [
          {
            type: "text",
            text:
              `Knowledge entry saved: "${result.title}" (id: ${result.id}, ` +
              `tags: [${(result.tags || []).join(", ")}]` +
              (backlinks.length > 0 ? `, links to: [${backlinks.join(", ")}]` : "") +
              `). All agents can now access this.`,
          },
        ],
      };
    }

    case "knowledge_search": {
      const params = new URLSearchParams();
      if (args.query) params.set("q", args.query);
      if (args.tag) params.set("tag", args.tag);
      const qs = params.toString() ? `?${params}` : "";

      const result = await apiCall(`/knowledge/agent/search${qs}`);
      if (!result.entries || result.entries.length === 0) {
        return {
          content: [{ type: "text", text: "No knowledge entries found." }],
        };
      }
      const lines = result.entries.map(
        (e) =>
          `**${e.title}** (id:${e.id}, tags:[${(e.tags || []).join(",")}], updated:${e.updated_at})\n` +
          `${e.content.substring(0, 300)}${e.content.length > 300 ? "..." : ""}`
      );
      return {
        content: [
          {
            type: "text",
            text: `Found ${result.total} entries:\n\n${lines.join("\n\n---\n\n")}`,
          },
        ],
      };
    }

    case "knowledge_read": {
      try {
        const result = await apiCall(`/knowledge/agent/read/${encodeURIComponent(args.title)}`);
        const backlinks = result.backlinks || [];
        return {
          content: [
            {
              type: "text",
              text:
                `# ${result.title}\n` +
                `Tags: [${(result.tags || []).join(", ")}] | ` +
                `Created by: ${result.created_by} | Updated: ${result.updated_at}\n` +
                (backlinks.length > 0 ? `Links to: [${backlinks.join(", ")}]\n` : "") +
                `\n${result.content}`,
            },
          ],
        };
      } catch (e) {
        return {
          content: [
            {
              type: "text",
              text: `Knowledge entry "${args.title}" not found. Use knowledge_search to find entries.`,
            },
          ],
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

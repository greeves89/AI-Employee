#!/usr/bin/env node
/**
 * Second Brain MCP Server
 * Gives agents access to the user's unified Second Brain:
 *   brain_search   — semantic search across all knowledge of this user's agents
 *   brain_contribute — add/update a node in the user's Second Brain
 */

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";

const ORCHESTRATOR_URL = process.env.ORCHESTRATOR_URL || "http://ai-employee-orchestrator:8000";
const AGENT_ID = process.env.AGENT_ID || "";
const AGENT_TOKEN = process.env.AGENT_TOKEN || "";

const server = new Server(
  { name: "mcp-brain", version: "1.0.0" },
  { capabilities: { tools: {} } }
);

async function apiCall(path, options = {}) {
  const url = `${ORCHESTRATOR_URL}/api/v1${path}`;
  const res = await fetch(url, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-Agent-ID": AGENT_ID,
      Authorization: `Bearer ${AGENT_TOKEN}`,
      ...(options.headers || {}),
    },
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Brain API ${path} → ${res.status}: ${text}`);
  }
  return res.json();
}

server.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: [
    {
      name: "brain_search",
      description:
        "Search the user's Second Brain — the unified semantic knowledge graph shared by all agents of this user. " +
        "Returns relevant knowledge entries (and optionally agent memories) ranked by semantic similarity. " +
        "Use this BEFORE starting any task to load relevant context from the shared brain.",
      inputSchema: {
        type: "object",
        properties: {
          q: {
            type: "string",
            description: "Search query — what you're looking for.",
          },
          limit: {
            type: "number",
            description: "Max results to return (default 10, max 50).",
          },
          include_memories: {
            type: "boolean",
            description:
              "Also search agent memories across all user's agents (default false). " +
              "Set true when you want cross-agent memory context.",
          },
        },
        required: ["q"],
      },
    },
    {
      name: "brain_contribute",
      description:
        "Add or update a node in the user's Second Brain. " +
        "Use this to share important learnings, decisions, research, or context with all other agents of this user. " +
        "The entry is automatically linked to semantically related entries in the brain.",
      inputSchema: {
        type: "object",
        properties: {
          title: {
            type: "string",
            description: "Unique title for this brain node (used as ID for upsert).",
          },
          content: {
            type: "string",
            description: "Markdown content. Use [[Other Title]] for explicit links.",
          },
          tags: {
            type: "array",
            items: { type: "string" },
            description: "Tags for categorization (e.g. ['trading', 'decision', 'research']).",
          },
        },
        required: ["title", "content"],
      },
    },
  ],
}));

server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const { name, arguments: args } = request.params;

  try {
    if (name === "brain_search") {
      const params = new URLSearchParams({ q: args.q || "", limit: args.limit || 10 });
      if (args.include_memories) params.set("include_memories", "true");

      const result = await apiCall(`/brain/agent/search?${params}`);

      const entries = result.entries || [];
      const memories = result.memories || [];

      let text = `[SECOND BRAIN — ${result.mode || "search"} | ${entries.length} entries`;
      if (memories.length) text += ` + ${memories.length} memories`;
      text += "]\n\n";

      for (const e of entries) {
        text += `### ${e.title}`;
        if (e.similarity !== undefined) text += ` (similarity: ${e.similarity})`;
        text += "\n";
        if (e.tags?.length) text += `Tags: ${e.tags.join(", ")}\n`;
        text += `${e.content?.slice(0, 600) || ""}`;
        if ((e.content?.length || 0) > 600) text += "…";
        text += "\n\n";
      }

      if (memories.length) {
        text += "---\n### Related Memories (cross-agent)\n\n";
        for (const m of memories) {
          text += `**[${m.agent_id}/${m.category}]** ${m.key}: ${m.content?.slice(0, 300) || ""}`;
          if (m.similarity !== undefined) text += ` (sim: ${m.similarity})`;
          text += "\n";
        }
      }

      if (!entries.length && !memories.length) {
        text += "No results found in the Second Brain.";
      }

      return { content: [{ type: "text", text }] };
    }

    if (name === "brain_contribute") {
      const result = await apiCall("/brain/agent/contribute", {
        method: "POST",
        body: JSON.stringify({
          title: args.title,
          content: args.content,
          tags: args.tags || [],
        }),
      });

      return {
        content: [
          {
            type: "text",
            text: `✅ Brain node saved: "${result.title}" (id: ${result.id}). Auto-linking in progress.`,
          },
        ],
      };
    }

    throw new Error(`Unknown tool: ${name}`);
  } catch (err) {
    return {
      content: [{ type: "text", text: `Error: ${err.message}` }],
      isError: true,
    };
  }
});

const transport = new StdioServerTransport();
await server.connect(transport);

#!/usr/bin/env node
/**
 * MCP Memory Server - Long-term memory for AI agents.
 *
 * Provides persistent, categorized memory storage via the orchestrator API.
 * Can be used standalone with any MCP client (Claude Code, Claude Desktop, etc.)
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
  { name: "mcp-memory", version: "1.0.0" },
  { capabilities: { tools: {} } }
);

// --- List available tools ---
server.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: [
    {
      name: "memory_save",
      description:
        "Save something to long-term memory. Use this to remember important information " +
        "across conversations: user preferences, contacts, project details, procedures, " +
        "decisions, facts, and learnings. If a memory with the same key exists, it will be updated.",
      inputSchema: {
        type: "object",
        properties: {
          category: {
            type: "string",
            enum: ["preference", "contact", "project", "procedure", "decision", "fact", "learning"],
            description:
              "Category of the memory. " +
              "preference: How the user likes things done. " +
              "contact: People, their roles, contact info. " +
              "project: Project names, tech stacks, deadlines. " +
              "procedure: Step-by-step processes for recurring tasks. " +
              "decision: Important decisions and their rationale. " +
              "fact: Company info, URLs, account numbers, etc. " +
              "learning: Lessons learned from mistakes or corrections.",
          },
          key: {
            type: "string",
            description:
              "Short identifier for this memory (e.g. 'email_style', 'max_mueller', 'project_alpha'). " +
              "Use snake_case. If a memory with this key already exists, it will be updated.",
          },
          content: {
            type: "string",
            description: "The actual content to remember. Be specific and actionable.",
          },
          importance: {
            type: "number",
            minimum: 1,
            maximum: 5,
            description:
              "How important is this memory? 1=trivial, 3=normal, 5=critical. " +
              "Higher importance memories are returned first in searches. Default: 3.",
          },
        },
        required: ["category", "key", "content"],
      },
    },
    {
      name: "memory_search",
      description:
        "Search long-term memory using SEMANTIC search (vector embeddings) with automatic " +
        "keyword fallback. Use this at the start of conversations and before tasks to recall context. " +
        "\n\n" +
        "**Write queries in natural language** — the system understands meaning, not just keywords. " +
        "Examples: 'what does the user prefer for emails?', 'did we discuss tax deadlines?', " +
        "'any previous decisions about the API design?'. " +
        "\n\n" +
        "Results are ranked by semantic similarity (each result shows match score %). " +
        "If semantic search is unavailable (no OpenAI API key), automatically falls back to " +
        "keyword search — the response will tell you which mode was used.",
      inputSchema: {
        type: "object",
        properties: {
          query: {
            type: "string",
            description:
              "Natural-language question or topic to search for. Be descriptive — the system " +
              "understands meaning, not just exact words. E.g. 'how should I format emails to the user?' " +
              "works better than just 'email'. Leave empty to list recent memories.",
          },
          category: {
            type: "string",
            enum: ["preference", "contact", "project", "procedure", "decision", "fact", "learning"],
            description:
              "Optional: filter by category. NOTE: providing a category forces keyword-only search " +
              "(no semantic ranking). Omit for best semantic results.",
          },
        },
      },
    },
    {
      name: "memory_list",
      description:
        "List all memories, optionally filtered by category. Returns memories sorted by importance.",
      inputSchema: {
        type: "object",
        properties: {
          category: {
            type: "string",
            enum: ["preference", "contact", "project", "procedure", "decision", "fact", "learning"],
            description: "Optional: filter by category.",
          },
        },
      },
    },
    {
      name: "memory_delete",
      description: "Delete a specific memory by its ID.",
      inputSchema: {
        type: "object",
        properties: {
          memory_id: {
            type: "number",
            description: "The numeric ID of the memory to delete.",
          },
        },
        required: ["memory_id"],
      },
    },
  ],
}));

// --- Handle tool calls ---
server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const { name, arguments: args } = request.params;

  switch (name) {
    case "memory_save": {
      const result = await apiCall("/memory/save", {
        method: "POST",
        body: JSON.stringify({
          agent_id: AGENT_ID,
          category: args.category,
          key: args.key,
          content: args.content,
          importance: args.importance || 3,
        }),
      });
      return {
        content: [
          {
            type: "text",
            text: `Saved memory [${result.category}] "${result.key}" (id: ${result.id}, importance: ${result.importance})`,
          },
        ],
      };
    }

    case "memory_search": {
      // Prefer semantic search (if query is non-empty and no specific category filter)
      if (args.query && !args.category) {
        try {
          const semParams = new URLSearchParams({
            agent_id: AGENT_ID,
            q: args.query,
            limit: "10",
          });
          const semResult = await apiCall(`/memory/semantic-search?${semParams}`);
          if (semResult.memories && semResult.memories.length > 0) {
            const mode = semResult.mode === "semantic" ? "semantic" : "keyword";
            const modeLabel = mode === "semantic"
              ? "🧠 semantic (vector-based, understanding meaning)"
              : "🔤 keyword (exact substring match — semantic unavailable)";
            const lines = semResult.memories.map(
              (m) => {
                const sim = m.similarity != null
                  ? ` [${(m.similarity * 100).toFixed(0)}% match]`
                  : "";
                return `[${m.category}] ${m.key}${sim} (id:${m.id}, importance:${m.importance})\n  ${m.content}`;
              }
            );
            return {
              content: [
                {
                  type: "text",
                  text: `Found ${semResult.memories.length} memories via ${modeLabel}:\n\n${lines.join("\n\n")}`,
                },
              ],
            };
          }
          // semResult returned 0 results — fall through to keyword search below
        } catch (e) {
          // Fall through to keyword search
        }
      }

      // Keyword fallback / category-filtered search
      const params = new URLSearchParams({ agent_id: AGENT_ID });
      if (args.query) params.set("q", args.query);
      if (args.category) params.set("category", args.category);

      const result = await apiCall(`/memory/search?${params}`);
      if (result.memories.length === 0) {
        return {
          content: [{
            type: "text",
            text: `No memories found for query "${args.query || "(empty)"}"${args.category ? ` in category ${args.category}` : ""}.`,
          }],
        };
      }
      const lines = result.memories.map(
        (m) =>
          `[${m.category}] ${m.key} (id:${m.id}, importance:${m.importance})\n  ${m.content}`
      );
      const modeLabel = args.category
        ? "🔤 keyword (category-filtered)"
        : "🔤 keyword (exact substring match)";
      return {
        content: [
          {
            type: "text",
            text: `Found ${result.total} memories via ${modeLabel}:\n\n${lines.join("\n\n")}`,
          },
        ],
      };
    }

    case "memory_list": {
      const params = new URLSearchParams();
      if (args.category) params.set("category", args.category);
      const result = await apiCall(`/memory/agents/${AGENT_ID}?${params}`);
      if (result.memories.length === 0) {
        return {
          content: [{ type: "text", text: "No memories stored yet." }],
        };
      }
      const catSummary = Object.entries(result.categories)
        .map(([k, v]) => `${k}: ${v}`)
        .join(", ");
      const lines = result.memories.map(
        (m) =>
          `[${m.category}] ${m.key} (id:${m.id}, importance:${m.importance}, accessed:${m.access_count}x)\n  ${m.content}`
      );
      return {
        content: [
          {
            type: "text",
            text: `${result.total} memories (${catSummary}):\n\n${lines.join("\n\n")}`,
          },
        ],
      };
    }

    case "memory_delete": {
      await apiCall(`/memory/${args.memory_id}`, { method: "DELETE" });
      return {
        content: [{ type: "text", text: `Deleted memory ${args.memory_id}.` }],
      };
    }

    default:
      throw new Error(`Unknown tool: ${name}`);
  }
});

// --- Start ---
const transport = new StdioServerTransport();
await server.connect(transport);

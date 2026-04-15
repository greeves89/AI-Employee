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
        "Save something to long-term memory (issue #24 memory system upgrade).\n\n" +
        "**Use the new fields for precise retrieval later:**\n" +
        "  • `room`: hierarchical path like 'project:<name>/<area>'. Memories in the same\n" +
        "    room retrieve together and beat cousin-room noise by ~33%. USE A ROOM unless\n" +
        "    the memory is truly cross-project.\n" +
        "  • `tag_type`: 'transient' for task state (decays in 30d), 'permanent' for\n" +
        "    learned patterns (lives for months). Default: permanent.\n" +
        "  • `override`: only set to true after you got a 409 contradiction warning AND\n" +
        "    you confirmed the new content should replace the existing one.",
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
              "Short identifier. Prefer canonical keys: " +
              "current_goal, current_task (single-value — new replaces old); " +
              "code_pattern, approach_used, lesson_learned, touched_file, " +
              "referenced_url, decision_rationale (multi-value — coexist). " +
              "Use snake_case.",
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
              "Higher importance memories score higher in semantic search. Default: 3.",
          },
          room: {
            type: "string",
            description:
              "Hierarchical room path like 'project:ai-employee/backend/auth' or " +
              "'chat:telegram'. Use a consistent prefix per project+area. Rooms " +
              "dramatically improve retrieval precision — PASS THIS whenever you know " +
              "which project/area the memory belongs to.",
          },
          tag_type: {
            type: "string",
            enum: ["transient", "permanent"],
            description:
              "'transient' = short-lived task state (current_task, today's error notes, " +
              "work-in-progress). Decays in ~30 days via exponential decay. " +
              "'permanent' = learned patterns, architecture decisions, user preferences. " +
              "Decays slowly via logarithmic decay. Default: permanent.",
          },
          tags: {
            type: "array",
            items: { type: "string" },
            description:
              "Canonical tags (auto-normalized server-side). Choose from: task, code, " +
              "decision, learning, error, correction, pattern, architecture, performance, " +
              "security, user_preference, meta.",
          },
          override: {
            type: "boolean",
            description:
              "Only set to true AFTER you got a 409 contradiction warning and you " +
              "confirmed the new content should replace the existing one. The old memory " +
              "is kept as an audit trail via superseded_by.",
          },
        },
        required: ["category", "key", "content"],
      },
    },
    {
      name: "memory_search",
      description:
        "Search long-term memory using SEMANTIC search with multi-strategy re-ranking " +
        "(semantic 50% + room 30% + recency 15% + importance 5%) and automatic keyword " +
        "fallback. Use this at the start of conversations and before tasks to recall context. " +
        "\n\n" +
        "**CRITICAL**: pass `room` whenever you know which project/area you're working in — " +
        "it eliminates cousin-room noise and boosts precision by ~33%. Superseded memories " +
        "are automatically excluded from results. " +
        "\n\n" +
        "**Write queries in natural language** — the system understands meaning, not just keywords.",
      inputSchema: {
        type: "object",
        properties: {
          query: {
            type: "string",
            description:
              "Natural-language question or topic. Be descriptive. E.g. 'how should I format " +
              "emails to the user?' works better than just 'email'. Leave empty to list recent memories.",
          },
          category: {
            type: "string",
            enum: ["preference", "contact", "project", "procedure", "decision", "fact", "learning"],
            description:
              "Optional category filter. NOTE: providing a category forces keyword search " +
              "(no semantic ranking). Omit for best semantic results.",
          },
          room: {
            type: "string",
            description:
              "Optional room filter like 'project:ai-employee/backend/auth'. Exact matches " +
              "get 1.0 structural score, sub-rooms 0.7, parent-rooms 0.5, cousins 0.3. " +
              "Omit to search across all rooms.",
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
      // Forward the upgrade fields only when explicitly provided so we
      // stay backwards-compatible with older callers.
      const body = {
        agent_id: AGENT_ID,
        category: args.category,
        key: args.key,
        content: args.content,
        importance: args.importance || 3,
      };
      if (args.room) body.room = args.room;
      if (args.tag_type === "transient" || args.tag_type === "permanent") {
        body.tag_type = args.tag_type;
      }
      if (Array.isArray(args.tags) && args.tags.length > 0) body.tags = args.tags;
      if (args.override === true) body.override = true;
      if (typeof args.confidence === "number") body.confidence = args.confidence;

      try {
        const result = await apiCall("/memory/save", {
          method: "POST",
          body: JSON.stringify(body),
        });
        const extras = [];
        if (result.room) extras.push(`room=${result.room}`);
        if (result.tag_type && result.tag_type !== "permanent") {
          extras.push(`tag_type=${result.tag_type}`);
        }
        const extrasStr = extras.length > 0 ? ` (${extras.join(", ")})` : "";
        return {
          content: [
            {
              type: "text",
              text: `Saved memory [${result.category}] "${result.key}" (id: ${result.id}, importance: ${result.importance})${extrasStr}`,
            },
          ],
        };
      } catch (e) {
        // Detect 409 contradiction warning — surface the hint to the agent
        // so it can decide whether to re-call with override=true.
        const msg = String(e.message || e);
        if (msg.includes("409") || msg.toLowerCase().includes("contradiction")) {
          return {
            content: [
              {
                type: "text",
                text:
                  `⚠️ Contradiction warning: a very similar memory already exists in this ` +
                  `(agent, room, key) bucket. Review it via memory_search first. ` +
                  `If you're sure the new content should replace it, re-call memory_save ` +
                  `with the exact same fields PLUS override=true.\n\nDetail: ${msg}`,
              },
            ],
          };
        }
        throw e;
      }
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
          if (args.room) semParams.set("room", args.room);
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

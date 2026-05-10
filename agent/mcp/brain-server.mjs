#!/usr/bin/env node
/**
 * Second Brain MCP Server — full CRUD over the user's unified knowledge graph.
 *   brain_search     — semantic search across the user's brain
 *   brain_contribute — add/update a node (upsert by title)
 *   brain_get        — fetch full entry by id
 *   brain_list       — paginated browse (optional tag filter)
 *   brain_update     — explicit field update by id
 *   brain_delete     — remove a node (and its semantic links)
 *   brain_related    — list semantically related entries for a node
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
        "Add or update a node in the user's Second Brain (upsert by title). " +
        "Use this to share important learnings, decisions, research, or context with all other agents of this user. " +
        "The entry is automatically linked to semantically related entries in the brain.",
      inputSchema: {
        type: "object",
        properties: {
          title: { type: "string", description: "Unique title for this brain node (used as ID for upsert)." },
          content: { type: "string", description: "Markdown content. Use [[Other Title]] for explicit links." },
          tags: {
            type: "array",
            items: { type: "string" },
            description: "Tags for categorization (e.g. ['trading', 'decision', 'research']).",
          },
        },
        required: ["title", "content"],
      },
    },
    {
      name: "brain_get",
      description: "Fetch the full content of a single brain entry by its id. Use after brain_search to read full content.",
      inputSchema: {
        type: "object",
        properties: { id: { type: "number", description: "Entry id from brain_search/brain_list." } },
        required: ["id"],
      },
    },
    {
      name: "brain_list",
      description: "Paginated list of brain entries (titles + tags only — call brain_get for content). Use to browse what's in the brain.",
      inputSchema: {
        type: "object",
        properties: {
          limit: { type: "number", description: "Max entries (default 50, max 200)." },
          offset: { type: "number", description: "Pagination offset (default 0)." },
          tag: { type: "string", description: "Optional tag filter — only entries with this tag." },
        },
      },
    },
    {
      name: "brain_update",
      description: "Update an existing brain entry by id. Re-embeds and re-runs auto-linking. Use for fixes/refinements.",
      inputSchema: {
        type: "object",
        properties: {
          id: { type: "number", description: "Entry id to update." },
          title: { type: "string", description: "New title (optional)." },
          content: { type: "string", description: "New content (optional)." },
          tags: { type: "array", items: { type: "string" }, description: "New tags (optional, replaces existing)." },
        },
        required: ["id"],
      },
    },
    {
      name: "brain_delete",
      description: "Delete a brain entry by id. Also removes semantic links pointing to/from it. Irreversible — use with care.",
      inputSchema: {
        type: "object",
        properties: { id: { type: "number", description: "Entry id to delete." } },
        required: ["id"],
      },
    },
    {
      name: "brain_related",
      description: "Get semantically related entries for a given node (via cosine similarity in pgvector). Use for discovery — 'what else is connected to this?'",
      inputSchema: {
        type: "object",
        properties: {
          id: { type: "number", description: "Entry id to find neighbors for." },
          limit: { type: "number", description: "Max related entries (default 10, max 50)." },
        },
        required: ["id"],
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
        content: [{ type: "text", text: `✅ Brain node saved: "${result.title}" (id: ${result.id}). Auto-linking in progress.` }],
      };
    }

    if (name === "brain_get") {
      const result = await apiCall(`/brain/agent/get/${args.id}`);
      let text = `### ${result.title} (id: ${result.id})\n`;
      if (result.tags?.length) text += `Tags: ${result.tags.join(", ")}\n`;
      if (result.created_by) text += `Author: ${result.created_by}\n`;
      if (result.updated_at) text += `Updated: ${result.updated_at}\n`;
      text += `\n${result.content || ""}`;
      return { content: [{ type: "text", text }] };
    }

    if (name === "brain_list") {
      const params = new URLSearchParams({
        limit: String(args.limit || 50),
        offset: String(args.offset || 0),
      });
      if (args.tag) params.set("tag", args.tag);
      const result = await apiCall(`/brain/agent/list?${params}`);
      const entries = result.entries || [];
      let text = `[BRAIN LIST — ${entries.length} of ${result.total} entries]\n\n`;
      for (const e of entries) {
        text += `- **${e.title}** (id: ${e.id})`;
        if (e.tags?.length) text += ` — ${e.tags.map((t) => `#${t}`).join(" ")}`;
        text += "\n";
      }
      if (!entries.length) text += "(no entries)\n";
      return { content: [{ type: "text", text }] };
    }

    if (name === "brain_update") {
      const body = {};
      if (args.title !== undefined) body.title = args.title;
      if (args.content !== undefined) body.content = args.content;
      if (args.tags !== undefined) body.tags = args.tags;
      const result = await apiCall(`/brain/agent/update/${args.id}`, {
        method: "PUT",
        body: JSON.stringify(body),
      });
      return { content: [{ type: "text", text: `✅ Brain entry ${result.id} updated: "${result.title}". Re-linking in progress.` }] };
    }

    if (name === "brain_delete") {
      const result = await apiCall(`/brain/agent/delete/${args.id}`, { method: "DELETE" });
      return { content: [{ type: "text", text: `🗑️  Brain entry ${result.deleted} deleted (and its links).` }] };
    }

    if (name === "brain_related") {
      const params = new URLSearchParams({ limit: String(args.limit || 10) });
      const result = await apiCall(`/brain/agent/related/${args.id}?${params}`);
      const related = result.related || [];
      let text = `[BRAIN RELATED — ${related.length} neighbors of entry ${result.entry_id}]\n\n`;
      for (const r of related) {
        text += `- **${r.title}** (id: ${r.id}, sim: ${r.similarity})`;
        if (r.tags?.length) text += ` — ${r.tags.map((t) => `#${t}`).join(" ")}`;
        text += "\n";
      }
      if (!related.length) text += "(no semantic neighbors yet — embedding may be missing)\n";
      return { content: [{ type: "text", text }] };
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

#!/usr/bin/env node
/**
 * MCP Skill Server — Skill marketplace access for agents.
 *
 * Agents can search, propose, and use skills from the central marketplace.
 *
 * Environment:
 *   ORCHESTRATOR_URL - Base URL of the orchestrator (default: http://orchestrator:8000)
 *   AGENT_ID         - ID of the agent using this server
 *   AGENT_TOKEN      - Auth token for agent API calls
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
  { name: "mcp-skills", version: "1.0.0" },
  { capabilities: { tools: {} } }
);

server.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: [
    {
      name: "skill_search",
      description:
        "Search the skill marketplace for reusable routines, templates, workflows, and patterns. " +
        "Use this when you need a proven approach for a task, before inventing your own solution. " +
        "Categories: routine, template, workflow, pattern, recipe, tool.",
      inputSchema: {
        type: "object",
        properties: {
          query: {
            type: "string",
            description: "Search query (e.g. 'deploy process', 'meeting notes template', 'error handling').",
          },
          category: {
            type: "string",
            enum: ["routine", "template", "workflow", "pattern", "recipe", "tool"],
            description: "Optional category filter.",
          },
        },
      },
    },
    {
      name: "skill_propose",
      description:
        "Propose a new skill for the marketplace. Use this when you discover a reusable pattern " +
        "that other agents could benefit from. The skill will be submitted as a draft for the user " +
        "to review and approve. Write clear, step-by-step instructions in markdown.",
      inputSchema: {
        type: "object",
        properties: {
          name: {
            type: "string",
            description: "Short, lowercase-hyphenated name (e.g. 'pr-review-workflow', 'api-doc-template').",
          },
          description: {
            type: "string",
            description: "One-line description of what the skill does.",
          },
          content: {
            type: "string",
            description:
              "Full skill instructions in markdown. Include: context, step-by-step process, " +
              "examples, and common pitfalls. This is what agents will follow when using the skill.",
          },
          category: {
            type: "string",
            enum: ["routine", "template", "workflow", "pattern", "recipe", "tool"],
            description: "Skill category. Default: pattern.",
          },
        },
        required: ["name", "description", "content"],
      },
    },
    {
      name: "skill_get_my_skills",
      description:
        "Get all skills currently assigned to you. Use this at the start of complex tasks " +
        "to check if you have relevant skills before starting from scratch.",
      inputSchema: {
        type: "object",
        properties: {},
      },
    },
    {
      name: "skill_rate",
      description:
        "Rate a skill after using it. Call this at the end of every task where you used a skill. " +
        "Your rating improves skill quality over time and helps other agents find the best skills.",
      inputSchema: {
        type: "object",
        properties: {
          skill_id: {
            type: "number",
            description: "The numeric ID of the skill you used.",
          },
          rating: {
            type: "number",
            description: "Rating from 1 (poor) to 5 (excellent).",
          },
          comment: {
            type: "string",
            description: "Optional: what worked well or what could be improved.",
          },
        },
        required: ["skill_id", "rating"],
      },
    },
    {
      name: "skill_update",
      description:
        "Update a skill you previously created — use this when user feedback shows the skill " +
        "needs improvement, or when you discover a better approach. Only works on skills you created.",
      inputSchema: {
        type: "object",
        properties: {
          skill_id: {
            type: "number",
            description: "The numeric ID of the skill to update.",
          },
          description: {
            type: "string",
            description: "Updated one-line description (optional).",
          },
          content: {
            type: "string",
            description: "Updated full skill content in markdown (optional).",
          },
          feedback: {
            type: "string",
            description: "What user feedback or insight triggered this update — logged as changelog entry.",
          },
        },
        required: ["skill_id"],
      },
    },
  ],
}));

server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const { name, arguments: args } = request.params;

  switch (name) {
    case "skill_search": {
      const params = new URLSearchParams();
      if (args.query) params.set("q", args.query);
      if (args.category) params.set("category", args.category);
      const qs = params.toString() ? `?${params}` : "";

      const result = await apiCall(`/skills/agent/search${qs}`);
      if (!result.skills || result.skills.length === 0) {
        return {
          content: [{
            type: "text",
            text: `No skills found for "${args.query || "(all)"}". Consider proposing a new skill with skill_propose if you develop a reusable pattern.`,
          }],
        };
      }
      const lines = result.skills.map((s) => {
        const rating = s.avg_rating ? ` [${"★".repeat(Math.round(s.avg_rating))}${"☆".repeat(5 - Math.round(s.avg_rating))}]` : "";
        return `**${s.name}** (${s.category})${rating} — ${s.description}\n${s.content.substring(0, 200)}${s.content.length > 200 ? "..." : ""}`;
      });
      return {
        content: [{
          type: "text",
          text: `Found ${result.total} skills:\n\n${lines.join("\n\n---\n\n")}`,
        }],
      };
    }

    case "skill_propose": {
      const result = await apiCall("/skills/agent/propose", {
        method: "POST",
        body: JSON.stringify({
          name: args.name,
          description: args.description,
          content: args.content,
          category: args.category || "pattern",
        }),
      });
      return {
        content: [{
          type: "text",
          text: `Skill proposed: "${result.name}" (id: ${result.id}, status: draft). The user will be notified to review and approve it.`,
        }],
      };
    }

    case "skill_get_my_skills": {
      const result = await apiCall("/skills/agent/available");
      if (!result.skills || result.skills.length === 0) {
        return {
          content: [{
            type: "text",
            text: "You have no skills assigned. Search the marketplace with skill_search to find useful ones.",
          }],
        };
      }
      const lines = result.skills.map((s) =>
        `**${s.name}** — ${s.description}\n${s.content.substring(0, 300)}${s.content.length > 300 ? "..." : ""}`
      );
      return {
        content: [{
          type: "text",
          text: `You have ${result.total} skills:\n\n${lines.join("\n\n---\n\n")}`,
        }],
      };
    }

    case "skill_rate": {
      const result = await apiCall(`/skills/marketplace/${args.skill_id}/rate`, {
        method: "POST",
        body: JSON.stringify({ rating: args.rating, comment: args.comment || "" }),
      });
      const stars = "★".repeat(args.rating) + "☆".repeat(5 - args.rating);
      return {
        content: [{
          type: "text",
          text: `Skill rated ${stars}. New avg: ${result.avg_rating?.toFixed(1) ?? "n/a"} (${result.usage_count} uses).`,
        }],
      };
    }

    case "skill_update": {
      const body = {};
      if (args.description !== undefined) body.description = args.description;
      if (args.content !== undefined) body.content = args.content;
      if (args.feedback !== undefined) body.feedback = args.feedback;
      const result = await apiCall(`/skills/agent/${args.skill_id}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      });
      return {
        content: [{
          type: "text",
          text: `Skill "${result.name}" (id: ${result.id}) updated successfully.${args.feedback ? ` Changelog: "${args.feedback}"` : ""}`,
        }],
      };
    }

    default:
      throw new Error(`Unknown tool: ${name}`);
  }
});

const transport = new StdioServerTransport();
await server.connect(transport);

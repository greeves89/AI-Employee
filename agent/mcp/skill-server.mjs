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
        "Always pass task_id so usage is tracked even if you don't call skill_install afterward. " +
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
          task_id: {
            type: "string",
            description: "Current task ID (CURRENT_TASK_ID). Required for usage tracking.",
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
      name: "skill_install",
      description:
        "Install a skill from the marketplace to yourself. Call after skill_search when you find " +
        "a relevant skill. The skill content is returned immediately so you can use it right away.",
      inputSchema: {
        type: "object",
        properties: {
          skill_id: {
            type: "number",
            description: "ID of the skill to install (from skill_search results).",
          },
        },
        required: ["skill_id"],
      },
    },
    {
      name: "skill_record_usage",
      description:
        "Record that you actively used a skill during this task. Call this whenever you apply a skill's " +
        "instructions to complete work — this builds the analytics data for skill effectiveness. " +
        "Use skill_rate instead if you also want to leave a quality rating.",
      inputSchema: {
        type: "object",
        properties: {
          skill_id: {
            type: "number",
            description: "The numeric ID of the skill you used.",
          },
          task_id: {
            type: "string",
            description: "Optional: the current task ID (shown as CURRENT_TASK_ID at the top of your prompt).",
          },
          helpfulness: {
            type: "number",
            description: "Optional: how helpful was the skill? 1 (not helpful) to 5 (extremely helpful).",
          },
        },
        required: ["skill_id"],
      },
    },
    {
      name: "skill_rate",
      description:
        "Rate a skill after using it AND record that you used it. Call this at the end of every task " +
        "where you used a skill. Also call this when the user gives feedback on your result — " +
        "pass user_rating based on their sentiment. Your rating improves skill quality over time.",
      inputSchema: {
        type: "object",
        properties: {
          skill_id: {
            type: "number",
            description: "The numeric ID of the skill you used.",
          },
          rating: {
            type: "number",
            description: "Your self-rating of task quality: 1 (poor) to 5 (excellent).",
          },
          helpfulness: {
            type: "number",
            description: "How much did this skill specifically help? 1-5.",
          },
          user_rating: {
            type: "number",
            description: "User feedback interpreted from natural language. 'super/perfekt'=5, 'gut/ok'=4, 'geht so'=3, 'nicht gut'=2, 'schlecht'=1. Only set when user has actually responded.",
          },
          task_id: {
            type: "string",
            description: "The current task ID (CURRENT_TASK_ID from the top of your prompt).",
          },
          comment: {
            type: "string",
            description: "What worked well or what could be improved in the skill.",
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
      if (args.task_id) params.set("task_id", args.task_id);
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
          text: `Found ${result.total} skills:\n\n${wrapData("skill-marketplace", lines.join("\n\n---\n\n"))}`,
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
          text: `You have ${result.total} skills:\n\n${wrapData("skill-marketplace", lines.join("\n\n---\n\n"))}`,
        }],
      };
    }

    case "skill_record_usage": {
      const result = await apiCall("/skills/agent/record-usage", {
        method: "POST",
        body: JSON.stringify({
          skill_id: args.skill_id,
          task_id: args.task_id || null,
          helpfulness: args.helpfulness || null,
        }),
      });
      return {
        content: [{
          type: "text",
          text: `Skill usage recorded (skill_id: ${args.skill_id}). Total uses: ${result.usage_count ?? "n/a"}.`,
        }],
      };
    }

    case "skill_install": {
      const result = await apiCall(`/skills/agent/install/${args.skill_id}`, {
        method: "POST",
      });
      const status = result.status === "already_installed" ? "already installed" : "installed";
      return {
        content: [{
          type: "text",
          text: wrapData(
            `skill-install:${args.skill_id}`,
            `Skill "${result.skill_name}" ${status} (id=${args.skill_id}).\n\n${result.content || ""}`,
          ),
        }],
      };
    }

    case "skill_rate": {
      const body = {
        skill_id: args.skill_id,
        task_id: args.task_id || null,
        helpfulness: args.helpfulness || null,
        rating: args.rating,
        comment: args.comment || null,
      };
      if (args.user_rating != null) body.user_rating = args.user_rating;
      const result = await apiCall("/skills/agent/record-usage", {
        method: "POST",
        body: JSON.stringify(body),
      });
      const stars = "★".repeat(args.rating) + "☆".repeat(5 - args.rating);
      const userFeedback = args.user_rating ? ` | User rating: ${args.user_rating}/5` : "";
      return {
        content: [{
          type: "text",
          text: `Skill rated ${stars}${userFeedback}. New avg: ${result.avg_rating?.toFixed(1) ?? "n/a"} (${result.usage_count} uses).`,
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

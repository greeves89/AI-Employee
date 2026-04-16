#!/usr/bin/env node
/**
 * MCP Orchestrator Server - Task management, team communication, and scheduling.
 *
 * Provides agents with the ability to create tasks, communicate with teammates,
 * and manage recurring schedules. Can be used with any MCP client.
 *
 * Environment:
 *   ORCHESTRATOR_URL - Base URL of the orchestrator (default: http://orchestrator:8000)
 *   AGENT_ID         - ID of the agent using this server
 *   AGENT_NAME       - Display name of the agent
 *   DEFAULT_MODEL    - Default model for new tasks (default: claude-sonnet-4-5-20250929)
 */

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";

const API = `${process.env.ORCHESTRATOR_URL || "http://orchestrator:8000"}/api/v1`;
const AGENT_ID = process.env.AGENT_ID || "unknown";
const AGENT_NAME = process.env.AGENT_NAME || "unknown";
const AGENT_TOKEN = process.env.AGENT_TOKEN || "";
const DEFAULT_MODEL = process.env.DEFAULT_MODEL || "claude-sonnet-4-6";

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
  { name: "mcp-orchestrator", version: "1.0.0" },
  { capabilities: { tools: {} } }
);

// --- List available tools ---
server.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: [
    {
      name: "create_task",
      description:
        "Create a new task for yourself or another agent. The task will be queued and " +
        "executed when resources are available. Use this to delegate work, split complex " +
        "tasks into subtasks, or schedule follow-up work.",
      inputSchema: {
        type: "object",
        properties: {
          title: {
            type: "string",
            description: "Short task title (e.g. 'Write unit tests for auth module').",
          },
          prompt: {
            type: "string",
            description: "Detailed instructions for the task. Be specific about what needs to be done.",
          },
          priority: {
            type: "number",
            minimum: 1,
            maximum: 10,
            description:
              "Task priority (1=highest, 10=lowest). Default: 5. " +
              "Use 1-2 for urgent tasks, 5 for normal, 8-10 for background tasks.",
          },
          agent_id: {
            type: "string",
            description:
              "ID of the agent to assign this task to. Leave empty to assign to yourself. " +
              "Use list_team to find other agents.",
          },
        },
        required: ["title", "prompt"],
      },
    },
    {
      name: "create_task_batch",
      description:
        "Create multiple tasks in parallel for different agents. All tasks run simultaneously. " +
        "Use this to split complex work into parallel sub-tasks: e.g. 'research + code + test' " +
        "running on 3 agents at once. You will be notified when each subtask completes. " +
        "Maximum 20 tasks per batch.",
      inputSchema: {
        type: "object",
        properties: {
          tasks: {
            type: "array",
            items: {
              type: "object",
              properties: {
                title: { type: "string", description: "Short task title." },
                prompt: { type: "string", description: "Detailed instructions for this sub-task." },
                priority: {
                  type: "number", minimum: 1, maximum: 10,
                  description: "Task priority (1=highest). Default: 5.",
                },
                agent_id: {
                  type: "string",
                  description: "Agent to assign to. Use list_team to find agents. Leave empty for auto-assign.",
                },
              },
              required: ["title", "prompt"],
            },
            description: "List of tasks to create in parallel (max 20).",
          },
        },
        required: ["tasks"],
      },
    },
    {
      name: "list_tasks",
      description:
        "List tasks, optionally filtered by status and/or agent. " +
        "By default shows YOUR tasks. Set agent_id to see another agent's tasks " +
        "(useful for checking delegated work).",
      inputSchema: {
        type: "object",
        properties: {
          status: {
            type: "string",
            enum: ["pending", "running", "completed", "failed"],
            description: "Filter by task status. Omit to show all tasks.",
          },
          agent_id: {
            type: "string",
            description:
              "Agent ID to check tasks for. Defaults to yourself. " +
              "Use another agent's ID to check tasks you delegated to them.",
          },
        },
      },
    },
    {
      name: "list_team",
      description:
        "List all agents in the team with their roles, capabilities, and current status. " +
        "Use this to find the right agent to delegate tasks to or to send messages.",
      inputSchema: {
        type: "object",
        properties: {},
      },
    },
    {
      name: "send_message",
      description:
        "Send a structured message to another agent. The message will appear in their conversation " +
        "context the next time they run a task. Use this for coordination, sharing results, " +
        "asking questions, or handing off work. Set message_type to help the receiver understand " +
        "the intent. Use reply_to to link responses to previous messages.",
      inputSchema: {
        type: "object",
        properties: {
          agent_id: {
            type: "string",
            description: "ID of the agent to send the message to. Use list_team to find IDs.",
          },
          message: {
            type: "string",
            description: "The message text to send.",
          },
          message_type: {
            type: "string",
            enum: ["message", "question", "response", "handoff", "notification", "status_update"],
            description:
              "Type of message. 'question' expects a reply, 'response' answers a previous question, " +
              "'handoff' transfers ownership of work, 'notification' is FYI only. Default: 'message'.",
          },
          reply_to: {
            type: "string",
            description:
              "message_id of a previous message you are replying to. " +
              "This links your response to the original message for conversation threading.",
          },
        },
        required: ["agent_id", "message"],
      },
    },
    {
      name: "send_message_and_wait",
      description:
        "Send a message to another agent AND wait for their reply (up to 45 seconds). " +
        "Use this instead of send_message when you need the answer in the current conversation. " +
        "The other agent must be online and processing messages for this to work.",
      inputSchema: {
        type: "object",
        properties: {
          agent_id: {
            type: "string",
            description: "ID of the agent to message. Use list_team to find IDs.",
          },
          message: {
            type: "string",
            description: "The message to send. Be specific about what you need.",
          },
          message_type: {
            type: "string",
            enum: ["question", "message"],
            description: "Type of message. Default: question.",
          },
        },
        required: ["agent_id", "message"],
      },
    },
    {
      name: "create_schedule",
      description:
        "Create a recurring schedule that automatically runs a task at regular intervals. " +
        "Use this for monitoring, periodic reports, data syncing, or any recurring work.",
      inputSchema: {
        type: "object",
        properties: {
          name: {
            type: "string",
            description: "Name of the schedule (e.g. 'Daily status report', 'Hourly health check').",
          },
          prompt: {
            type: "string",
            description: "The task instructions to run each time the schedule triggers.",
          },
          interval_seconds: {
            type: "number",
            minimum: 60,
            description:
              "Interval between runs in seconds. Examples: 3600=hourly, 86400=daily, 604800=weekly. " +
              "Minimum: 60 seconds.",
          },
        },
        required: ["name", "prompt", "interval_seconds"],
      },
    },
    {
      name: "list_schedules",
      description: "List all recurring schedules with their status, interval, and next run time.",
      inputSchema: {
        type: "object",
        properties: {},
      },
    },
    {
      name: "manage_schedule",
      description: "Pause, resume, or delete a recurring schedule.",
      inputSchema: {
        type: "object",
        properties: {
          schedule_id: {
            type: "number",
            description: "ID of the schedule to manage.",
          },
          action: {
            type: "string",
            enum: ["pause", "resume", "delete"],
            description: "Action to take on the schedule.",
          },
        },
        required: ["schedule_id", "action"],
      },
    },
    {
      name: "list_todos",
      description:
        "List your TODO items. TODOs are persistent and visible to the user in the Todo tab. " +
        "Use this to check what work is pending, in progress, or completed. " +
        "In proactive mode, always check TODOs first before doing anything else. " +
        "TODOs can be grouped by project - use the project filter to see TODOs for a specific project.",
      inputSchema: {
        type: "object",
        properties: {
          status: {
            type: "string",
            enum: ["pending", "in_progress", "completed"],
            description: "Filter by status. Omit to show all TODOs.",
          },
          task_id: {
            type: "string",
            description: "Filter by task ID to see steps for a specific task. Omit for all TODOs.",
          },
          project: {
            type: "string",
            description:
              "Filter by project name (e.g. 'Deeskalator', 'Entscheidungs-App'). " +
              "Omit to show all TODOs across all projects.",
          },
        },
      },
    },
    {
      name: "update_todos",
      description:
        "Add or replace pending TODOs. Completed TODOs are NEVER deleted (preserved automatically). " +
        "IMPORTANT: ALWAYS call list_todos FIRST to check existing TODOs before using this! " +
        "Existing TODOs represent the user's work plan - review and work on them before creating new ones. " +
        "Only pending/in_progress items are replaced; completed items are always kept. " +
        "ALWAYS set the 'project' field to group TODOs by project (e.g. 'Deeskalator', 'Entscheidungs-App'). " +
        "Set project_path to the workspace path of the project (e.g. '/workspace/deeskalator/').",
      inputSchema: {
        type: "object",
        properties: {
          task_id: {
            type: "string",
            description:
              "Link TODOs to a specific task. Omit for general/recurring TODOs.",
          },
          project: {
            type: "string",
            description:
              "Project name to group these TODOs under (e.g. 'Deeskalator', 'Entscheidungs-App'). " +
              "ALWAYS set this when TODOs belong to a specific project. " +
              "Applied to all TODOs in this batch unless overridden per-item.",
          },
          project_path: {
            type: "string",
            description:
              "Workspace path of the project (e.g. '/workspace/deeskalator/'). " +
              "Applied to all TODOs in this batch unless overridden per-item.",
          },
          todos: {
            type: "array",
            items: {
              type: "object",
              properties: {
                title: { type: "string", description: "Short TODO description. Do NOT prefix with [ProjectName] - use the project field instead." },
                description: { type: "string", description: "Optional details." },
                status: {
                  type: "string",
                  enum: ["pending", "in_progress", "completed"],
                  description: "Status. Default: pending.",
                },
                priority: {
                  type: "number",
                  minimum: 1,
                  maximum: 5,
                  description: "Priority (1=highest, 5=lowest). Default: 3.",
                },
                project: {
                  type: "string",
                  description: "Override project name for this specific TODO (optional, inherits from batch-level).",
                },
                project_path: {
                  type: "string",
                  description: "Override project path for this specific TODO (optional, inherits from batch-level).",
                },
              },
              required: ["title"],
            },
            description: "New/updated TODOs. Replaces pending/in_progress items only (completed are preserved).",
          },
        },
        required: ["todos"],
      },
    },
    {
      name: "complete_todo",
      description:
        "Mark a single TODO as completed by its ID. Use this when you finish a step.",
      inputSchema: {
        type: "object",
        properties: {
          todo_id: {
            type: "number",
            description: "ID of the TODO to mark as completed.",
          },
        },
        required: ["todo_id"],
      },
    },
  ],
}));

// --- Handle tool calls ---
server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const { name, arguments: args } = request.params;

  switch (name) {
    case "create_task": {
      const targetAgent = args.agent_id || AGENT_ID;
      const body = {
        title: args.title,
        prompt: args.prompt,
        priority: args.priority || 5,
        agent_id: targetAgent,
        model: DEFAULT_MODEL,
      };
      // Track delegation: if creating task for another agent, record who delegated
      if (targetAgent !== AGENT_ID) {
        body.created_by_agent = AGENT_ID;
      }
      const result = await apiCall("/tasks/", {
        method: "POST",
        body: JSON.stringify(body),
      });
      return {
        content: [
          {
            type: "text",
            text: `Task created (id: ${result.id}, status: ${result.status}, assigned to: ${result.agent_id || AGENT_ID}).`,
          },
        ],
      };
    }

    case "create_task_batch": {
      const batchTasks = (args.tasks || []).map((t) => ({
        title: t.title,
        prompt: t.prompt,
        priority: t.priority || 5,
        agent_id: t.agent_id || null,
        model: DEFAULT_MODEL,
      }));
      const result = await apiCall("/tasks/batch", {
        method: "POST",
        body: JSON.stringify({
          tasks: batchTasks,
          created_by_agent: AGENT_ID,
        }),
      });
      const lines = result.tasks.map(
        (t) => `  - #${t.id}: "${t.title}" → ${t.agent_id || "auto"} [${t.status}]`
      );
      return {
        content: [
          {
            type: "text",
            text:
              `Batch created: ${result.total} tasks running in parallel:\n${lines.join("\n")}\n\n` +
              `You will be notified as each task completes.`,
          },
        ],
      };
    }

    case "list_tasks": {
      const params = new URLSearchParams({ agent_id: args.agent_id || AGENT_ID });
      if (args.status) params.set("status", args.status);

      const result = await apiCall(`/tasks/?${params}`);
      if (!result.tasks || result.tasks.length === 0) {
        return {
          content: [{ type: "text", text: "No tasks found." }],
        };
      }
      const lines = result.tasks.map(
        (t) =>
          `[${t.status}] #${t.id}: ${t.title} (priority: ${t.priority})`
      );
      return {
        content: [
          {
            type: "text",
            text: `${result.tasks.length} tasks:\n\n${lines.join("\n")}`,
          },
        ],
      };
    }

    case "list_team": {
      const result = await apiCall("/agents/team/directory");
      if (!result.agents || result.agents.length === 0) {
        return {
          content: [{ type: "text", text: "No team members found." }],
        };
      }
      const lines = result.agents.map(
        (a) =>
          `${a.name} (id: ${a.id}, role: ${a.role || "general"}, status: ${a.status})`
      );
      return {
        content: [
          {
            type: "text",
            text: `Team (${result.agents.length} agents):\n\n${lines.join("\n")}`,
          },
        ],
      };
    }

    case "send_message": {
      const sendResult = await apiCall(`/agents/${args.agent_id}/message`, {
        method: "POST",
        body: JSON.stringify({
          from_agent_id: AGENT_ID,
          from_name: AGENT_NAME,
          text: args.message,
          message_type: args.message_type || "message",
          reply_to: args.reply_to || null,
        }),
      });
      const typeLabel = args.message_type ? ` [${args.message_type}]` : "";
      const replyLabel = args.reply_to ? ` (reply to: ${args.reply_to})` : "";
      return {
        content: [
          {
            type: "text",
            text: `Message sent to agent ${args.agent_id}${typeLabel}${replyLabel}. message_id: ${sendResult.message_id}`,
          },
        ],
      };
    }

    case "send_message_and_wait": {
      // Step 1: Get current max message ID (so we know what's "new")
      const beforeMsgs = await apiCall(
        `/agents/team/poll-reply?from_agent_id=${args.agent_id}&to_agent_id=${AGENT_ID}&since_id=0&timeout=1`
      );
      const sinceId = beforeMsgs.message ? beforeMsgs.message.id : 0;

      // Step 2: Send the message
      await apiCall(`/agents/${args.agent_id}/message`, {
        method: "POST",
        body: JSON.stringify({
          from_agent_id: AGENT_ID,
          from_name: AGENT_NAME,
          text: args.message,
          message_type: args.message_type || "question",
        }),
      });

      // Step 3: Poll for reply (up to 45s)
      const pollResult = await apiCall(
        `/agents/team/poll-reply?from_agent_id=${args.agent_id}&to_agent_id=${AGENT_ID}&since_id=${sinceId}&timeout=45`
      );

      if (pollResult.found && pollResult.message) {
        return {
          content: [{
            type: "text",
            text:
              `Reply from ${pollResult.message.from_name}:\n\n${pollResult.message.text}`,
          }],
        };
      }
      return {
        content: [{
          type: "text",
          text:
            `Message sent to ${args.agent_id}, but no reply received within 45 seconds. ` +
            `The agent may be busy or offline. The reply will arrive in your message queue later.`,
        }],
      };
    }

    case "create_schedule": {
      const result = await apiCall("/schedules/", {
        method: "POST",
        body: JSON.stringify({
          name: args.name,
          prompt: args.prompt,
          interval_seconds: args.interval_seconds,
          agent_id: AGENT_ID,
          model: DEFAULT_MODEL,
        }),
      });
      return {
        content: [
          {
            type: "text",
            text: `Schedule created: "${result.name}" (id: ${result.id}, interval: ${result.interval_seconds}s).`,
          },
        ],
      };
    }

    case "list_schedules": {
      const result = await apiCall("/schedules/");
      if (!result.schedules || result.schedules.length === 0) {
        return {
          content: [{ type: "text", text: "No schedules found." }],
        };
      }
      const lines = result.schedules.map(
        (s) =>
          `[${s.active ? "active" : "paused"}] #${s.id}: ${s.name} (every ${s.interval_seconds}s)`
      );
      return {
        content: [
          {
            type: "text",
            text: `${result.schedules.length} schedules:\n\n${lines.join("\n")}`,
          },
        ],
      };
    }

    case "manage_schedule": {
      const { schedule_id, action } = args;
      if (action === "delete") {
        await apiCall(`/schedules/${schedule_id}`, { method: "DELETE" });
        return {
          content: [{ type: "text", text: `Schedule ${schedule_id} deleted.` }],
        };
      }
      await apiCall(`/schedules/${schedule_id}/${action}`, { method: "POST" });
      return {
        content: [
          {
            type: "text",
            text: `Schedule ${schedule_id} ${action === "pause" ? "paused" : "resumed"}.`,
          },
        ],
      };
    }

    case "list_todos": {
      const params = new URLSearchParams();
      if (args.status) params.set("status", args.status);
      if (args.task_id) params.set("task_id", args.task_id);
      if (args.project) params.set("project", args.project);
      const qs = params.toString() ? `?${params}` : "";

      const result = await apiCall(`/todos/agent/list${qs}`);
      if (!result.todos || result.todos.length === 0) {
        return {
          content: [{ type: "text", text: "No TODOs found." }],
        };
      }
      const lines = result.todos.map(
        (t) =>
          `[${t.status}] #${t.id}: ${t.title}${t.project ? ` [${t.project}]` : ""}${t.description ? ` - ${t.description}` : ""} (priority: ${t.priority})`
      );
      const projectInfo = result.projects && result.projects.length > 0
        ? `\nProjects: ${result.projects.join(", ")}`
        : "";
      return {
        content: [
          {
            type: "text",
            text: `${result.total} TODOs (${result.pending} pending, ${result.in_progress} in progress, ${result.completed} completed):${projectInfo}\n\n${lines.join("\n")}`,
          },
        ],
      };
    }

    case "update_todos": {
      const result = await apiCall("/todos/agent/bulk", {
        method: "PUT",
        body: JSON.stringify({
          task_id: args.task_id || null,
          project: args.project || null,
          project_path: args.project_path || null,
          todos: (args.todos || []).map((t) => ({
            title: t.title,
            description: t.description || null,
            status: t.status || "pending",
            priority: t.priority || 3,
            project: t.project || null,
            project_path: t.project_path || null,
          })),
        }),
      });
      return {
        content: [
          {
            type: "text",
            text: `TODOs updated: ${result.updated} updated, ${result.added} added (total: ${result.total}).`,
          },
        ],
      };
    }

    case "complete_todo": {
      const result = await apiCall(`/todos/agent/${args.todo_id}/complete`, {
        method: "PATCH",
      });
      return {
        content: [
          {
            type: "text",
            text: `TODO #${result.id} "${result.title}" marked as completed.`,
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

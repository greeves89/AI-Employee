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
 *   DEFAULT_MODEL    - (nicht mehr benutzt) Das Modell eines Auftrags bestimmt der
 *                      ZIELAGENT, nicht der Auftraggeber — siehe unten.
 */

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { startServer } from "./_transport.mjs";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";

const API = `${process.env.ORCHESTRATOR_URL || "http://orchestrator:8000"}/api/v1`;
const AGENT_ID = process.env.AGENT_ID || "unknown";
const AGENT_NAME = process.env.AGENT_NAME || "unknown";
const AGENT_TOKEN = process.env.AGENT_TOKEN || "";

function wrapData(source, content) {
  return `[EXTERNAL-DATA source="${source}"]\n${content}\n[/EXTERNAL-DATA]`;
}

// Ein blosses .substring(0, n) schneidet mitten im Wort ab, wenn ein Sub-Agent
// erst seine Pflicht-Vorabchecks beschreibt, bevor die eigentliche Antwort
// kommt — die Antwort sah dadurch aus, als fehle sie komplett. Schneidet
// stattdessen an der letzten Wortgrenze vor dem Limit.
function truncatePreservingWords(text, limit) {
  if (text.length <= limit) return text;
  const cut = text.lastIndexOf(" ", limit);
  const at = cut > 0 ? cut : limit;
  return `${text.slice(0, at).trimEnd()} […]`;
}
// Bewusst NICHT mehr an neue Auftraege gehaengt.
//
// Bis 1.254.x schickte jedes Delegier-Werkzeug `model: DEFAULT_MODEL` mit — das
// Modell des AUFTRAGGEBERS. Ein Kollege arbeitete damit unter einem Modell, das
// er sich nie ausgesucht hat, und der Model-Router des Zielagenten kam gar nicht
// erst zum Zug (der Orchestrator fragt ihn nur, wenn KEIN Modell mitkam).
//
// Vorgabe des Nutzers am 19.08.2026: „wenn delegiert SOLL das eingestellte
// Modell des Agent verwendet werden". Ohne Modell im Auftrag faellt der
// Orchestrator genau darauf zurueck ("we leave it None so the agent falls back
// to its own default"). Der Custom-LLM-Weg machte es ohnehin schon so.
//
// Die Variable bleibt fuer Diagnosezwecke stehen; wer sie wieder anhaengt,
// nimmt dem Zielagenten seine Modellwahl.
const DEFAULT_MODEL = process.env.DEFAULT_MODEL || "claude-sonnet-4-6";
void DEFAULT_MODEL;

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

// Eine Fabrik statt einer Modul-Instanz: im HTTP-Modus bedient ein Prozess
// mehrere gleichzeitige Laeufe, und ein `Server` laesst sich nur an genau einen
// Transport binden (siehe _transport.mjs).
export function buildServer() {
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
        name: "delegate_and_wait",
        description:
          "Create parallel sub-tasks and wait for ALL of them to complete, then return aggregated results. " +
          "Use this when you need the results before continuing (e.g. research + analysis in parallel). " +
          "For fire-and-forget delegation, use create_task_batch instead. Max 20 tasks, max 10 min wait.",
        inputSchema: {
          type: "object",
          properties: {
            tasks: {
              type: "array",
              items: {
                type: "object",
                properties: {
                  title: { type: "string", description: "Short task title." },
                  prompt: {
                    type: "string",
                    description:
                      "Detailed instructions for this sub-task. Must stand on its own: the receiver " +
                      "has its OWN /workspace and cannot see yours. Never point at a /workspace/... " +
                      "path of yours — copy what they need to /shared/ and name that path, or put " +
                      "the content into this prompt.",
                  },
                  agent_id: { type: "string", description: "Agent to assign to. Leave empty for auto-assign." },
                },
                required: ["title", "prompt"],
              },
              description: "Sub-tasks to run in parallel (max 20).",
            },
            timeout_seconds: {
              type: "number",
              description: "Max wait time in seconds. Default: 300 (5 min). Max: 600.",
            },
          },
          required: ["tasks"],
        },
      },
      {
        name: "get_tasks_status",
        description:
          "Check the status and result of specific tasks by their IDs. " +
          "Use after create_task or create_task_batch to poll for completion.",
        inputSchema: {
          type: "object",
          properties: {
            task_ids: {
              type: "array",
              items: { type: "number" },
              description: "List of task IDs to check.",
            },
          },
          required: ["task_ids"],
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
          "SYSTEM-WIDE directory: agents visible to you across teams (your own members plus " +
          "other teams' leads), with roles and status. Use it to FIND someone outside your team. " +
          "\n\nThis is NOT your team. When asked 'which agents do you have / who is on your team', " +
          "answer from list_my_team ALONE and do not merge these entries into it — an agent from " +
          "another team is not your colleague, and naming them as such leads to work being handed " +
          "to someone who never picks it up.",
        inputSchema: {
          type: "object",
          properties: {},
        },
      },
      {
        name: "list_my_team",
        description:
          "List the team(s) YOU belong to, with the current roster (names, roles, who is lead). " +
          "ALWAYS call this before answering anything about 'my team' or 'who works for me' — " +
          "members can be added or removed at any time WITHOUT restarting you, so your memory " +
          "and any team file in your workspace go stale. This is the live source.",
        inputSchema: {
          type: "object",
          properties: {},
        },
      },
      {
        name: "list_team_tasks",
        description:
          "List every task across your WHOLE team (yourself + all members), if you are " +
          "a team lead. Use this when the user asks about work you delegated to a " +
          "subagent — list_tasks with an explicit agent_id only shows one member at a " +
          "time and requires already knowing their ID; this shows all of them at once.",
        inputSchema: {
          type: "object",
          properties: {},
        },
      },
      {
        name: "list_agent_messages",
        description:
          "List recent inter-agent messages involving you. Use this when the user asks " +
          "whether another agent contacted you, replied to you, or sent you a message.",
        inputSchema: {
          type: "object",
          properties: {
            minutes: {
              type: "number",
              description: "How far back to look in minutes. Default: 240.",
            },
          },
        },
      },
      {
        name: "get_agent_conversation",
        description:
          "Read your conversation history with another agent. Use list_team to find the agent ID.",
        inputSchema: {
          type: "object",
          properties: {
            agent_id: {
              type: "string",
              description: "The other agent's ID.",
            },
          },
          required: ["agent_id"],
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
          "If the other agent is busy with a task, the message is queued and this returns immediately.",
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
        name: "complete_onboarding",
        description:
          "Finish YOUR onboarding: record who you are and what you are permanently responsible " +
          "for. Call this as soon as the user answered what your role is and which recurring " +
          "duties you take over — do NOT keep asking afterwards. Every duty becomes a " +
          "Verantwortungsbereich, and from the next proactive run you build your own day from " +
          "them instead of waiting for a todo. At least one duty is required.",
        inputSchema: {
          type: "object",
          properties: {
            role: { type: "string", description: "Your role in one sentence." },
            responsibilities: {
              type: "array",
              description: "Every RECURRING duty the user named. At least one.",
              items: {
                type: "object",
                properties: {
                  title: { type: "string", description: "Short and concrete, e.g. 'Posteingang sichten'." },
                  rhythm: { type: "string", description: "daily | weekly | monthly | continuous" },
                  priority: { type: "string", description: "high | normal | low" },
                  notes: { type: "string", description: "How you know today's pass is done." },
                },
                required: ["title"],
              },
            },
            boundaries: { type: "string", description: "What you must NOT do." },
            notes: { type: "string", description: "Other standing instructions." },
          },
          required: ["responsibilities"],
        },
      },
      {
        name: "tickets",
        description:
          "Read and write the company ticket system (Matrix42 or compatible). Use it to " +
          "look up an existing ticket, list open ones, file a new ticket, or add a comment. " +
          "You can NOT close or delete tickets — a human does that. " +
          "Actions: list | get | create | comment.",
        inputSchema: {
          type: "object",
          properties: {
            action: { type: "string", description: "list | get | create | comment" },
            ticket_id: { type: "string", description: "For get / comment." },
            title: { type: "string", description: "For create." },
            description: { type: "string", description: "For create." },
            priority: { type: "string", description: "For create (optional)." },
            text: { type: "string", description: "For comment." },
            query: { type: "string", description: "For list: system filter expression." },
            limit: { type: "number", description: "For list: max results (default 20)." },
          },
          required: ["action"],
        },
      },
      {
        name: "plan_day",
        description:
          "Write down what you intend to do TODAY so it becomes VISIBLE to the user in the " +
          "agent calendar — instead of living only in your own notes. Call this at the START " +
          "of a proactive run, right after you worked out your plan: pass the blocks in the " +
          "order you mean to work them. Replaces the plan you wrote earlier for that day; " +
          "blocks already running or done are kept. The user can move or drop a block — read " +
          "it back with get_day_plan and respect their changes.",
        inputSchema: {
          type: "object",
          properties: {
            items: {
              type: "array",
              description: "The blocks you plan for the day, in the order you will work them.",
              items: {
                type: "object",
                properties: {
                  title: { type: "string", description: "What you will do — short and concrete." },
                  notes: { type: "string", description: "Optional detail (how you know it is done)." },
                  planned_start: { type: "string", description: "ISO-8601 UTC start, e.g. '2026-08-07T07:30:00Z'. Omit if only the order matters." },
                  estimated_minutes: { type: "number", description: "Rough duration in minutes (default 30)." },
                  source: { type: "string", description: "'responsibility' | 'todo' | 'self'" },
                  priority: { type: "string", description: "high | normal | low — inherit from the responsibility or todo." },
                  todo_id: { type: "number", description: "Link to the todo this block works on, if any." },
                },
                required: ["title"],
              },
            },
            plan_date: { type: "string", description: "Day as YYYY-MM-DD. Default: today (UTC)." },
          },
          required: ["items"],
        },
      },
      {
        name: "get_day_plan",
        description:
          "Read your day plan back — including what the user changed. A block the user dropped " +
          "must NOT be worked on. Use at the start of a run before planning anew.",
        inputSchema: {
          type: "object",
          properties: {
            date: { type: "string", description: "Day as YYYY-MM-DD. Default: today." },
            days: { type: "number", description: "How many days from 'date' (default 1)." },
          },
          required: [],
        },
      },
      {
        name: "create_schedule",
        description:
          "Schedule YOURSELF to run a task later — you choose the timing. Use instead of sleeping/waiting. " +
          "run_in_seconds = ONE-SHOT self follow-up ('look at this again in 30 min' → 1800): fires once, then stops. " +
          "interval_seconds = repeat forever. cron_expression = exact wall-clock times (daily, twice-daily). " +
          "Provide exactly ONE of the three timing options.",
        inputSchema: {
          type: "object",
          properties: {
            name: {
              type: "string",
              description: "Name of the schedule (e.g. 'Re-check PR #290 in 30m', 'Daily status report').",
            },
            prompt: {
              type: "string",
              description: "The task instructions to run when the schedule triggers.",
            },
            run_in_seconds: {
              type: "number",
              minimum: 30,
              description:
                "ONE-SHOT: run once after this many seconds, then auto-disable. " +
                "Examples: 1800=in 30 minutes, 3600=in 1 hour. Use this for 'follow up later', NOT a sleep.",
            },
            interval_seconds: {
              type: "number",
              minimum: 60,
              description:
                "RECURRING: repeat every N seconds forever. Examples: 3600=hourly, 86400=daily. " +
                "Minimum: 60 seconds. Prefer cron_expression for exact times.",
            },
            cron_expression: {
              type: "string",
              description:
                "Optional cron expression for wall-clock schedules. Examples: '0 6 * * *'=every day at 06:00, " +
                "'*/15 * * * *'=every 15 minutes, '0 9 * * 1'=Mondays at 09:00. " +
                "Use this instead of interval_seconds when the time of day matters.",
            },
            timezone: {
              type: "string",
              description:
                "IANA timezone the cron_expression is evaluated in (DST-aware). LEAVE EMPTY " +
                "unless you mean a different zone than your own — without it the server uses " +
                "YOUR timezone, so '0 7 * * *' fires at 07:00 your local time. Setting 'UTC' " +
                "by hand is how a schedule named '(07:00)' ends up firing at 09:00. " +
                "Ignored for interval_seconds schedules.",
            },
          },
          required: ["name", "prompt"],
        },
      },
      {
        name: "schedule_meeting",
        description:
          "Schedule a meeting room between agents — either once at a specific time or recurring via cron. " +
          "Use this after a meeting or task to create follow-up meetings automatically. " +
          "Example: schedule a review meeting in 3 days, or a weekly sync every Monday.",
        inputSchema: {
          type: "object",
          properties: {
            name: {
              type: "string",
              description: "Meeting room name (e.g. 'Follow-up: Q2 Strategie').",
            },
            topic: {
              type: "string",
              description: "The agenda / topic the agents should discuss. Be specific.",
            },
            agent_ids: {
              type: "array",
              items: { type: "string" },
              description: "List of agent IDs to invite (minimum 2). Use list_agents to find IDs.",
            },
            run_at: {
              type: "string",
              description:
                "ISO 8601 datetime for a one-shot meeting (e.g. '2025-04-25T09:00:00Z'). " +
                "Omit for recurring meetings or to start immediately.",
            },
            cron_expression: {
              type: "string",
              description:
                "Cron expression for recurring meetings (e.g. '0 9 * * 1' = every Monday 9am). " +
                "Omit for one-shot meetings.",
            },
            initial_message: {
              type: "string",
              description: "Opening message for the meeting. Optional.",
            },
            use_moderator: {
              type: "boolean",
              description: "Whether to use a virtual moderator. Default true.",
            },
          },
          required: ["name", "topic", "agent_ids"],
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
              type: "string",
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
        name: "trigger_create",
        description:
          "Set yourself up to react to an EVENT instead of polling on a timer — fires a task for " +
          "you when a matching webhook arrives (e.g. a GitHub PR, a Stripe payment, any inbound " +
          "webhook your setup receives). Use this instead of create_schedule when the work is " +
          "event-driven, not time-driven.",
        inputSchema: {
          type: "object",
          properties: {
            name: {
              type: "string",
              description: "Name for the trigger.",
            },
            prompt_template: {
              type: "string",
              description:
                "The prompt to run when the trigger fires. Supports {{payload.field}} " +
                "interpolation from the webhook payload.",
            },
            source_filter: {
              type: "string",
              description: "Only fire for webhooks from this source, e.g. 'github', 'stripe'. Omit to match any source.",
            },
            event_type_filter: {
              type: "string",
              description: "Only fire for this event type, e.g. 'pull_request', 'payment'. Omit to match any type.",
            },
            payload_conditions: {
              type: "object",
              description: "Field:value pairs that must match in the webhook payload, e.g. {\"action\": \"opened\"}. Omit for no extra conditions.",
            },
            priority: {
              type: "number",
              description: "Task priority when the trigger fires (default 5).",
            },
          },
          required: ["name", "prompt_template"],
        },
      },
      {
        name: "trigger_list",
        description: "List all your event triggers.",
        inputSchema: {
          type: "object",
          properties: {},
        },
      },
      {
        name: "trigger_toggle",
        description: "Enable or disable one of your event triggers (does not delete it).",
        inputSchema: {
          type: "object",
          properties: {
            trigger_id: {
              type: "string",
              description: "ID of the trigger to toggle.",
            },
          },
          required: ["trigger_id"],
        },
      },
      {
        name: "trigger_delete",
        description: "Delete one of your event triggers.",
        inputSchema: {
          type: "object",
          properties: {
            trigger_id: {
              type: "string",
              description: "ID of the trigger to delete.",
            },
          },
          required: ["trigger_id"],
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
      {
        name: "rate_task",
        description:
          "Rate your own task performance after completion. ALWAYS call this at the end of every task. " +
          "Give an honest 1-5 star rating and a one-sentence reflection on what went well or could be improved.",
        inputSchema: {
          type: "object",
          properties: {
            rating: {
              type: "integer",
              description: "1-5 stars (1=poor, 3=ok, 5=excellent)",
              minimum: 1,
              maximum: 5,
            },
            reflection: {
              type: "string",
              description: "One sentence: what went well or what could be improved next time",
            },
            ask_feedback: {
              type: "boolean",
              description: "Whether to ask the user for feedback (default: true)",
            },
          },
          required: ["rating", "reflection"],
        },
      },
      {
        name: "create_skill",
        description:
          "Save a reusable skill/solution to the marketplace after completing a task. " +
          "Call this when you've built something that could be reused in future tasks by you or other agents.",
        inputSchema: {
          type: "object",
          properties: {
            title: {
              type: "string",
              description: "Short skill name (e.g. 'PDF Report Generator', 'GitHub PR Workflow')",
            },
            description: {
              type: "string",
              description: "What this skill does (1-2 sentences)",
            },
            solution: {
              type: "string",
              description: "The actual approach, code snippet, or workflow used",
            },
            category: {
              type: "string",
              description: "Category: routine, template, workflow, pattern, recipe, tool (default: pattern)",
              enum: ["routine", "template", "workflow", "pattern", "recipe", "tool"],
            },
            tags: {
              type: "array",
              items: { type: "string" },
              description: "Keywords for search (e.g. ['pdf', 'report', 'python'])",
            },
            task_id: {
              type: "string",
              description: "ID of the current task (from CURRENT_TASK_ID in your prompt) — links skill to task for feedback loop",
            },
          },
          required: ["title", "description", "solution"],
        },
      },
      {
        name: "skill_update",
        description:
          "Update a skill you previously created, e.g. after receiving user feedback. " +
          "Pass the skill_id from the create_skill response. Updates the skill content in the marketplace.",
        inputSchema: {
          type: "object",
          properties: {
            skill_id: {
              type: "integer",
              description: "ID of the skill to update (from the create_skill response)",
            },
            description: {
              type: "string",
              description: "Updated description (optional)",
            },
            solution: {
              type: "string",
              description: "Updated skill content/approach",
            },
            feedback: {
              type: "string",
              description: "What changed and why (e.g. 'User wanted landscape orientation, not portrait')",
            },
          },
          required: ["skill_id", "solution", "feedback"],
        },
      },
      {
        name: "trigger_list",
        description:
          "List all your event triggers — webhook-to-task routing rules that fire automatically " +
          "when external events arrive. Shows name, source filter, event type, conditions, and enabled status.",
        inputSchema: { type: "object", properties: {} },
      },
      {
        name: "trigger_create",
        description:
          "Create an event trigger that automatically starts a task when a matching webhook arrives. " +
          "Use source_filter to match the webhook source (e.g. 'github', 'stripe'). " +
          "Use event_type_filter for a specific event (e.g. 'push', 'payment.succeeded'). " +
          "Use payload_conditions for JSON field matching (e.g. {\"action\": \"opened\"}). " +
          "In prompt_template, use {{field}} to interpolate webhook payload fields.",
        inputSchema: {
          type: "object",
          properties: {
            name: { type: "string", description: "Short trigger name (e.g. 'New GitHub PR')" },
            source_filter: { type: "string", description: "Match webhooks from this source (e.g. 'github'). Omit to match all." },
            event_type_filter: { type: "string", description: "Match this event type (e.g. 'pull_request'). Omit to match all." },
            payload_conditions: {
              type: "object",
              description: "Key/value pairs that must match in the webhook payload (e.g. {\"action\": \"opened\"}). Omit for no conditions.",
            },
            prompt_template: {
              type: "string",
              description: "Task prompt to run when this trigger fires. Use {{field}} for webhook payload interpolation (e.g. 'Review PR: {{pull_request.title}}').",
            },
            priority: { type: "number", minimum: 1, maximum: 10, description: "Task priority (1=highest). Default: 5." },
            model: { type: "string", description: "Model for the triggered task. Omit to use default." },
          },
          required: ["name", "prompt_template"],
        },
      },
      {
        name: "trigger_delete",
        description: "Delete an event trigger by its ID.",
        inputSchema: {
          type: "object",
          properties: {
            trigger_id: { type: "number", description: "ID of the trigger to delete." },
          },
          required: ["trigger_id"],
        },
      },
      {
        name: "trigger_toggle",
        description: "Enable or disable an event trigger by its ID.",
        inputSchema: {
          type: "object",
          properties: {
            trigger_id: { type: "number", description: "ID of the trigger to toggle." },
          },
          required: ["trigger_id"],
        },
      },
      {
        name: "list_apps",
        description:
          "List MY OWN docker-compose apps (the projects under /workspace/projects/) with their " +
          "running status and containers. I have NO docker myself — the platform (orchestrator) " +
          "runs them; use these app_* tools to drive them. Use the app 'path' from here for the " +
          "other app tools.",
        inputSchema: { type: "object", properties: {} },
      },
      {
        name: "app_logs",
        description:
          "Read the container logs of one of MY apps (to debug why it won't start or misbehaves). " +
          "Pass the app 'path' from list_apps.",
        inputSchema: {
          type: "object",
          properties: {
            path: { type: "string", description: "App path in /workspace (from list_apps)." },
            service: { type: "string", description: "Optional: only this compose service." },
            lines: { type: "number", description: "Log lines to fetch (10-1000, default 100)." },
          },
          required: ["path"],
        },
      },
      {
        name: "start_app",
        description:
          "Start one of MY apps via the orchestrator (docker compose up -d --build). Use to bring a " +
          "stopped or newly-created app up. Pass the app 'path' from list_apps.",
        inputSchema: {
          type: "object",
          properties: {
            path: { type: "string", description: "App path in /workspace (from list_apps)." },
          },
          required: ["path"],
        },
      },
      {
        name: "stop_app",
        description:
          "Stop one of MY apps (docker compose down). Pass the app 'path' from list_apps.",
        inputSchema: {
          type: "object",
          properties: {
            path: { type: "string", description: "App path in /workspace (from list_apps)." },
          },
          required: ["path"],
        },
      },
      {
        name: "rebuild_app",
        description:
          "Rebuild one of MY apps from its CURRENT code and restart it (docker compose up -d --build " +
          "--force-recreate). ALWAYS use this after I changed an app's code/config in the workspace — " +
          "a plain start of an already-built app does NOT pick up my changes. Pass the app 'path' " +
          "from list_apps.",
        inputSchema: {
          type: "object",
          properties: {
            path: { type: "string", description: "App path in /workspace (from list_apps)." },
          },
          required: ["path"],
        },
      },
      {
        name: "restart_own_container",
        description:
          "Rebuild and restart MY OWN container from the current agent image/config, preserving my " +
          "full workspace (files, git history, memory, everything on disk). This INTERRUPTS whatever " +
          "I'm currently doing and drops my in-progress conversation turn — ALWAYS tell the user this " +
          "is about to happen BEFORE calling it, never call it silently. Use only when explicitly " +
          "asked to restart/rebuild myself, or when a config/instruction change needs a fresh " +
          "container to take effect. No arguments.",
        inputSchema: {
          type: "object",
          properties: {},
        },
      },
      {
        name: "web_search",
        description:
          "Search the web for information. Use this when you need current data (weather, news, " +
          "prices, facts) or don't know which URL to visit. Returns top search results with titles, " +
          "URLs, and snippets. Uses the admin-configured provider (DuckDuckGo by default, or Brave/" +
          "SerpApi if the admin set an API key under Admin -> Websuche).",
        inputSchema: {
          type: "object",
          properties: {
            query: {
              type: "string",
              description: "Search query (e.g. 'weather Berlin today', 'Python FastAPI tutorial').",
            },
            max_results: {
              type: "number",
              description: "Number of results to return (default: 5, max: 10).",
            },
          },
          required: ["query"],
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

      case "delegate_and_wait": {
        const timeout = Math.min(args.timeout_seconds || 300, 600) * 1000;
        const batchTasks = (args.tasks || []).slice(0, 20).map((t) => ({
          title: t.title,
          prompt: t.prompt,
          priority: t.priority || 5,
          agent_id: t.agent_id || null,
        }));
        const batch = await apiCall("/tasks/batch", {
          method: "POST",
          body: JSON.stringify({ tasks: batchTasks, created_by_agent: AGENT_ID }),
        });
        const taskIds = batch.tasks.map((t) => t.id);

        // Poll until all tasks are done or timeout
        const deadline = Date.now() + timeout;
        const results = {};
        while (Date.now() < deadline) {
          await new Promise((r) => setTimeout(r, 4000));
          const statuses = await Promise.all(
            taskIds.map((id) => apiCall(`/tasks/${id}`).catch(() => null))
          );
          for (const t of statuses) {
            if (t && (t.status === "completed" || t.status === "failed")) {
              results[t.id] = { title: t.title, status: t.status, result: t.result || "(no output)" };
            }
          }
          if (Object.keys(results).length === taskIds.length) break;
        }

        const pending = taskIds.filter((id) => !results[id]);
        const lines = taskIds.map((id) => {
          const r = results[id];
          if (!r) return `  #${id}: still running (timed out)`;
          return `[${r.status}] #${id} "${r.title}"\n${r.result}`;
        });
        // Die Rueckgabe muss sagen, dass das Warten VORBEI ist. Ein Team-Lead hat
        // am 2026-08-13 aus einem fertigen Ergebnis ein "wurde angestossen, laeuft
        // jetzt" gemacht; der Mensch wartete 18 Minuten auf etwas, das schon fertig
        // war. Ohne Emojis — harte Vorgabe fuer nutzersichtbaren Text.
        const done = Object.keys(results).length;
        const head = pending.length === 0
          ? `FERTIG: alle ${taskIds.length} Auftraege sind abgeschlossen. Das hier ist ` +
            `das ENDERGEBNIS, kein Zwischenstand. Gib es dem Menschen wieder und sage ` +
            `ausdruecklich, dass die Arbeit erledigt ist. Schreibe NICHT, dass etwas ` +
            `"angestossen" wurde oder "jetzt laeuft".`
          : `TEILWEISE FERTIG: ${done} von ${taskIds.length} Auftraegen sind zurueck, ` +
            `${pending.length} laufen noch. Berichte beides getrennt.`;
        return {
          content: [{
            type: "text",
            text: `${head}\n\n` + wrapData("subtask-results", lines.join("\n\n---\n\n")),
          }],
        };
      }

      case "get_tasks_status": {
        const ids = (args.task_ids || []).slice(0, 50);
        const statuses = await Promise.all(
          ids.map((id) => apiCall(`/tasks/${id}`).catch(() => ({ id, status: "error", result: "not found" })))
        );
        const lines = statuses.map((t) => {
          const cost = t.cost_usd ? ` (${t.cost_usd.toFixed(4)} USD)` : "";
          return `#${t.id} [${t.status}]${cost}: ${t.title || ""}${t.result ? `\n  -> ${truncatePreservingWords(t.result, 1000)}` : ""}`;
        });
        return {
          content: [{
            type: "text",
            text: `${statuses.length} tasks:\n\n${lines.join("\n\n")}`,
          }],
        };
      }

      case "create_task_batch": {
        const batchTasks = (args.tasks || []).map((t) => ({
          title: t.title,
          prompt: t.prompt,
          priority: t.priority || 5,
          agent_id: t.agent_id || null,
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

      case "list_my_team": {
        const result = await apiCall("/teams/mine");
        const teams = result.teams || [];
        if (teams.length === 0) {
          return {
            content: [{
              type: "text",
              text: "You are not part of any team. Use list_team to see the agents available in the system.",
            }],
          };
        }
        const blocks = teams.map((t) => {
          const lines = (t.members || []).map((m) => {
            const tags = [m.is_lead ? "LEAD" : null, m.is_me ? "you" : null].filter(Boolean).join(", ");
            return `  - ${m.name} (id: ${m.id}${tags ? `, ${tags}` : ""}): ${m.role || "no role set"}`;
          });
          return `Team "${t.name}" [${t.team_id}]${t.i_am_lead ? " — you are the LEAD" : ""}\n${lines.join("\n")}`;
        });
        return {
          content: [{
            type: "text",
            text: wrapData("my-teams", blocks.join("\n\n")),
          }],
        };
      }

      case "list_team_tasks": {
        const teams = await apiCall("/teams/");
        const myTeam = (teams.teams || []).find((t) => t.lead_agent_id === AGENT_ID);
        if (!myTeam) {
          return {
            content: [{
              type: "text",
              text: "You are not the lead of any team, so there is no team-wide task list. " +
                    "Use list_tasks with an explicit agent_id to check a single agent's tasks.",
            }],
          };
        }
        const result = await apiCall(`/teams/${myTeam.id}/tasks`);
        if (!result.tasks || result.tasks.length === 0) {
          return {
            content: [{ type: "text", text: `No tasks yet across team "${myTeam.name}".` }],
          };
        }
        const lines = result.tasks.map(
          (t) => `[${t.status}] #${t.id}: ${t.title} — agent: ${t.agent_id}${t.agent_id === AGENT_ID ? " (you)" : ""}`
        );
        return {
          content: [
            {
              type: "text",
              text: `Team "${myTeam.name}" — ${result.tasks.length} tasks across ${myTeam.member_agent_ids.length} members:\n\n${lines.join("\n")}`,
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
            `${a.name} (id: ${a.id}, role: ${a.role || "general"}, status: ${a.state || a.status || "unknown"})`
        );
        return {
          content: [
            {
              type: "text",
              text: `Team (${result.agents.length} agents):\n\n${wrapData("agent-directory", lines.join("\n"))}`,
            },
          ],
        };
      }

      case "list_agent_messages": {
        const minutes = Number(args.minutes || 240);
        const result = await apiCall(`/agents/team/messages?minutes=${encodeURIComponent(minutes)}`);
        if (!result.messages || result.messages.length === 0) {
          return {
            content: [{ type: "text", text: `No inter-agent messages in the last ${minutes} minutes.` }],
          };
        }
        const lines = result.messages.map((m) => {
          const direction = m.to === AGENT_ID ? "from" : "to";
          const other = direction === "from" ? m.from_name : m.to;
          return `${m.timestamp} ${direction} ${other}: ${String(m.text || "").replace(/\n/g, " ")}`;
        });
        return {
          content: [{
            type: "text",
            text: `Recent inter-agent messages involving ${AGENT_NAME}:\n\n${wrapData("agent-messages", lines.join("\n"))}`,
          }],
        };
      }

      case "get_agent_conversation": {
        const result = await apiCall(
          `/agents/team/conversation?agent_a=${encodeURIComponent(AGENT_ID)}&agent_b=${encodeURIComponent(args.agent_id)}`
        );
        if (!result.messages || result.messages.length === 0) {
          return {
            content: [{ type: "text", text: `No conversation with ${args.agent_id} yet.` }],
          };
        }
        const lines = result.messages.slice(-20).map(
          (m) => `[${m.timestamp}] ${m.from_name || m.from_id}: ${m.text}`
        );
        return {
          content: [{
            type: "text",
            text: `Conversation with ${args.agent_id}:\n\n${wrapData("agent-conversation", lines.join("\n\n"))}`,
          }],
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
        const sendResult = await apiCall(`/agents/${args.agent_id}/message`, {
          method: "POST",
          body: JSON.stringify({
            from_agent_id: AGENT_ID,
            from_name: AGENT_NAME,
            text: args.message,
            message_type: args.message_type || "question",
          }),
        });

        if (sendResult.will_reply_later) {
          const task = sendResult.target_current_task
            ? ` (current task: ${sendResult.target_current_task})`
            : "";
          return {
            content: [{
              type: "text",
              text:
                `Message queued for agent ${args.agent_id}. ` +
                `They are currently busy${task}, so the reply will arrive later. ` +
                `message_id: ${sendResult.message_id}`,
            }],
          };
        }

        // Step 3: Poll for reply (up to 45s)
        const pollResult = await apiCall(
          `/agents/team/poll-reply?from_agent_id=${args.agent_id}&to_agent_id=${AGENT_ID}&since_id=${sinceId}&timeout=45`
        );

        if (pollResult.found && pollResult.message) {
          return {
            content: [{
              type: "text",
              text:
                `Reply from ${pollResult.message.from_name}:\n\n` +
                wrapData("agent-message", pollResult.message.text),
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

      case "complete_onboarding": {
        const duties = Array.isArray(args.responsibilities) ? args.responsibilities : [];
        if (duties.length === 0) {
          throw new Error(
            "Provide at least one recurring duty in 'responsibilities' — without one you would " +
            "be onboarded but still have no assignment."
          );
        }
        const result = await apiCall(`/agents/${AGENT_ID}/onboarding/complete`, {
          method: "POST",
          body: JSON.stringify({
            role: args.role || "",
            boundaries: args.boundaries || "",
            responsibilities: duties,
            notes: args.notes || "",
          }),
        });
        const titles = (result.responsibilities || []).map((d) => d.title).filter(Boolean);
        return {
          content: [
            {
              type: "text",
              text:
                "Einrichtung abgeschlossen. Deine Verantwortungsbereiche: " +
                titles.join(", ") +
                ". Ab dem nächsten proaktiven Lauf planst du deinen Tag daraus selbst.",
            },
          ],
        };
      }

      case "tickets": {
        const action = String(args.action || "").toLowerCase();
        if (action === "list") {
          const limit = args.limit || 20;
          const q = args.query ? `&query=${encodeURIComponent(args.query)}` : "";
          const res = await apiCall(`/tickets/?limit=${limit}${q}`);
          const rows = res.tickets || [];
          return {
            content: [{
              type: "text",
              text: rows.length
                ? rows.map((t) => `- [${t.id}] ${t.title} (${t.status || "ohne Status"})`).join("\n")
                : "No tickets found.",
            }],
          };
        }
        if (action === "get") {
          if (!args.ticket_id) throw new Error("ticket_id is required for get.");
          const t = await apiCall(`/tickets/${encodeURIComponent(args.ticket_id)}`);
          return {
            content: [{
              type: "text",
              text: Object.entries(t).filter(([, v]) => v).map(([k, v]) => `${k}: ${v}`).join("\n"),
            }],
          };
        }
        if (action === "create") {
          if (!args.title) throw new Error("title is required for create.");
          const created = await apiCall("/tickets/", {
            method: "POST",
            body: JSON.stringify({
              title: args.title,
              description: args.description || "",
              priority: args.priority || "",
            }),
          });
          return { content: [{ type: "text", text: `Ticket created: ${created.id || "?"}` }] };
        }
        if (action === "comment") {
          if (!args.ticket_id || !args.text) throw new Error("ticket_id and text are required.");
          await apiCall(`/tickets/${encodeURIComponent(args.ticket_id)}/comment`, {
            method: "POST",
            body: JSON.stringify({ text: args.text }),
          });
          return { content: [{ type: "text", text: `Comment added to ${args.ticket_id}.` }] };
        }
        throw new Error("Unknown action. Use list | get | create | comment.");
      }

      case "plan_day": {
        const items = Array.isArray(args.items) ? args.items : [];
        if (items.length === 0) throw new Error("Provide at least one planned block in 'items'.");
        const body = { items };
        if (args.plan_date) body.plan_date = args.plan_date;
        const result = await apiCall(`/agents/${AGENT_ID}/day-plan`, {
          method: "PUT",
          body: JSON.stringify(body),
        });
        const written = (result.items || []).length;
        return {
          content: [
            {
              type: "text",
              text:
                `Tagesplan für ${result.plan_date} gespeichert: ${written} Block/Blöcke. ` +
                "Der Nutzer sieht ihn jetzt im Kalender und kann Blöcke verschieben oder streichen.",
            },
          ],
        };
      }

      case "get_day_plan": {
        const params = new URLSearchParams();
        if (args.date) params.set("date", args.date);
        if (args.days) params.set("days", String(args.days));
        const qs = params.toString() ? `?${params.toString()}` : "";
        const result = await apiCall(`/agents/${AGENT_ID}/day-plan${qs}`);
        const items = result.items || [];
        if (items.length === 0) {
          return { content: [{ type: "text", text: "Für diesen Tag ist noch nichts geplant." }] };
        }
        const marks = { done: "erledigt", running: "läuft", dropped: "GESTRICHEN" };
        const lines = items.map((it) => {
          const when = (it.planned_start || "").slice(11, 16) || "--:--";
          const mark = marks[it.status] || "geplant";
          return `- [${mark}] ${when} (${it.estimated_minutes} Min) ${it.title}` +
            (it.notes ? ` — ${it.notes}` : "");
        });
        return { content: [{ type: "text", text: "Tagesplan:\n" + lines.join("\n") }] };
      }

      case "create_schedule": {
        if (!args.run_in_seconds && !args.interval_seconds && !args.cron_expression) {
          throw new Error("Provide run_in_seconds (one-shot), interval_seconds, or cron_expression.");
        }
        const body = {
          name: args.name,
          prompt: args.prompt,
          agent_id: AGENT_ID,
        };
        if (args.run_in_seconds) {
          body.run_in_seconds = args.run_in_seconds;  // one-shot; backend sets interval 0 + disables after firing
        } else if (args.cron_expression) {
          body.cron_expression = args.cron_expression;
          body.interval_seconds = 0;
          if (args.timezone) body.timezone = args.timezone;
        } else {
          body.interval_seconds = args.interval_seconds;
        }
        const result = await apiCall("/schedules/", {
          method: "POST",
          body: JSON.stringify(body),
        });
        const timing = result.cron_expression
          ? `cron: ${result.cron_expression}`
          : args.run_in_seconds
          ? `one-shot at ${result.next_run_at}`
          : `interval: ${result.interval_seconds}s`;
        const nextRun = result.next_run_at ? `, next: ${result.next_run_at}` : "";
        return {
          content: [
            {
              type: "text",
              text: `Schedule created: "${result.name}" (id: ${result.id}, ${timing}${nextRun}).`,
            },
          ],
        };
      }

      case "schedule_meeting": {
        const { name, topic, agent_ids, run_at, cron_expression, initial_message, use_moderator } = args;
        const body = {
          name,
          topic,
          agent_ids,
          use_moderator: use_moderator !== false,
          ...(run_at && { run_at }),
          ...(cron_expression && { cron_expression }),
          ...(initial_message && { initial_message }),
        };
        const result = await apiCall("/meeting-rooms/schedule", {
          method: "POST",
          body: JSON.stringify(body),
        });
        const when = cron_expression
          ? `recurring (${cron_expression})`
          : run_at
          ? `once at ${run_at}`
          : "immediately";
        return {
          content: [{
            type: "text",
            text: `Meeting scheduled (${when}): "${name}" — schedule ID: ${result.schedule_id}, next run: ${result.next_run_at}`,
          }],
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
          (s) => {
            const status = (s.enabled ?? s.active) ? "active" : "paused";
            const timing = s.cron_expression
              ? `cron ${s.cron_expression}`
              : `every ${s.interval_seconds}s`;
            const next = s.next_run_at ? `, next ${s.next_run_at}` : "";
            return `[${status}] #${s.id}: ${s.name} (${timing}${next})`;
          }
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

      case "trigger_create": {
        const body = {
          name: args.name,
          prompt_template: args.prompt_template,
          source_filter: args.source_filter,
          event_type_filter: args.event_type_filter,
          payload_conditions: args.payload_conditions,
          priority: args.priority ?? 5,
        };
        const result = await apiCall("/event-triggers/for-agent", {
          method: "POST",
          body: JSON.stringify(body),
        });
        return {
          content: [{
            type: "text",
            text: `Trigger created: "${result.name}" (id: ${result.id}).`,
          }],
        };
      }

      case "trigger_list": {
        const result = await apiCall("/event-triggers/for-agent");
        if (!result.triggers || result.triggers.length === 0) {
          return {
            content: [{ type: "text", text: "No event triggers found." }],
          };
        }
        const lines = result.triggers.map((t) => {
          const status = t.enabled ? "enabled" : "disabled";
          const match = [
            t.source_filter && `source=${t.source_filter}`,
            t.event_type_filter && `event=${t.event_type_filter}`,
          ].filter(Boolean).join(", ") || "any event";
          return `[${status}] #${t.id}: ${t.name} (${match}, fired ${t.fire_count ?? 0}x)`;
        });
        return {
          content: [{
            type: "text",
            text: `${result.triggers.length} event triggers:\n\n${lines.join("\n")}`,
          }],
        };
      }

      case "trigger_toggle": {
        const result = await apiCall(`/event-triggers/for-agent/${args.trigger_id}/toggle`, {
          method: "PATCH",
        });
        return {
          content: [{
            type: "text",
            text: `Trigger ${args.trigger_id} ${result.enabled ? "enabled" : "disabled"}.`,
          }],
        };
      }

      case "trigger_delete": {
        await apiCall(`/event-triggers/for-agent/${args.trigger_id}`, { method: "DELETE" });
        return {
          content: [{ type: "text", text: `Trigger ${args.trigger_id} deleted.` }],
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

      case "rate_task": {
        const body = {
          rating: args.rating,
          reflection: args.reflection,
          ask_feedback: args.ask_feedback !== undefined ? args.ask_feedback : true,
        };
        const result = await apiCall("/ratings/task-self-rate", {
          method: "POST",
          body: JSON.stringify(body),
        });
        return {
          content: [{
            type: "text",
            text: `Task rated: ${"★".repeat(args.rating)}${"☆".repeat(5 - args.rating)} (${args.rating}/5). Reflection saved. ${result.ask_feedback ? "User will be asked for feedback." : ""}`,
          }],
        };
      }

      case "create_skill": {
        // Map free-form category to valid DB enum values
        const VALID_CATEGORIES = ["routine", "template", "workflow", "pattern", "recipe", "tool"];
        const rawCategory = (args.category || "pattern").toLowerCase();
        const categoryMap = {
          coding: "pattern", code: "pattern", programming: "pattern", dev: "pattern",
          web: "pattern", data: "routine", communication: "routine",
          research: "workflow", other: "pattern",
        };
        const category = VALID_CATEGORIES.includes(rawCategory)
          ? rawCategory
          : (categoryMap[rawCategory] || "pattern");

        const result = await apiCall("/skills/agent/propose", {
          method: "POST",
          body: JSON.stringify({
            name: args.title.toLowerCase().replace(/\s+/g, "-").replace(/[^a-z0-9-]/g, ""),
            description: args.description,
            content: args.solution,
            category,
            task_id: args.task_id || null,
          }),
        });
        return {
          content: [{
            type: "text",
            text: `Skill created: "${result.name}" (id: ${result.id}). It's now in the marketplace. If you get feedback on this task, the skill will be updated automatically.`,
          }],
        };
      }

      case "skill_update": {
        const result = await apiCall(`/skills/agent/${args.skill_id}`, {
          method: "PATCH",
          body: JSON.stringify({
            description: args.description || null,
            content: args.solution,
            feedback: args.feedback,
          }),
        });
        return {
          content: [{
            type: "text",
            text: `Skill "${result.name}" (id: ${result.id}) updated. Changelog: ${args.feedback}`,
          }],
        };
      }

      case "trigger_list": {
        const result = await apiCall("/event-triggers/for-agent");
        if (!result.triggers?.length) {
          return { content: [{ type: "text", text: "No event triggers configured." }] };
        }
        const lines = result.triggers.map((t) =>
          `#${t.id} [${t.enabled ? "✓ enabled" : "✗ disabled"}] "${t.name}" | source: ${t.source_filter || "*"} | event: ${t.event_type_filter || "*"} | fired: ${t.fire_count}x\n  prompt: ${t.prompt_template.slice(0, 80)}${t.prompt_template.length > 80 ? "…" : ""}`
        );
        return { content: [{ type: "text", text: wrapData("event-triggers", `${result.total} trigger(s):\n\n${lines.join("\n\n")}`) }] };
      }

      case "trigger_create": {
        const result = await apiCall("/event-triggers/for-agent", {
          method: "POST",
          body: JSON.stringify({
            name: args.name,
            agent_id: AGENT_ID,
            source_filter: args.source_filter || null,
            event_type_filter: args.event_type_filter || null,
            payload_conditions: args.payload_conditions || null,
            prompt_template: args.prompt_template,
            priority: args.priority || 5,
            model: args.model || null,
            enabled: true,
          }),
        });
        return {
          content: [{
            type: "text",
            text: `Trigger created: #${result.id} "${result.name}" — fires when ${result.source_filter || "any"} sends ${result.event_type_filter || "any event"}.`,
          }],
        };
      }

      case "trigger_delete": {
        await apiCall(`/event-triggers/for-agent/${args.trigger_id}`, { method: "DELETE" });
        return { content: [{ type: "text", text: `Trigger #${args.trigger_id} deleted.` }] };
      }

      case "trigger_toggle": {
        const result = await apiCall(`/event-triggers/for-agent/${args.trigger_id}/toggle`, { method: "PATCH" });
        return {
          content: [{
            type: "text",
            text: `Trigger #${result.id} "${result.name}" is now ${result.enabled ? "enabled" : "disabled"}.`,
          }],
        };
      }

      case "list_apps": {
        const result = await apiCall(`/agent-apps`);
        const apps = result.apps || [];
        if (apps.length === 0) {
          return { content: [{ type: "text", text: "Du hast noch keine Apps (docker-compose-Projekte) in /workspace/projects/." }] };
        }
        const lines = apps.map((a) => {
          const svc = (a.services || []).map((s) => s.name).join(", ");
          const conts = (a.containers || []).length;
          const url = a.url ? `\n    Link (an User geben): ${a.url}` : "";
          return `- ${a.name} (path: ${a.path}) — ${a.status}${conts ? `, ${conts} Container` : ""}${svc ? ` [services: ${svc}]` : ""}${url}`;
        });
        return { content: [{ type: "text", text: `Meine Apps:\n${lines.join("\n")}\n\nHINWEIS: Wenn der User den Link/die URL zu einer App will, gib GENAU die obige "Link"-URL weiter (das ist der Plattform-Link, von überall erreichbar nach AI-Employee-Login). NIEMALS localhost, Host-Ports oder "docker compose"/ZIP nennen.` }] };
      }

      case "app_logs": {
        const q = new URLSearchParams({ path: args.path });
        if (args.service) q.set("service", args.service);
        if (args.lines) q.set("lines", String(args.lines));
        const result = await apiCall(`/agent-apps/logs?${q.toString()}`);
        const logs = result.logs || [];
        if (logs.length === 0) {
          return { content: [{ type: "text", text: `Keine laufenden Container/Logs für „${args.path}".` }] };
        }
        const text = logs.map((l) => `[${l.service}] ${l.line}`).join("\n");
        return { content: [{ type: "text", text: wrapData("app-logs", text.slice(-8000)) }] };
      }

      case "start_app": {
        const q = new URLSearchParams({ path: args.path });
        const result = await apiCall(`/agent-apps/up?${q.toString()}`, { method: "POST" });
        const conts = (result.containers || []).length;
        const url = result.url ? ` Link für den User: ${result.url}` : "";
        return { content: [{ type: "text", text: `App „${args.path}" gestartet (${conts} Container, ${result.status}).${url}` }] };
      }

      case "stop_app": {
        const q = new URLSearchParams({ path: args.path });
        const result = await apiCall(`/agent-apps/down?${q.toString()}`, { method: "POST" });
        return { content: [{ type: "text", text: `App „${args.path}" gestoppt (${result.status}).` }] };
      }

      case "rebuild_app": {
        const q = new URLSearchParams({ path: args.path });
        const result = await apiCall(`/agent-apps/rebuild?${q.toString()}`, { method: "POST" });
        const conts = (result.containers || []).length;
        const url = result.url ? ` Link für den User: ${result.url}` : "";
        return { content: [{ type: "text", text: `App „${args.path}" neu gebaut und gestartet (${conts} Container, ${result.status}).${url}` }] };
      }

      case "restart_own_container": {
        await apiCall(`/agent-apps/restart-self`, { method: "POST" });
        return { content: [{ type: "text", text: "Mein Container wird gerade neu gebaut und startet gleich neu. Mein Workspace bleibt erhalten." }] };
      }

      case "web_search": {
        const query = (args.query || "").trim();
        if (!query) {
          return { content: [{ type: "text", text: "Error: query cannot be empty" }] };
        }
        const maxResults = Math.min(Number(args.max_results) || 5, 10);
        const result = await apiCall(`/agent-search/web`, {
          method: "POST",
          body: JSON.stringify({ query, max_results: maxResults }),
        });
        const items = result.results || [];
        if (items.length === 0) {
          return { content: [{ type: "text", text: `No results found for '${query}'. Try different search terms.` }] };
        }
        const blocks = items.map((r) => `**${r.title || ""}**\n${r.url || ""}\n${r.snippet || ""}`);
        return { content: [{ type: "text", text: `Search results for '${query}':\n\n${blocks.join("\n\n---\n\n")}` }] };
      }

      default:
        throw new Error(`Unknown tool: ${name}`);
    }
  });

  return server;
}


await startServer("orchestrator", buildServer);

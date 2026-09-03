#!/usr/bin/env node
/**
 * MCP Notification Server - Send notifications and request approvals.
 *
 * Allows agents to notify the user (via Web UI, Telegram) and request
 * approval for critical actions. Can be used with any MCP client.
 *
 * Environment:
 *   ORCHESTRATOR_URL - Base URL of the orchestrator (default: http://orchestrator:8000)
 *   AGENT_ID         - ID of the agent using this server
 */

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { startServer } from "./_transport.mjs";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import fs from "node:fs";
import path from "node:path";

const API = `${process.env.ORCHESTRATOR_URL || "http://orchestrator:8000"}/api/v1`;
const AGENT_ID = process.env.AGENT_ID || "unknown";
const AGENT_TOKEN = process.env.AGENT_TOKEN || "";

function wrapData(source, content) {
  return `[EXTERNAL-DATA source="${source}"]\n${content}\n[/EXTERNAL-DATA]`;
}

function guessMediaType(filename) {
  const ext = path.extname(filename).toLowerCase();
  const types = {
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".json": "application/json",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".zip": "application/zip",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
  };
  return types[ext] || "application/octet-stream";
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

// Eine Fabrik statt einer Modul-Instanz: im HTTP-Modus bedient ein Prozess
// mehrere gleichzeitige Laeufe, und ein `Server` laesst sich nur an genau einen
// Transport binden (siehe _transport.mjs).
export function buildServer() {
  const server = new Server(
    { name: "mcp-notifications", version: "1.0.0" },
    { capabilities: { tools: {} } }
  );

  // --- List available tools ---
  server.setRequestHandler(ListToolsRequestSchema, async () => ({
    tools: [
      {
        name: "notify_user",
        description:
          "Send a notification to the user. The notification appears in the Web UI " +
          "notification center. High/urgent priority notifications are also forwarded to Telegram. " +
          "Use this when you complete tasks, encounter errors, or have important updates.",
        inputSchema: {
          type: "object",
          properties: {
            title: {
              type: "string",
              description: "Short notification title (e.g. 'Task completed', 'Error in deployment').",
            },
            message: {
              type: "string",
              description: "Detailed notification message. Can be multi-line.",
            },
            priority: {
              type: "string",
              enum: ["low", "normal", "high", "urgent"],
              description:
                "Notification priority. low=informational, normal=standard, " +
                "high=sent to Telegram too, urgent=Telegram + flashing badge. Default: normal.",
            },
            type: {
              type: "string",
              enum: ["info", "warning", "error", "success"],
              description:
                "Notification type for visual styling. info=blue, warning=amber, " +
                "error=red, success=green. Default: info.",
            },
            target_channel: {
              type: "string",
              enum: ["webapp", "ios", "telegram", "all"],
              description:
                "Preferred delivery channel. Use the current chat channel unless the user asks otherwise.",
            },
            is_checkin: {
              type: "boolean",
              description:
                "Set true ONLY for a proactive 'nothing left to do, checking in with a suggestion' " +
                "notification (PROACTIVE_PROMPT STEP 3). Server-enforced to at most once per 12h per " +
                "agent — extra check-ins in the same window are silently dropped. Do NOT set this for " +
                "real accomplishments, results, or actionable problems; those are never rate-limited.",
            },
          },
          required: ["title"],
        },
      },
      {
        name: "send_telegram",
        description:
          "Send a direct message to the user via Telegram. Use this for live progress updates, " +
          "intermediate results, and status messages DURING work. Unlike notify_user (which goes " +
          "to the notification center), this goes DIRECTLY to Telegram as a chat message. " +
          "Use this frequently to keep the user informed about what you're doing: " +
          "e.g. 'Step 1/3 done: Database schema created', 'Building frontend...', " +
          "'Found 3 issues, fixing now'. The user expects regular updates!",
        inputSchema: {
          type: "object",
          properties: {
            message: {
              type: "string",
              description:
                "The message to send via Telegram. Supports basic formatting. " +
                "Keep it concise but informative. Use emojis for visual structure.",
            },
          },
          required: ["message"],
        },
      },
      {
        name: "request_approval",
        description:
          "Request explicit user approval before taking a critical action. " +
          "This creates a special notification with clickable options in the UI. " +
          "ALWAYS use this before: sending emails, deleting files, making purchases, " +
          "calling external APIs with side effects, or any irreversible action. " +
          "The approval notification is sent with high priority (Telegram included).",
        inputSchema: {
          type: "object",
          properties: {
            question: {
              type: "string",
              description:
                "The question to ask the user (e.g. 'Shall I send this email to john@example.com?').",
            },
            options: {
              type: "array",
              items: { type: "string" },
              minItems: 2,
              maxItems: 5,
              description:
                "The options to present to the user (e.g. ['Send now', 'Edit first', 'Cancel']). " +
                "First option is visually highlighted as the primary action.",
            },
            context: {
              type: "string",
              description:
                "Additional context to help the user decide (e.g. email body preview, file list, cost estimate).",
            },
            target_channel: {
              type: "string",
              enum: ["webapp", "ios", "telegram", "all"],
              description: "Preferred delivery channel for the approval prompt.",
            },
          },
          required: ["question", "options"],
        },
      },
      {
        name: "present_view",
        description:
          "Ask the user something with a PICTURE instead of a list of words, and WAIT " +
          "for the answer. Blocks exactly like request_approval and returns the choice.\n\n" +
          "Use it when the answer is easier to point at than to describe — choosing " +
          "between images you generated is the clear case. If plain words do the job, " +
          "use request_approval; a view for a yes/no question is just slower.\n\n" +
          "The view itself lives in the web UI; you pick one by name and hand it data.\n" +
          "  image_choice — several images side by side, the user picks one. " +
          "data: {\"images\": [{\"path\": \"/workspace/...\", \"label\": \"...\"}]}. " +
          "Give FILE PATHS in your workspace, never image content.\n\n" +
          "Always pass `options` too: the same question in plain words. Telegram, the " +
          "phone app and voice-only cannot draw a view — without options those users " +
          "are stuck with a question they cannot answer, and you wait until timeout.",
        inputSchema: {
          type: "object",
          properties: {
            view: {
              type: "string",
              enum: ["image_choice"],
              description: "Which view to show.",
            },
            data: {
              type: "object",
              description: "The view's payload — see the description for the expected shape.",
            },
            question: {
              type: "string",
              description:
                "The question in words. Shown above the view and used wherever the view cannot be drawn.",
            },
            options: {
              type: "array",
              items: { type: "string" },
              description:
                "The same choices in plain words — fallback for Telegram, phone and voice. " +
                "Same order as the view's items.",
            },
            context: { type: "string", description: "Additional context for the user." },
          },
          required: ["view", "data", "question"],
        },
      },
      {
        name: "escalate_if_unsure",
        description:
          "Report how confident you are (0-100) BEFORE acting on an uncertain decision. " +
          "The SERVER decides whether that is enough: if your confidence is at or above " +
          "the operator's threshold, this returns immediately and costs nothing — nobody " +
          "is bothered. Only below the threshold does it hand the decision to a human and " +
          "BLOCK until they answer. " +
          "Use this whenever you would otherwise GUESS: ambiguous instructions, several " +
          "plausible readings, missing information you cannot look up, or an irreversible " +
          "step you are not sure about. A guessed result is worse than a question — it " +
          "looks like work and is not. " +
          "Do NOT use it for actions that are simply risky but clear: that is request_approval.",
        inputSchema: {
          type: "object",
          properties: {
            confidence: {
              type: "number",
              description:
                "How sure you are, 0-100 (0.0-1.0 is also accepted). Be honest — " +
                "inflating this defeats the entire mechanism.",
            },
            question: {
              type: "string",
              description:
                "What you would ask the human. State the actual decision, not 'is this ok?'.",
            },
            context: {
              type: "string",
              description:
                "Why you are unsure and what the options are — everything the human " +
                "needs to decide without asking you back.",
            },
            options: {
              type: "array",
              items: { type: "string" },
              description: "The concrete choices, if there are distinct ones.",
            },
            task_id: {
              type: "string",
              description: "The task this decision belongs to, if any.",
            },
          },
          required: ["confidence", "question"],
        },
      },
      {
        name: "present_file",
        description:
          "Show a generated or prepared file to the user as a downloadable chat attachment. " +
          "Use this after creating PDFs, DOCX, spreadsheets, ZIPs, or other deliverables in /workspace.",
        inputSchema: {
          type: "object",
          properties: {
            path: {
              type: "string",
              description: "Path to the file, absolute or workspace-relative.",
            },
            caption: {
              type: "string",
              description: "Optional short caption shown with the attachment.",
            },
          },
          required: ["path"],
        },
      },
    ],
  }));

  // --- Handle tool calls ---
  server.setRequestHandler(CallToolRequestSchema, async (request) => {
    const { name, arguments: args } = request.params;

    switch (name) {
      case "notify_user": {
        const result = await apiCall("/notifications/", {
          method: "POST",
          body: JSON.stringify({
            agent_id: AGENT_ID,
            type: args.type || "info",
            title: args.title,
            message: args.message || "",
            priority: args.priority || "normal",
            meta: {
              target_channel: args.target_channel || "webapp",
              is_checkin: !!args.is_checkin,
            },
          }),
        });
        if (result.suppressed) {
          return {
            content: [{
              type: "text",
              text: "Notification suppressed: you already sent a check-in within the last 12h.",
            }],
          };
        }
        return {
          content: [
            {
              type: "text",
              text: `Notification sent (id: ${result.id}, priority: ${result.priority}). ` +
                (result.priority === "high" || result.priority === "urgent"
                  ? "Also forwarded to Telegram."
                  : "Visible in Web UI."),
            },
          ],
        };
      }

      case "send_telegram": {
        const result = await apiCall(`/agents/${AGENT_ID}/telegram/send`, {
          method: "POST",
          body: JSON.stringify({
            message: args.message,
          }),
        });
        return {
          content: [
            {
              type: "text",
              text: result.sent_to > 0
                ? `Telegram message sent to ${result.sent_to} user(s).`
                : "No authorized Telegram users found. Message not delivered.",
            },
          ],
        };
      }

      case "present_view":
      case "request_approval": {
        // Eine Ansicht ist eine Rueckfrage, die anders aussieht: derselbe
        // Endpunkt, dasselbe Anhalten, derselbe Rueckweg. Ein zweiter Zweig mit
        // eigener Warteschleife wuerde beim ersten Umbau auseinanderlaufen.
        const istAnsicht = name === "present_view";

        // Ohne Wortoptionen waere der Nutzer auf Telegram, Telefon und im reinen
        // Sprachbetrieb mit einer Frage allein, die er dort nicht beantworten
        // kann — und der Agent wartete bis zur Zeitgrenze. Notfalls aus den
        // Bildbeschriftungen ableiten.
        let optionen = args.options;
        if (istAnsicht && (!optionen || !optionen.length)) {
          const bilder = (args.data && args.data.images) || [];
          optionen = bilder.map((b, i) => String((b && b.label) || `Bild ${i + 1}`));
        }

        // Post to the proper approvals endpoint (persisted in DB, shown on Approvals page)
        const result = await apiCall("/approvals/request", {
          method: "POST",
          body: JSON.stringify({
            question: args.question,
            options: (optionen && optionen.length) ? optionen : ["Approve", "Deny"],
            context: args.context || "",
            // Eine Ansicht fuehrt nichts aus, sie zeigt und wartet — sie als
            // hohes Risiko zu melden wuerde die Dringlichkeitsstufen abstumpfen.
            risk_level: istAnsicht ? "low" : "high",
            target_channel: args.target_channel || "all",
            ...(istAnsicht ? { view: { name: args.view, data: args.data || {} } } : {}),
          }),
        });

        const approvalId = result.approval_id;

        // Poll /approvals/check/{id} — up to 10 minutes
        const startTime = Date.now();
        const maxWait = 10 * 60 * 1000;
        let decision = null;
        while (Date.now() - startTime < maxWait) {
          await new Promise(r => setTimeout(r, 4000));
          try {
            const poll = await apiCall(`/approvals/check/${approvalId}`);
            if (poll.status === "approved" || poll.status === "denied") {
              decision = poll;
              break;
            }
          } catch (e) {
            // continue polling
          }
        }

        if (decision) {
          const approved = decision.status === "approved";
          // Die Antwort des Nutzers wurde bisher NUR bei Ablehnung weitergegeben.
          // Bei einer Rueckfrage mit Antwortmoeglichkeiten ist sie aber der ganze
          // Punkt: „genehmigt" beantwortet die Frage nicht, welche Option gemeint
          // war. Der Custom-LLM-Weg liest `user_response` seit jeher als die
          // Wahl — hier fehlte es, und damit war die Faehigkeit nicht in allen
          // Laufzeiten gleich.
          const antwort = (decision.user_response || "").trim();
          const istNurBestaetigung = /^Approved by /.test(antwort);
          const gewaehlt = antwort && !istNurBestaetigung
            ? ` Antwort des Nutzers: "${antwort}" — richte dich danach.`
            : "";
          return {
            content: [{
              type: "text",
              text: approved
                ? `User APPROVED the action (approval_id: ${approvalId}). You may proceed.${gewaehlt}`
                : `User DENIED the action (approval_id: ${approvalId}). Reason: "${antwort || "No reason given"}". Do NOT proceed.`,
            }],
          };
        } else {
          return {
            content: [{
              type: "text",
              text: `User did not respond within 10 minutes (approval_id: ${approvalId}). Do NOT proceed with the action. Inform the user and wait for explicit confirmation.`,
            }],
          };
        }
      }

      case "escalate_if_unsure": {
        // Die Schwelle liegt auf dem Server. Hier wird nur gemeldet und gewartet —
        // ein Agent, der selbst entscheidet, ob seine 40 % reichen, entscheidet das
        // genauso unsicher wie die Antwort selbst.
        const gate = await apiCall("/approvals/confidence", {
          method: "POST",
          body: JSON.stringify({
            confidence: args.confidence,
            question: args.question,
            context: args.context || "",
            options: args.options || undefined,
            task_id: args.task_id || undefined,
          }),
        });

        if (!gate.escalated) {
          return { content: [{ type: "text", text: gate.message }] };
        }

        const approvalId = gate.approval_id;
        const startTime = Date.now();
        const maxWait = 10 * 60 * 1000;
        let decision = null;
        while (Date.now() - startTime < maxWait) {
          await new Promise((r) => setTimeout(r, 4000));
          try {
            const poll = await apiCall(`/approvals/check/${approvalId}`);
            if (poll.status === "approved" || poll.status === "denied") {
              decision = poll;
              break;
            }
          } catch {
            // weiter warten — ein Aussetzer der Leitung ist keine Entscheidung
          }
        }

        if (!decision) {
          return {
            content: [{
              type: "text",
              text:
                `No decision within 10 minutes (approval_id: ${approvalId}). Do NOT ` +
                `proceed on your uncertain assumption. Stop and tell the user you are ` +
                `waiting; they can still decide under Approvals.`,
            }],
          };
        }
        const choice = decision.user_response || "";
        return {
          content: [{
            type: "text",
            text:
              decision.status === "approved"
                ? `The human decided${choice ? `: ${choice}` : ""}. Proceed accordingly.`
                : `The human declined${choice ? `: ${choice}` : ""}. Do NOT proceed.`,
          }],
        };
      }

      case "present_file": {
        const rawPath = String(args.path || "");
        if (!rawPath) throw new Error("path is required");
        const resolved = path.isAbsolute(rawPath)
          ? rawPath
          : path.resolve("/workspace", rawPath);
        const workspace = path.resolve("/workspace");
        if (!resolved.startsWith(workspace + path.sep) && resolved !== workspace) {
          throw new Error("Only files inside /workspace can be presented");
        }
        const stat = fs.statSync(resolved);
        if (!stat.isFile()) throw new Error(`Not a file: ${resolved}`);
        if (stat.size <= 0) throw new Error("File is empty");
        if (stat.size > 50 * 1024 * 1024) throw new Error("File exceeds the 50 MB chat attachment limit");
        const payload = {
          path: resolved,
          filename: path.basename(resolved),
          media_type: guessMediaType(resolved),
          size: stat.size,
          caption: args.caption || "",
        };
        return {
          content: [{
            type: "text",
            text: "__AI_EMPLOYEE_PRESENT_FILE__" + JSON.stringify(payload),
          }],
        };
      }

      default:
        throw new Error(`Unknown tool: ${name}`);
    }
  });

  return server;
}


await startServer("notification", buildServer);

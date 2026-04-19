#!/usr/bin/env node
/**
 * MCP Computer-Use Server — Desktop and browser control via the bridge app.
 *
 * Relays tool calls to the orchestrator REST API, which forwards them via
 * WebSocket to the local bridge app running on the user's machine.
 *
 * Environment:
 *   ORCHESTRATOR_URL          - Base URL of the orchestrator
 *   AGENT_TOKEN               - HMAC token for agent auth
 *   COMPUTER_USE_USER_ID      - User ID this agent belongs to (set by orchestrator)
 *   COMPUTER_USE_SESSION_ID   - Optional: pin to a specific session at startup
 *
 * Security: the orchestrator enforces user-scoped session access — agents can
 * only send commands to sessions owned by their user (agent.user_id).
 */

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";

const API = `${process.env.ORCHESTRATOR_URL || "http://orchestrator:8000"}/api/v1`;
const AGENT_TOKEN = process.env.AGENT_TOKEN || "";
const AGENT_USER_ID = process.env.COMPUTER_USE_USER_ID || "";
let pinnedSessionId = process.env.COMPUTER_USE_SESSION_ID || "";

async function apiCall(path, options = {}) {
  const url = `${API}${path}`;
  const res = await fetch(url, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${AGENT_TOKEN}`,
      ...(options.headers || {}),
    },
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`API ${res.status}: ${text}`);
  }
  return res.json();
}

async function resolveSession() {
  if (pinnedSessionId) return pinnedSessionId;
  // List sessions scoped to this agent's user (orchestrator enforces ownership)
  const data = await apiCall("/computer-use/sessions");
  const sessions = data.sessions || [];
  const connected = sessions.find((s) => s.status === "connected");
  if (!connected) {
    const waiting = sessions.filter((s) => s.status === "waiting_for_bridge").length;
    if (waiting > 0) {
      throw new Error(
        `Bridge not connected yet (${waiting} session(s) waiting). ` +
        "Open the AI-Employee Bridge app on your computer — it will connect automatically."
      );
    }
    throw new Error(
      "No bridge session found. " +
      "Go to the agent's Computer Use tab in the web UI, create a session, " +
      "then start the Bridge app on your computer."
    );
  }
  // Pin for this process lifetime to avoid switching mid-task
  pinnedSessionId = connected.session_id;
  return pinnedSessionId;
}

async function sendCommand(action, params = {}, timeout = 15) {
  const sessionId = await resolveSession();
  const result = await apiCall(`/computer-use/sessions/${sessionId}/command`, {
    method: "POST",
    body: JSON.stringify({ action, params, timeout }),
  });
  return result.result;
}

const server = new Server(
  { name: "mcp-computer-use", version: "1.0.0" },
  { capabilities: { tools: {} } }
);

server.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: [
    {
      name: "computer_screenshot",
      description:
        "Capture a screenshot of the user's desktop. Returns a base64-encoded PNG. " +
        "Use this to see the current state of the screen before clicking or typing.",
      inputSchema: {
        type: "object",
        properties: {
          scale: {
            type: "number",
            description: "Scale factor (default 1.0). Use 0.5 for Retina displays to reduce size.",
            default: 1.0,
          },
        },
      },
    },
    {
      name: "computer_ax_tree",
      description:
        "Get the macOS Accessibility (AX) element tree — much faster than screenshot loops. " +
        "Returns structured JSON with roles, titles, values, and bounding boxes. " +
        "Only available on macOS with accessibility permissions granted.",
      inputSchema: {
        type: "object",
        properties: {
          app: {
            type: "string",
            description: "App name to inspect (e.g. 'Safari', 'Finder'). Omit for full system tree.",
          },
          max_depth: {
            type: "integer",
            description: "Maximum tree depth (default 6).",
            default: 6,
          },
        },
      },
    },
    {
      name: "computer_click",
      description: "Click at screen coordinates (x, y). Optionally double-click or use right button.",
      inputSchema: {
        type: "object",
        required: ["x", "y"],
        properties: {
          x: { type: "integer", description: "X coordinate in pixels." },
          y: { type: "integer", description: "Y coordinate in pixels." },
          button: { type: "string", enum: ["left", "right", "middle"], default: "left" },
          double: { type: "boolean", description: "Double-click if true.", default: false },
        },
      },
    },
    {
      name: "computer_type",
      description: "Type text as keyboard input. Use for form fields, search boxes, etc.",
      inputSchema: {
        type: "object",
        required: ["text"],
        properties: {
          text: { type: "string", description: "Text to type." },
          interval: {
            type: "number",
            description: "Delay between keystrokes in seconds (default 0.02).",
            default: 0.02,
          },
        },
      },
    },
    {
      name: "computer_key",
      description:
        "Press keyboard key(s). For hotkeys pass multiple keys (e.g. ['ctrl', 'c']). " +
        "Key names: enter, tab, space, backspace, delete, escape, up, down, left, right, " +
        "f1-f12, ctrl, alt, shift, cmd/win.",
      inputSchema: {
        type: "object",
        required: ["keys"],
        properties: {
          keys: {
            type: "array",
            items: { type: "string" },
            description: "Key or key combination (e.g. ['enter'] or ['ctrl', 'c']).",
          },
        },
      },
    },
    {
      name: "computer_scroll",
      description: "Scroll at screen position (x, y).",
      inputSchema: {
        type: "object",
        required: ["x", "y"],
        properties: {
          x: { type: "integer" },
          y: { type: "integer" },
          amount: {
            type: "integer",
            description: "Scroll clicks. Positive = up/forward, negative = down/backward.",
            default: 3,
          },
        },
      },
    },
    {
      name: "computer_move",
      description: "Move mouse cursor to (x, y) without clicking.",
      inputSchema: {
        type: "object",
        required: ["x", "y"],
        properties: {
          x: { type: "integer" },
          y: { type: "integer" },
        },
      },
    },
    {
      name: "computer_drag",
      description: "Click and drag from (x1, y1) to (x2, y2).",
      inputSchema: {
        type: "object",
        required: ["x1", "y1", "x2", "y2"],
        properties: {
          x1: { type: "integer" },
          y1: { type: "integer" },
          x2: { type: "integer" },
          y2: { type: "integer" },
          duration: { type: "number", description: "Drag duration in seconds (default 0.3).", default: 0.3 },
        },
      },
    },
    {
      name: "computer_open_app",
      description: "Open an application by name (macOS only). E.g. 'Safari', 'Finder', 'Terminal'.",
      inputSchema: {
        type: "object",
        required: ["app"],
        properties: {
          app: { type: "string", description: "Application name (e.g. 'Safari', 'Calculator')." },
        },
      },
    },
    {
      name: "computer_get_clipboard",
      description: "Read the current clipboard contents as text.",
      inputSchema: { type: "object", properties: {} },
    },
    {
      name: "computer_set_clipboard",
      description: "Write text to the clipboard.",
      inputSchema: {
        type: "object",
        required: ["text"],
        properties: {
          text: { type: "string", description: "Text to copy to clipboard." },
        },
      },
    },
    {
      name: "computer_find_element",
      description:
        "Search the AX tree for a UI element by text and/or role. Returns the element's " +
        "bounding box and center coordinates — ready to pass to computer_click. " +
        "Faster than reading the full AX tree manually.",
      inputSchema: {
        type: "object",
        properties: {
          query: { type: "string", description: "Text to search for in title/label/value." },
          role: { type: "string", description: "AX role to match (e.g. 'AXButton', 'AXTextField')." },
          app: { type: "string", description: "App name to search in (omit for full desktop)." },
        },
      },
    },
    {
      name: "computer_wait_for_element",
      description:
        "Wait until a UI element matching the query appears on screen. " +
        "Polls the AX tree every 0.5s up to the timeout. Returns element coords when found.",
      inputSchema: {
        type: "object",
        properties: {
          query: { type: "string", description: "Text to wait for." },
          role: { type: "string", description: "AX role filter (optional)." },
          app: { type: "string", description: "App name to watch (optional)." },
          timeout: { type: "number", description: "Max wait in seconds (default 10, max 30).", default: 10 },
        },
      },
    },
    {
      name: "computer_list_sessions",
      description: "List all active computer-use bridge sessions. Shows which are connected.",
      inputSchema: { type: "object", properties: {} },
    },
    {
      name: "computer_use_session",
      description: "Pin this MCP server to a specific session ID for subsequent commands.",
      inputSchema: {
        type: "object",
        required: ["session_id"],
        properties: {
          session_id: { type: "string", description: "Session ID to use." },
        },
      },
    },
  ],
}));

server.setRequestHandler(CallToolRequestSchema, async (req) => {
  const { name, arguments: args } = req.params;

  try {
    let result;

    switch (name) {
      case "computer_screenshot":
        result = await sendCommand("screenshot", { scale: args?.scale ?? 1.0 }, 30);
        if (result.screenshot_b64) {
          return {
            content: [
              { type: "text", text: "Screenshot captured." },
              { type: "image", data: result.screenshot_b64, mimeType: "image/png" },
            ],
          };
        }
        break;

      case "computer_ax_tree":
        result = await sendCommand("ax_tree", {
          app: args?.app,
          max_depth: args?.max_depth ?? 6,
        }, 10);
        return {
          content: [{ type: "text", text: JSON.stringify(result.ax_tree ?? result, null, 2) }],
        };

      case "computer_click":
        result = await sendCommand("click", {
          x: args.x, y: args.y,
          button: args?.button ?? "left",
          double: args?.double ?? false,
        });
        return { content: [{ type: "text", text: result.ok ? "Clicked." : `Error: ${result.error}` }] };

      case "computer_type":
        result = await sendCommand("type", { text: args.text, interval: args?.interval ?? 0.02 });
        return { content: [{ type: "text", text: result.ok ? "Typed." : `Error: ${result.error}` }] };

      case "computer_key":
        result = await sendCommand("key", { keys: args.keys });
        return { content: [{ type: "text", text: result.ok ? "Key pressed." : `Error: ${result.error}` }] };

      case "computer_scroll":
        result = await sendCommand("scroll", { x: args.x, y: args.y, amount: args?.amount ?? 3 });
        return { content: [{ type: "text", text: result.ok ? "Scrolled." : `Error: ${result.error}` }] };

      case "computer_move":
        result = await sendCommand("move", { x: args.x, y: args.y });
        return { content: [{ type: "text", text: result.ok ? "Moved." : `Error: ${result.error}` }] };

      case "computer_drag":
        result = await sendCommand("drag", {
          x1: args.x1, y1: args.y1, x2: args.x2, y2: args.y2,
          duration: args?.duration ?? 0.3,
        });
        return { content: [{ type: "text", text: result.ok ? "Dragged." : `Error: ${result.error}` }] };

      case "computer_open_app":
        result = await sendCommand("open_app", { app: args.app });
        return { content: [{ type: "text", text: result.ok ? `Opened "${args.app}".` : `Error: ${result.error}` }] };

      case "computer_get_clipboard":
        result = await sendCommand("get_clipboard", {});
        return { content: [{ type: "text", text: result.text ?? `Error: ${result.error}` }] };

      case "computer_set_clipboard":
        result = await sendCommand("set_clipboard", { text: args.text });
        return { content: [{ type: "text", text: result.ok ? "Clipboard set." : `Error: ${result.error}` }] };

      case "computer_find_element":
        result = await sendCommand("find_element", {
          query: args?.query ?? "",
          role: args?.role ?? "",
          app: args?.app,
        }, 15);
        if (result.found) {
          return {
            content: [{
              type: "text",
              text: `Found: ${result.role} "${result.title || result.label}"\n` +
                    `Center: (${result.center.x}, ${result.center.y})\n` +
                    `Bbox: x=${result.bbox.x} y=${result.bbox.y} w=${result.bbox.w} h=${result.bbox.h}`,
            }],
          };
        }
        return { content: [{ type: "text", text: `Not found: "${args?.query}" (role: ${args?.role || "any"})` }] };

      case "computer_wait_for_element":
        result = await sendCommand("wait_for_element", {
          query: args?.query ?? "",
          role: args?.role ?? "",
          app: args?.app,
          timeout: args?.timeout ?? 10,
        }, (args?.timeout ?? 10) + 5);
        if (result.found) {
          return {
            content: [{
              type: "text",
              text: `Element appeared: ${result.role} "${result.title}"\nCenter: (${result.center.x}, ${result.center.y})`,
            }],
          };
        }
        return { content: [{ type: "text", text: `Timed out waiting for "${args?.query}"` }], isError: true };

      case "computer_list_sessions": {
        const data = await apiCall("/computer-use/sessions");
        const sessions = data.sessions || [];
        if (sessions.length === 0) {
          return {
            content: [{
              type: "text",
              text: "No sessions found. Start the bridge app on your machine to create one.",
            }],
          };
        }
        const lines = sessions.map(
          (s) => `• ${s.session_id} — ${s.status}${s.session_id === pinnedSessionId ? " (active)" : ""}`
        );
        return { content: [{ type: "text", text: lines.join("\n") }] };
      }

      case "computer_use_session":
        pinnedSessionId = args.session_id;
        return { content: [{ type: "text", text: `Session set to: ${args.session_id}` }] };

      default:
        return { content: [{ type: "text", text: `Unknown tool: ${name}` }], isError: true };
    }

    return { content: [{ type: "text", text: JSON.stringify(result) }] };
  } catch (err) {
    return {
      content: [{ type: "text", text: `Error: ${err.message}` }],
      isError: true,
    };
  }
});

const transport = new StdioServerTransport();
await server.connect(transport);

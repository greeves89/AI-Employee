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
const AGENT_ID = process.env.AGENT_ID || "";
const AGENT_USER_ID = process.env.COMPUTER_USE_USER_ID || "";
let pinnedSessionId = process.env.COMPUTER_USE_SESSION_ID || "";

async function apiCall(path, options = {}) {
  const url = `${API}${path}`;
  const res = await fetch(url, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${AGENT_TOKEN}`,
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
        "Use this to see the current state of the screen before clicking or typing. " +
        "The reply states the image size in points and, when the user has more than " +
        "one monitor, which displays exist — click coordinates must lie inside the " +
        "stated size, with (0,0) at the top left. Pass `display` to look at another " +
        "monitor.",
      inputSchema: {
        type: "object",
        properties: {
          scale: {
            type: "number",
            description: "Scale factor (default 1.0). Use 0.5 for Retina displays to reduce size.",
            default: 1.0,
          },
          display: {
            type: "number",
            description:
              "Which monitor to capture (1 = primary). Omit for the primary one. " +
              "The reply lists the available displays.",
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
      name: "computer_close_app",
      description: "Quit an application by name (macOS only). E.g. 'Safari', 'Finder', 'Terminal'.",
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
      name: "computer_list_windows",
      description:
        "List the windows currently open on the user's machine (app + window title). " +
        "Use this before computer_focus_window when you need to work in a specific app — " +
        "a screenshot shows pixels, the AX tree shows only one app at a time.",
      inputSchema: { type: "object", properties: {} },
    },
    {
      name: "computer_focus_window",
      description:
        "Bring an app (optionally a specific window) to the front. Typing and clicking " +
        "always go to the FOREGROUND window — without this, input lands in whatever app " +
        "was last used instead of the one you mean.",
      inputSchema: {
        type: "object",
        required: ["app"],
        properties: {
          app: { type: "string", description: "App name, e.g. 'Excel'." },
          title: { type: "string", description: "Optional: part of the window title." },
        },
      },
    },
    {
      name: "browser_navigate",
      description:
        "Open a URL in the agent's OWN browser profile on the user's machine. This is a " +
        "real, logged-in browser you control properly (DOM, forms, tabs) — unlike " +
        "computer_open_url, which just hands the URL to the default browser and leaves " +
        "you blind. Requires the 'browser' capability; the allowed domains are enforced " +
        "server-side. The user signs in once in this profile; the session persists.",
      inputSchema: {
        type: "object",
        required: ["url"],
        properties: { url: { type: "string", description: "http(s) URL." } },
      },
    },
    {
      name: "browser_snapshot",
      description:
        "Structured accessibility snapshot of the current page — the reliable way to see " +
        "what is on a page. Prefer this over a screenshot when you need to act on elements.",
      inputSchema: {
        type: "object",
        properties: {
          max_chars: { type: "number", description: "Truncate the snapshot (default 20000)." },
        },
      },
    },
    {
      name: "browser_click",
      description: "Click an element in the agent's browser, by CSS selector or by visible text.",
      inputSchema: {
        type: "object",
        properties: {
          selector: { type: "string", description: "CSS selector." },
          text: { type: "string", description: "Visible text (used when no selector given)." },
        },
      },
    },
    {
      name: "browser_fill",
      description: "Fill a form field in the agent's browser (clears it first).",
      inputSchema: {
        type: "object",
        required: ["selector", "value"],
        properties: {
          selector: { type: "string", description: "CSS selector of the field." },
          value: { type: "string", description: "Value to enter." },
        },
      },
    },
    {
      name: "browser_wait",
      description:
        "Wait for an element to become visible, or (without a selector) for the page to " +
        "go quiet. Use after a click that triggers loading, instead of guessing a sleep.",
      inputSchema: {
        type: "object",
        properties: {
          selector: { type: "string", description: "CSS selector to wait for (optional)." },
          timeout_ms: { type: "number", description: "Max wait in ms (default 15000)." },
        },
      },
    },
    {
      name: "browser_capture",
      description: "Screenshot of the current page in the agent's browser.",
      inputSchema: {
        type: "object",
        properties: {
          full_page: { type: "boolean", description: "Capture the whole page, not just the viewport." },
        },
      },
    },
    {
      name: "browser_tabs",
      description: "List the open tabs, or switch to one by index.",
      inputSchema: {
        type: "object",
        properties: {
          index: { type: "number", description: "Tab to switch to. Omit to just list." },
        },
      },
    },
    {
      name: "browser_close",
      description: "Close the agent's browser. The profile (and its logins) is kept.",
      inputSchema: { type: "object", properties: {} },
    },
    {
      name: "ego_run",
      description:
        "Run a JS automation script against ego lite, a Chromium-based browser on the " +
        "user's machine that works in their REAL, already-logged-in browser session " +
        "(unlike browser_navigate/browser_*, which use an isolated separate profile the " +
        "user has to sign into again). Use this when the task needs an account the user " +
        "is already logged into in their everyday browser (mail, internal tools, etc.) " +
        "and re-authenticating in a fresh profile would be unnecessary friction. The " +
        "script runs via the local `ego-browser nodejs` CLI with helpers like " +
        "useOrCreateTaskSpace, openOrReuseTab, snapshotText, click, fillInput, js, cdp — " +
        "write it exactly as you would inside an `ego-browser nodejs <<'EOF' ... EOF` " +
        "heredoc. Only the text passed to cliLog(...) calls is returned. Requires the " +
        "'ego_browser' capability (off by default) AND ego lite installed on the user's " +
        "machine. IMPORTANT: call this DIRECTLY for anything involving ego lite — it " +
        "launches ego lite itself if it isn't already running (a `useOrCreateTaskSpace(...)` " +
        "call is enough). Never call open_app/computer_open_app for ego lite first; that is " +
        "an unnecessary extra step and not how the user should have to ask for it.",
      inputSchema: {
        type: "object",
        required: ["script"],
        properties: {
          script: {
            type: "string",
            description:
              "The Node.js script body (same helpers/conventions as the ego-browser skill's heredoc).",
          },
          timeout: {
            type: "integer",
            description: "Seconds before the script is aborted (default 120).",
            default: 120,
          },
        },
      },
    },
    {
      name: "ego_navigate",
      description:
        "Open a URL in ego lite — the user's REAL, already-logged-in browser session " +
        "(counterpart to browser_navigate, which uses an isolated separate profile). " +
        "Requires the 'ego_browser' capability. Launches ego lite itself if needed.",
      inputSchema: {
        type: "object",
        required: ["url"],
        properties: { url: { type: "string", description: "http(s) URL." } },
      },
    },
    {
      name: "ego_snapshot",
      description:
        "Structured text snapshot of the current page in ego lite — the reliable way to " +
        "see what is on a page (counterpart to browser_snapshot).",
      inputSchema: {
        type: "object",
        properties: {
          max_chars: { type: "number", description: "Truncate the snapshot (default 20000)." },
        },
      },
    },
    {
      name: "ego_click",
      description:
        "Click an element in ego lite. selector accepts CSS, 'xpath=...', '@N'/'ref=N', or " +
        "'loc=...' (from ego_snapshot output); text does a best-effort text match if no " +
        "selector is given.",
      inputSchema: {
        type: "object",
        properties: {
          selector: { type: "string", description: "CSS/xpath=/@N/loc= selector." },
          text: { type: "string", description: "Visible text (used when no selector given)." },
        },
      },
    },
    {
      name: "ego_fill",
      description: "Fill a form field in ego lite (clears it first).",
      inputSchema: {
        type: "object",
        required: ["selector", "value"],
        properties: {
          selector: { type: "string", description: "CSS/xpath=/@N/loc= selector of the field." },
          value: { type: "string", description: "Value to enter." },
        },
      },
    },
    {
      name: "ego_wait",
      description: "Wait for an element to become visible in ego lite before acting on it.",
      inputSchema: {
        type: "object",
        required: ["selector"],
        properties: {
          selector: { type: "string", description: "CSS/xpath=/@N/loc= selector to wait for." },
          timeout_ms: { type: "number", description: "Max wait in ms (default 15000)." },
        },
      },
    },
    {
      name: "ego_capture",
      description: "Screenshot of the current page in ego lite.",
      inputSchema: { type: "object", properties: {} },
    },
    {
      name: "ego_tabs",
      description: "List the open tabs in ego lite's task space, or switch to one by index.",
      inputSchema: {
        type: "object",
        properties: {
          index: { type: "number", description: "Tab to switch to. Omit to just list." },
        },
      },
    },
    {
      name: "ego_close",
      description: "Close the current tab in ego lite's task space.",
      inputSchema: { type: "object", properties: {} },
    },
    {
      name: "computer_shell",
      description:
        "Run a shell command ON THE USER'S OWN MACHINE (their Mac/PC) via the bridge — " +
        "NOT in your container. This is how you READ and LIST the user's real local " +
        "files and folders: when they ask 'do you see my folder X', 'what's in folder Y', " +
        "'read that file on my machine', use this (e.g. `ls -la`, `find . -name ...`, " +
        "`cat file.txt`) instead of a screenshot or opening Finder. It runs inside the " +
        "folders the user allowed in the bridge (Berechtigungen > Ordner-Zugriff), so a " +
        "path they mentioned is very likely reachable — just try it. Only works if the " +
        "user enabled the 'shell' capability AND allowed at least one folder; the working " +
        "directory must be inside an allowed folder (default: the first allowed folder). " +
        "If it comes back 'gesperrt', tell the user to enable 'Shell-Befehle' and add the " +
        "folder in the bridge.",
      inputSchema: {
        type: "object",
        required: ["command"],
        properties: {
          command: { type: "string", description: "Shell command to run." },
          cwd: {
            type: "string",
            description: "Working directory (must be inside an allowed folder). Default: first allowed folder.",
          },
          timeout: {
            type: "integer",
            description: "Seconds before the command is aborted (default 120, max 300).",
            default: 120,
          },
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
      case "computer_screenshot": {
        const screenshotParams = { scale: args?.scale ?? 1.0 };
        // Nur mitschicken, wenn wirklich gewaehlt — eine aeltere Bridge kennt
        // den Parameter nicht.
        if (args?.display) screenshotParams.display = Number(args.display);
        result = await sendCommand("screenshot", screenshotParams, 30);
        if (result.screenshot_b64) {
          // Groesse und Bildschirme MITSAGEN. Die Bridge rechnet beides seit
          // jeher aus, und niemand hat es je weitergereicht — das Modell nannte
          // Klickkoordinaten, ohne zu wissen, wie gross das Bild ist, und wusste
          // nichts von einem zweiten Monitor (gemeldet am 21.08.2026).
          const groesse = result.image_size || {};
          const teile = ["Screenshot captured."];
          if (groesse.w && groesse.h) {
            teile.push(
              `Image is ${groesse.w}x${groesse.h} points — click coordinates must be ` +
              `inside that, (0,0) is top left.`,
            );
          }
          const monitore = result.displays || [];
          if (monitore.length > 1) {
            const liste = monitore
              .map((d) => `${d.number}${d.primary ? " (primary)" : ""}: ${d.width}x${d.height}`)
              .join(", ");
            teile.push(
              `The user has ${monitore.length} displays (${liste}); this is number ` +
              `${result.display}. Pass display=N to look at another one.`,
            );
          }
          return {
            content: [
              { type: "text", text: teile.join(" ") },
              { type: "image", data: result.screenshot_b64, mimeType: "image/png" },
            ],
          };
        }
        return {
          content: [{ type: "text", text: `Error: screenshot did not return image data: ${JSON.stringify(result)}` }],
          isError: true,
        };
      }

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

      case "computer_close_app":
        result = await sendCommand("close_app", { app: args.app });
        return { content: [{ type: "text", text: result.ok ? `Closed "${args.app}".` : `Error: ${result.error}` }] };

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

      case "computer_list_windows": {
        result = await sendCommand("list_windows", {}, 20);
        if (result.error) {
          return { content: [{ type: "text", text: `Error: ${result.error}` }], isError: true };
        }
        const wins = result.windows || [];
        if (wins.length === 0) {
          return { content: [{ type: "text", text: "No windows found." }] };
        }
        return {
          content: [{
            type: "text",
            text: wins.map((w) => `${w.app} — ${w.title}`).join("\n"),
          }],
        };
      }

      case "computer_focus_window":
        result = await sendCommand("focus_window", {
          app: args?.app ?? "",
          title: args?.title ?? "",
        }, 20);
        return {
          content: [{
            type: "text",
            text: result.ok
              ? `Focused: ${result.app}${result.title ? ` — ${result.title}` : ""}`
              : `Error: ${result.error}`,
          }],
          isError: !result.ok,
        };

      // ── Browser im eigenen Profil ──────────────────────────────────────────
      case "browser_navigate":
        result = await sendCommand("browser_navigate", { url: args?.url ?? "" }, 45);
        return {
          content: [{
            type: "text",
            text: result.ok ? `Opened: ${result.title || ""} (${result.url})` : `Error: ${result.error}`,
          }],
          isError: !result.ok,
        };

      case "browser_snapshot":
        result = await sendCommand("browser_snapshot", {
          max_chars: args?.max_chars ?? 20000,
        }, 45);
        if (!result.ok) {
          return { content: [{ type: "text", text: `Error: ${result.error}` }], isError: true };
        }
        return {
          content: [{
            type: "text",
            text: `${result.title || ""} (${result.url})\n\n${result.snapshot}` +
                  (result.truncated ? "\n\n[truncated — raise max_chars if you need more]" : ""),
          }],
        };

      case "browser_click":
        result = await sendCommand("browser_click", {
          selector: args?.selector ?? "",
          text: args?.text ?? "",
        }, 40);
        return {
          content: [{ type: "text", text: result.ok ? `Clicked: ${result.clicked}` : `Error: ${result.error}` }],
          isError: !result.ok,
        };

      case "browser_fill":
        result = await sendCommand("browser_fill", {
          selector: args?.selector ?? "",
          value: args?.value ?? "",
        }, 40);
        return {
          content: [{ type: "text", text: result.ok ? `Filled: ${result.filled}` : `Error: ${result.error}` }],
          isError: !result.ok,
        };

      case "browser_wait":
        result = await sendCommand("browser_wait", {
          selector: args?.selector ?? "",
          timeout_ms: args?.timeout_ms ?? 15000,
        }, ((args?.timeout_ms ?? 15000) / 1000) + 20);
        return {
          content: [{ type: "text", text: result.ok ? `Ready: ${result.url}` : `Error: ${result.error}` }],
          isError: !result.ok,
        };

      case "browser_capture":
        result = await sendCommand("browser_capture", {
          full_page: args?.full_page ?? false,
        }, 45);
        if (!result.ok) {
          return { content: [{ type: "text", text: `Error: ${result.error}` }], isError: true };
        }
        return {
          content: [
            { type: "text", text: `Page: ${result.url}` },
            { type: "image", data: result.screenshot_b64, mimeType: "image/png" },
          ],
        };

      case "browser_tabs": {
        const params = args?.index === undefined ? {} : { index: args.index };
        result = await sendCommand("browser_tabs", params, 30);
        if (!result.ok) {
          return { content: [{ type: "text", text: `Error: ${result.error}` }], isError: true };
        }
        if (result.tabs) {
          return {
            content: [{
              type: "text",
              text: result.tabs.map((t) => `[${t.index}] ${t.title} — ${t.url}`).join("\n") || "No tabs.",
            }],
          };
        }
        return { content: [{ type: "text", text: `Switched to tab ${result.active}: ${result.url}` }] };
      }

      case "browser_close":
        result = await sendCommand("browser_close", {}, 30);
        return {
          content: [{ type: "text", text: result.ok ? "Browser closed (profile kept)." : `Error: ${result.error}` }],
          isError: !result.ok,
        };

      case "ego_run": {
        const timeout = args?.timeout ?? 120;
        result = await sendCommand("ego_run", {
          script: args?.script ?? "",
          timeout,
        }, timeout + 30);
        return {
          content: [{ type: "text", text: result.ok ? (result.output || "(no output)") : `Error: ${result.error}` }],
          isError: !result.ok,
        };
      }

      case "ego_navigate":
        result = await sendCommand("ego_navigate", { url: args?.url ?? "" }, 45);
        return {
          content: [{
            type: "text",
            text: result.ok ? `Opened: ${result.title || ""} (${result.url})` : `Error: ${result.error}`,
          }],
          isError: !result.ok,
        };

      case "ego_snapshot":
        result = await sendCommand("ego_snapshot", {
          max_chars: args?.max_chars ?? 20000,
        }, 45);
        if (!result.ok) {
          return { content: [{ type: "text", text: `Error: ${result.error}` }], isError: true };
        }
        return {
          content: [{
            type: "text",
            text: `${result.title || ""} (${result.url})\n\n${result.snapshot}` +
                  (result.truncated ? "\n\n[truncated — raise max_chars if you need more]" : ""),
          }],
        };

      case "ego_click":
        result = await sendCommand("ego_click", {
          selector: args?.selector ?? "",
          text: args?.text ?? "",
        }, 40);
        return {
          content: [{ type: "text", text: result.ok ? `Clicked: ${result.clicked}` : `Error: ${result.error}` }],
          isError: !result.ok,
        };

      case "ego_fill":
        result = await sendCommand("ego_fill", {
          selector: args?.selector ?? "",
          value: args?.value ?? "",
        }, 40);
        return {
          content: [{ type: "text", text: result.ok ? `Filled: ${result.filled}` : `Error: ${result.error}` }],
          isError: !result.ok,
        };

      case "ego_wait":
        result = await sendCommand("ego_wait", {
          selector: args?.selector ?? "",
          timeout_ms: args?.timeout_ms ?? 15000,
        }, ((args?.timeout_ms ?? 15000) / 1000) + 20);
        return {
          content: [{ type: "text", text: result.ok ? `Ready: ${result.url}` : `Error: ${result.error}` }],
          isError: !result.ok,
        };

      case "ego_capture":
        result = await sendCommand("ego_capture", {}, 45);
        if (!result.ok) {
          return { content: [{ type: "text", text: `Error: ${result.error}` }], isError: true };
        }
        return {
          content: [
            { type: "text", text: `Page: ${result.url}` },
            { type: "image", data: result.screenshot_b64, mimeType: "image/png" },
          ],
        };

      case "ego_tabs": {
        const params = args?.index === undefined ? {} : { index: args.index };
        result = await sendCommand("ego_tabs", params, 30);
        if (!result.ok) {
          return { content: [{ type: "text", text: `Error: ${result.error}` }], isError: true };
        }
        if (result.tabs) {
          return {
            content: [{
              type: "text",
              text: result.tabs.map((t) => `[${t.index}] ${t.title} — ${t.url}`).join("\n") || "No tabs.",
            }],
          };
        }
        return { content: [{ type: "text", text: `Switched to tab ${result.active}: ${result.url}` }] };
      }

      case "ego_close":
        result = await sendCommand("ego_close", {}, 30);
        return {
          content: [{ type: "text", text: result.ok ? "Tab closed." : `Error: ${result.error}` }],
          isError: !result.ok,
        };

      case "computer_shell": {
        result = await sendCommand("shell_run", {
          command: args.command,
          cwd: args?.cwd,
          timeout: args?.timeout ?? 120,
        }, (args?.timeout ?? 120) + 30);
        if (result.error && result.ok === undefined) {
          return { content: [{ type: "text", text: `Error: ${result.error}` }], isError: true };
        }
        const parts = [];
        parts.push(result.ok ? `Exit 0 (cwd: ${result.cwd})` : `Exit ${result.returncode ?? "?"} (cwd: ${result.cwd ?? "?"})`);
        if (result.stdout) parts.push(`stdout:\n${result.stdout}`);
        if (result.stderr) parts.push(`stderr:\n${result.stderr}`);
        if (result.error) parts.push(`error: ${result.error}`);
        return { content: [{ type: "text", text: parts.join("\n\n") }], isError: !result.ok };
      }

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

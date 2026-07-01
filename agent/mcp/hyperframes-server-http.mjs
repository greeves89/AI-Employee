#!/usr/bin/env node
/**
 * HTTP MCP wrapper around the hyperframes CLI.
 *
 * Runs natively on macOS so Chromium can use Apple GPU (Metal) instead of
 * software rendering inside the Linux agent container. Containers reach this
 * via host.docker.internal:7849 and register it as a Custom MCP Server.
 *
 * Env:
 *   HYPERFRAMES_MCP_PORT  - listen port (default 7849)
 *   HYPERFRAMES_OUTPUT_DIR - where rendered MP4s land (default ~/Movies/AI-Employee-Renders)
 */

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import http from "node:http";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { randomUUID } from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import os from "node:os";

const execFileAsync = promisify(execFile);

const PORT = Number(process.env.HYPERFRAMES_MCP_PORT || 7849);
const OUTPUT_DIR =
  process.env.HYPERFRAMES_OUTPUT_DIR ||
  path.join(os.homedir(), "Movies", "AI-Employee-Renders");
const RENDER_TIMEOUT_MS = Number(process.env.HYPERFRAMES_RENDER_TIMEOUT_MS || 600_000);

const SAFE_NAME_RE = /^[a-zA-Z0-9_-]{1,64}$/;

function safeName(name) {
  if (!name) return null;
  const base = path.basename(name).replace(/\.mp4$/i, "");
  return SAFE_NAME_RE.test(base) ? base : null;
}

async function ensureOutputDir() {
  await fs.mkdir(OUTPUT_DIR, { recursive: true });
}

async function runHyperframes(cwd, args) {
  return execFileAsync("hyperframes", args, {
    cwd,
    timeout: RENDER_TIMEOUT_MS,
    env: { ...process.env, HOME: os.homedir() },
    maxBuffer: 16 * 1024 * 1024,
  });
}

const HYPERFRAMES_DOCS = `# Hyperframes — Video Rendering Guide for Agents

## ALWAYS CALL get_docs FIRST before writing any composition HTML.

## Key Rules
1. Every timed element needs \`data-start\`, \`data-duration\`, and \`data-track-index\`
2. Visible timed elements MUST have \`class="clip"\`
3. GSAP timelines must be paused and registered on \`window.__timelines\`:
   \`\`\`js
   window.__timelines = window.__timelines || {};
   window.__timelines["composition-id"] = gsap.timeline({ paused: true });
   \`\`\`
4. No non-deterministic code: no Date.now(), Math.random(), network fetches
5. Videos use \`muted\` + separate \`<audio>\` for audio track
6. Root element needs \`data-composition-id\`, \`data-width\`, \`data-height\`, \`data-duration\`

## Minimal Working Example (5-second video)
\`\`\`html
<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=1920, height=1080" />
  <script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    html, body { width: 1920px; height: 1080px; overflow: hidden; background: #111; }
  </style>
</head>
<body>
  <div id="root" data-composition-id="main" data-start="0" data-duration="5"
       data-width="1920" data-height="1080">
    <h1 id="title" class="clip" data-start="0" data-duration="5" data-track-index="1"
        style="color:#fff; font-size:80px; font-family:sans-serif; position:absolute;
               top:50%; left:50%; transform:translate(-50%,-50%); opacity:0">
      Hello World
    </h1>
  </div>
  <script>
    window.__timelines = window.__timelines || {};
    const tl = gsap.timeline({ paused: true });
    tl.to("#title", { opacity: 1, y: -20, duration: 1 }, 0);
    tl.to("#title", { opacity: 0, duration: 0.5 }, 4);
    window.__timelines["main"] = tl;
  </script>
</body>
</html>
\`\`\`

## Full Docs
https://hyperframes.heygen.com/introduction
Machine-readable index: https://hyperframes.heygen.com/llms.txt
`;

function buildMcpServer() {
  const server = new Server(
    { name: "hyperframes", version: "1.0.0" },
    { capabilities: { tools: {} } }
  );

  server.setRequestHandler(ListToolsRequestSchema, async () => ({
    tools: [
      {
        name: "get_docs",
        description:
          "CALL THIS FIRST before creating any video. Returns the Hyperframes guide " +
          "with HTML composition rules, required data attributes, and GSAP setup.",
        inputSchema: { type: "object", properties: {}, additionalProperties: false },
      },
      {
        name: "render_video",
        description:
          "Render an HTML composition to an MP4 using Hyperframes natively on macOS " +
          "with Apple GPU. Call get_docs first to learn the required HTML structure.",
        inputSchema: {
          type: "object",
          properties: {
            html_content: {
              type: "string",
              description: "Full HTML content for the video composition.",
            },
            filename: {
              type: "string",
              description:
                "Output filename without extension (alphanumeric, hyphens, " +
                "underscores; max 64 chars). Defaults to timestamp.",
            },
          },
          required: ["html_content"],
          additionalProperties: false,
        },
      },
      {
        name: "list_videos",
        description: "List all rendered MP4 videos.",
        inputSchema: { type: "object", properties: {}, additionalProperties: false },
      },
    ],
  }));

  server.setRequestHandler(CallToolRequestSchema, async (req) => {
    const { name, arguments: args = {} } = req.params;

    if (name === "get_docs") {
      return { content: [{ type: "text", text: HYPERFRAMES_DOCS }] };
    }

    if (name === "render_video") {
      const { html_content, filename } = args;
      if (!html_content || typeof html_content !== "string") {
        return {
          content: [{ type: "text", text: "Error: html_content is required." }],
          isError: true,
        };
      }
      const rawName = filename || `video-${Date.now()}`;
      const safeFn = safeName(rawName);
      if (!safeFn) {
        return {
          content: [
            {
              type: "text",
              text: `Error: Invalid filename "${filename}". Use letters, numbers, hyphens, underscores (max 64).`,
            },
          ],
          isError: true,
        };
      }

      await ensureOutputDir();
      const outPath = path.join(OUTPUT_DIR, `${safeFn}.mp4`);
      const tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), "hf-"));

      try {
        await runHyperframes(tmpDir, ["init", "."]).catch(() => {});
        await fs.writeFile(path.join(tmpDir, "index.html"), html_content, "utf8");

        try {
          await runHyperframes(tmpDir, ["render", "--output", outPath, "--workers", "1"]);
        } catch (renderErr) {
          try {
            await fs.access(outPath);
          } catch {
            const msg = (renderErr.stderr || renderErr.message || "")
              .replace(/\[BrowserManager\].*?falling back to screenshot mode\.\n?/g, "")
              .trim();
            throw new Error(msg || renderErr.message);
          }
        }

        const stat = await fs.stat(outPath);
        return {
          content: [
            {
              type: "text",
              text: `Video rendered (native macOS, Apple GPU).\nPath: ${outPath}\nSize: ${(stat.size / 1024).toFixed(1)} KB`,
            },
          ],
        };
      } catch (err) {
        return {
          content: [{ type: "text", text: `Render failed: ${err.message}` }],
          isError: true,
        };
      } finally {
        await fs.rm(tmpDir, { recursive: true, force: true });
      }
    }

    if (name === "list_videos") {
      try {
        await ensureOutputDir();
        const files = await fs.readdir(OUTPUT_DIR);
        const mp4s = files.filter((f) => f.endsWith(".mp4"));
        if (mp4s.length === 0) {
          return { content: [{ type: "text", text: "No videos found." }] };
        }
        return {
          content: [
            {
              type: "text",
              text:
                `Videos in ${OUTPUT_DIR}:\n` +
                mp4s.map((f) => `- ${path.join(OUTPUT_DIR, f)}`).join("\n"),
            },
          ],
        };
      } catch (err) {
        return {
          content: [{ type: "text", text: `Error: ${err.message}` }],
          isError: true,
        };
      }
    }

    return {
      content: [{ type: "text", text: `Unknown tool: ${name}` }],
      isError: true,
    };
  });

  return server;
}

const sessions = new Map();

async function readJsonBody(req) {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  if (chunks.length === 0) return undefined;
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

const httpServer = http.createServer(async (req, res) => {
  if (req.url === "/" || req.url === "") {
    res.writeHead(200, { "content-type": "application/json" });
    res.end(
      JSON.stringify({
        service: "hyperframes-mcp",
        protocol: "MCP over HTTP (Streamable)",
        output_dir: OUTPUT_DIR,
      })
    );
    return;
  }
  if (!req.url || !req.url.startsWith("/mcp")) {
    res.writeHead(404).end("Not Found");
    return;
  }

  const sessionId =
    req.headers["mcp-session-id"] && String(req.headers["mcp-session-id"]);
  let transport = sessionId ? sessions.get(sessionId) : undefined;

  if (!transport) {
    transport = new StreamableHTTPServerTransport({
      sessionIdGenerator: () => randomUUID(),
      onsessioninitialized: (sid) => {
        sessions.set(sid, transport);
      },
    });
    transport.onclose = () => {
      if (transport.sessionId) sessions.delete(transport.sessionId);
    };
    const server = buildMcpServer();
    await server.connect(transport);
  }

  let body;
  if (req.method === "POST") {
    try {
      body = await readJsonBody(req);
    } catch (e) {
      res.writeHead(400).end(`Invalid JSON: ${e.message}`);
      return;
    }
  }

  try {
    await transport.handleRequest(req, res, body);
  } catch (e) {
    if (!res.headersSent) {
      res.writeHead(500).end(`Transport error: ${e.message}`);
    }
  }
});

// Loopback only. Docker Desktop on macOS routes `host.docker.internal` through
// VPNkit to the host's 127.0.0.1, so containers still reach us. LAN does not.
const BIND_HOST = process.env.HYPERFRAMES_MCP_BIND || "127.0.0.1";
httpServer.listen(PORT, BIND_HOST, () => {
  console.log(
    `[hyperframes-mcp] listening on http://${BIND_HOST}:${PORT}/mcp ` +
      `(output: ${OUTPUT_DIR})`
  );
});

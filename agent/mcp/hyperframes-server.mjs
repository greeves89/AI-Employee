#!/usr/bin/env node
/**
 * MCP Hyperframes Server — AI video rendering for agents.
 *
 * Wraps the @hyperframes/cli tool with strict user isolation.
 * Each agent's videos are scoped to /workspace/videos/{user_id}/.
 * Path traversal is blocked at the input validation layer.
 *
 * Environment:
 *   COMPUTER_USE_USER_ID - User ID for output isolation (required)
 */

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import fs from "node:fs/promises";
import path from "node:path";
import os from "node:os";

const execFileAsync = promisify(execFile);

const USER_ID = process.env.COMPUTER_USE_USER_ID || "anonymous";
const WORKSPACE = process.env.WORKSPACE_DIR || "/workspace";
// Each user's videos land in their own directory inside the container workspace.
// Container-level isolation (one container per user) is the primary boundary;
// this directory scoping is defense-in-depth.
const USER_VIDEO_DIR = path.join(WORKSPACE, "videos", USER_ID);

const SAFE_NAME_RE = /^[a-zA-Z0-9_-]{1,64}$/;

function safeName(name) {
  if (!name) return null;
  const base = path.basename(name).replace(/\.mp4$/i, "");
  return SAFE_NAME_RE.test(base) ? base : null;
}

async function ensureVideoDir() {
  await fs.mkdir(USER_VIDEO_DIR, { recursive: true });
}

async function runHyperframes(projectDir, args) {
  return execFileAsync("hyperframes", args, {
    cwd: projectDir,
    timeout: 180_000,
    env: { ...process.env, HOME: os.homedir() },
  });
}

const server = new Server(
  { name: "hyperframes", version: "1.0.0" },
  { capabilities: { tools: {} } }
);

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

## Multi-Scene Videos
Use multiple elements with different data-start values on the same timeline:
- Scene 1: data-start="0" data-duration="5"
- Scene 2: data-start="5" data-duration="7"
- Scene 3: data-start="12" data-duration="6"
Total duration = 18s → set on root: data-duration="18"

## Full Docs
https://hyperframes.heygen.com/introduction
Machine-readable index: https://hyperframes.heygen.com/llms.txt
`;

server.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: [
    {
      name: "get_docs",
      description:
        "CALL THIS FIRST before creating any video. Returns the complete Hyperframes guide " +
        "with HTML composition rules, required data attributes, GSAP timeline setup, and examples.",
      inputSchema: { type: "object", properties: {} },
    },
    {
      name: "render_video",
      description:
        "Render an HTML composition to an MP4 video using Hyperframes. " +
        "Call get_docs first to learn the required HTML structure. " +
        "Returns the absolute path to the rendered MP4 file.",
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
              "Output filename without extension (alphanumeric, hyphens, underscores, max 64 chars). " +
              "Defaults to a timestamp-based name.",
          },
        },
        required: ["html_content"],
      },
    },
    {
      name: "list_videos",
      description: "List all rendered MP4 videos available for this user.",
      inputSchema: { type: "object", properties: {} },
    },
  ],
}));

server.setRequestHandler(CallToolRequestSchema, async (req) => {
  const { name, arguments: args } = req.params;

  if (name === "get_docs") {
    return { content: [{ type: "text", text: HYPERFRAMES_DOCS }] };
  }

  if (name === "render_video") {
    const { html_content, filename } = args;

    const rawName = filename || `video-${Date.now()}`;
    const safeFn = safeName(rawName);
    if (!safeFn) {
      return {
        content: [
          {
            type: "text",
            text: `Error: Invalid filename "${filename}". Use only letters, numbers, hyphens, and underscores (max 64 chars).`,
          },
        ],
        isError: true,
      };
    }

    const outPath = path.join(USER_VIDEO_DIR, `${safeFn}.mp4`);
    const tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), "hf-"));

    try {
      await ensureVideoDir();

      // Init hyperframes project in-place (dot = current dir, no subdirectory created)
      await runHyperframes(tmpDir, ["init", "."]).catch(() => {
        // Continue even if init fails — some versions skip this step
      });

      // Write the HTML composition (overwrite scaffold if present)
      const htmlTarget = path.join(tmpDir, "index.html");
      await fs.writeFile(htmlTarget, html_content, "utf8");

      // Render to MP4 — limit to 1 worker to stay within container memory limits
      await runHyperframes(tmpDir, ["render", "--output", outPath, "--workers", "1"]);

      return {
        content: [
          {
            type: "text",
            text: `Video rendered successfully.\nPath: ${outPath}`,
          },
        ],
      };
    } catch (err) {
      return {
        content: [
          {
            type: "text",
            text: `Render failed: ${err.stderr || err.message}`,
          },
        ],
        isError: true,
      };
    } finally {
      await fs.rm(tmpDir, { recursive: true, force: true });
    }
  }

  if (name === "list_videos") {
    try {
      await ensureVideoDir();
      const files = await fs.readdir(USER_VIDEO_DIR);
      const mp4s = files.filter((f) => f.endsWith(".mp4"));
      if (mp4s.length === 0) {
        return { content: [{ type: "text", text: "No videos found." }] };
      }
      const list = mp4s
        .map((f) => `- ${path.join(USER_VIDEO_DIR, f)}`)
        .join("\n");
      return { content: [{ type: "text", text: `Videos:\n${list}` }] };
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

const transport = new StdioServerTransport();
await server.connect(transport);

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

server.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: [
    {
      name: "render_video",
      description:
        "Render an HTML composition to an MP4 video using Hyperframes. " +
        "Write your video scene as a single HTML file with data-duration attributes. " +
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

      // Render to MP4
      await runHyperframes(tmpDir, ["render", "--output", outPath]);

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

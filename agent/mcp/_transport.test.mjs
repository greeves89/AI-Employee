import { test } from "node:test";
import assert from "node:assert/strict";
import net from "node:net";
import { fileURLToPath } from "node:url";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

test("an empty port override starts a real stdio server despite an occupied parent port", { timeout: 10000 }, async () => {
  const occupied = net.createServer();
  await new Promise((resolve) => occupied.listen(0, "127.0.0.1", resolve));
  const parentEnv = { ...process.env, MCP_HTTP_PORT: String(occupied.address().port) };
  const transport = new StdioClientTransport({
    command: process.execPath,
    args: [fileURLToPath(new URL("./memory-server.mjs", import.meta.url))],
    env: { ...parentEnv, MCP_HTTP_PORT: "", MCP_COMBINED: "" },
    stderr: "pipe",
  });
  const client = new Client({ name: "stdio-fallback-test", version: "1.0.0" });
  try {
    await client.connect(transport);
    const result = await client.listTools();
    assert.ok(result.tools.some((tool) => tool.name === "memory_search"));
  } finally {
    await client.close();
    await new Promise((resolve, reject) => occupied.close((error) => error ? reject(error) : resolve()));
  }
});

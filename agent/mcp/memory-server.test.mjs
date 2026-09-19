import { test } from "node:test";
import assert from "node:assert/strict";
import http from "node:http";

// Both env vars must be set BEFORE importing memory-server.mjs / _tool_gate.mjs
// — both read process.env at module-load time.
const hookPort = 38200 + Math.floor(Math.random() * 1000);
const apiPort = 38300 + Math.floor(Math.random() * 1000);
process.env.AGENT_HOOK_URL = `http://127.0.0.1:${hookPort}/hooks/pretooluse`;
process.env.ORCHESTRATOR_URL = `http://127.0.0.1:${apiPort}`;
process.env.AGENT_ID = "test-agent";
process.env.AGENT_TOKEN = "test-token";
// _transport.mjs::startServer() no-ops under MCP_COMBINED=1 (the same guard
// _all.mjs relies on to import server files without each one racing to bind
// stdio/a port) — needed here too, otherwise importing this module would try
// to connect a real StdioServerTransport as an unwanted side effect.
process.env.MCP_COMBINED = "1";

const { handleCallTool } = await import("./memory-server.mjs");

function startJsonStub(port, handler) {
  return new Promise((resolve) => {
    const server = http.createServer((req, res) => {
      let body = "";
      req.on("data", (c) => (body += c));
      req.on("end", () => handler(body ? JSON.parse(body) : {}, req, res));
    });
    server.listen(port, "127.0.0.1", () => resolve(server));
  });
}

function stop(server) {
  return new Promise((resolve) => server.close(resolve));
}

test("a denied memory_save is blocked BEFORE the orchestrator API is ever called", async () => {
  const hookServer = await startJsonStub(hookPort, (_body, _req, res) => {
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify({
      hookSpecificOutput: {
        hookEventName: "PreToolUse",
        permissionDecision: "deny",
        permissionDecisionReason: "Werkzeug 'mcp__memory__memory_save' braucht Kategorie 'knowledge_write'.",
      },
    }));
  });

  let apiWasCalled = false;
  const apiServer = await startJsonStub(apiPort, (_body, _req, res) => {
    apiWasCalled = true;
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify({ id: 1, category: "fact", key: "x", importance: 3 }));
  });

  try {
    const result = await handleCallTool({
      params: { name: "memory_save", arguments: { category: "fact", key: "x", content: "y" } },
    });
    assert.equal(result.isError, true);
    assert.equal(
      result.content[0].text,
      "Werkzeug 'mcp__memory__memory_save' braucht Kategorie 'knowledge_write'."
    );
    assert.equal(apiWasCalled, false, "the real memory/save API call must not have fired");
  } finally {
    await stop(hookServer);
    await stop(apiServer);
  }
});

test("an allowed memory_save proceeds to the real orchestrator call as before", async () => {
  const hookServer = await startJsonStub(hookPort, (_body, _req, res) => {
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify({
      hookSpecificOutput: { hookEventName: "PreToolUse", permissionDecision: "allow" },
    }));
  });

  let apiWasCalled = false;
  const apiServer = await startJsonStub(apiPort, (_body, _req, res) => {
    apiWasCalled = true;
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify({ id: 1, category: "fact", key: "x", importance: 3 }));
  });

  try {
    const result = await handleCallTool({
      params: { name: "memory_save", arguments: { category: "fact", key: "x", content: "y" } },
    });
    assert.equal(result.isError, undefined);
    assert.equal(apiWasCalled, true, "the real memory/save API call must have fired");
  } finally {
    await stop(hookServer);
    await stop(apiServer);
  }
});

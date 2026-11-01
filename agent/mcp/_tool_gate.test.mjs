import { test } from "node:test";
import assert from "node:assert/strict";

// _tool_gate.mjs reads AGENT_HOOK_URL at module-load time, so it must be set
// BEFORE the dynamic import below — a static top-level import would freeze
// the default localhost:8080 URL before this test file gets a chance to
// point it at the stub server.
const port = 38080 + Math.floor(Math.random() * 1000);
process.env.AGENT_HOOK_URL = `http://127.0.0.1:${port}/hooks/pretooluse`;
const { checkToolPermission } = await import("./_tool_gate.mjs");

import http from "node:http";

/** Starts a stub PreToolUse-hook HTTP server for one test. */
function startStub(handler) {
  return new Promise((resolve) => {
    const server = http.createServer((req, res) => {
      let body = "";
      req.on("data", (c) => (body += c));
      req.on("end", () => handler(JSON.parse(body || "{}"), req, res));
    });
    server.listen(port, "127.0.0.1", () => resolve(server));
  });
}

function stop(server) {
  return new Promise((resolve) => server.close(resolve));
}

test("allow decision passes through as {allowed: true}", async () => {
  const server = await startStub((_body, _req, res) => {
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify({
      hookSpecificOutput: { hookEventName: "PreToolUse", permissionDecision: "allow" },
    }));
  });
  try {
    const result = await checkToolPermission("memory", "memory_save", { key: "x" });
    assert.deepEqual(result, { allowed: true });
  } finally {
    await stop(server);
  }
});

test("deny decision surfaces the exact permissionDecisionReason", async () => {
  const server = await startStub((_body, _req, res) => {
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify({
      hookSpecificOutput: {
        hookEventName: "PreToolUse",
        permissionDecision: "deny",
        permissionDecisionReason: "Werkzeug 'mcp__memory__memory_save' braucht Kategorie 'knowledge_write'.",
      },
    }));
  });
  try {
    const result = await checkToolPermission("memory", "memory_save", {});
    assert.deepEqual(result, {
      allowed: false,
      reason: "Werkzeug 'mcp__memory__memory_save' braucht Kategorie 'knowledge_write'.",
    });
  } finally {
    await stop(server);
  }
});

test("request body sends the exact mcp__<server>__<tool> shape", async () => {
  let seen;
  const server = await startStub((body, _req, res) => {
    seen = body;
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify({
      hookSpecificOutput: { hookEventName: "PreToolUse", permissionDecision: "allow" },
    }));
  });
  try {
    await checkToolPermission("brain", "brain_contribute", { content: "note" });
    assert.deepEqual(seen, {
      tool_name: "mcp__brain__brain_contribute",
      tool_input: { content: "note" },
    });
  } finally {
    await stop(server);
  }
});

test("non-2xx HTTP response fails closed", async () => {
  const server = await startStub((_body, _req, res) => {
    res.writeHead(500, { "content-type": "application/json" });
    res.end(JSON.stringify({ error: "boom" }));
  });
  try {
    const result = await checkToolPermission("memory", "memory_save", {});
    assert.deepEqual(result, { allowed: false, reason: "Werkzeugpruefung nicht erreichbar — aus Sicherheitsgruenden abgelehnt." });
  } finally {
    await stop(server);
  }
});

test("a refused connection (hook unreachable) fails closed", async () => {
  // No stub started on this port — connection refused.
  const result = await checkToolPermission("memory", "memory_save", {});
  assert.deepEqual(result, { allowed: false, reason: "Werkzeugpruefung nicht erreichbar — aus Sicherheitsgruenden abgelehnt." });
});

test("missing hookSpecificOutput in a malformed-but-200 response fails closed", async () => {
  const server = await startStub((_body, _req, res) => {
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify({ unexpected: "shape" }));
  });
  try {
    const result = await checkToolPermission("memory", "memory_save", {});
    assert.deepEqual(result, { allowed: false, reason: "Werkzeugpruefung nicht erreichbar — aus Sicherheitsgruenden abgelehnt." });
  } finally {
    await stop(server);
  }
});

// ── gateAktivieren (#781 PR 2) ──────────────────────────────────────────────
const { gateAktivieren } = await import("./_tool_gate.mjs");
const { CallToolRequestSchema, ListToolsRequestSchema } = await import(
  "@modelcontextprotocol/sdk/types.js"
);

/** Minimaler Server-Ersatz: merkt sich, welcher Handler fuer welches Schema registriert wurde. */
function fakeServer() {
  const handlers = new Map();
  return {
    handlers,
    setRequestHandler(schema, handler) {
      handlers.set(schema, handler);
    },
  };
}

test("gateAktivieren: Ablehnung verhindert den Werkzeug-Handler", async () => {
  const server = await startStub((_body, _req, res) => {
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify({
      hookSpecificOutput: {
        hookEventName: "PreToolUse",
        permissionDecision: "deny",
        permissionDecisionReason: "braucht Kategorie custom",
      },
    }));
  });
  try {
    const mcp = gateAktivieren(fakeServer(), "email");
    let aufgerufen = false;
    mcp.setRequestHandler(CallToolRequestSchema, async () => {
      aufgerufen = true;
      return { content: [{ type: "text", text: "gesendet" }] };
    });
    const antwort = await mcp.handlers.get(CallToolRequestSchema)({
      params: { name: "email_send", arguments: { to: "a@example.invalid" } },
    });
    assert.equal(aufgerufen, false);
    assert.equal(antwort.isError, true);
    assert.match(antwort.content[0].text, /Kategorie custom/);
  } finally {
    await stop(server);
  }
});

test("gateAktivieren: Erlaubnis ruft den Handler, Werkzeugliste bleibt ungegated", async () => {
  let hookAufrufe = 0;
  const server = await startStub((body, _req, res) => {
    hookAufrufe += 1;
    assert.equal(body.tool_name, "mcp__email__email_read");
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify({ hookSpecificOutput: { permissionDecision: "allow" } }));
  });
  try {
    const mcp = gateAktivieren(fakeServer(), "email");
    const liste = async () => ({ tools: [] });
    mcp.setRequestHandler(ListToolsRequestSchema, liste);
    mcp.setRequestHandler(CallToolRequestSchema, async () => ({ content: [{ type: "text", text: "ok" }] }));
    assert.equal(mcp.handlers.get(ListToolsRequestSchema), liste, "tools/list wird nicht umschlossen");
    const antwort = await mcp.handlers.get(CallToolRequestSchema)({
      params: { name: "email_read", arguments: {} },
    });
    assert.equal(antwort.content[0].text, "ok");
    assert.equal(hookAufrufe, 1);
  } finally {
    await stop(server);
  }
});

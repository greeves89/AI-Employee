import { test } from "node:test";
import assert from "node:assert/strict";
import http from "node:http";

// Vor dem Import setzen: orchestrator-server.mjs liest die Umgebung beim Laden.
const apiPort = 38500 + Math.floor(Math.random() * 1000);
process.env.ORCHESTRATOR_URL = `http://127.0.0.1:${apiPort}`;
process.env.AGENT_ID = "agent-1";
process.env.AGENT_TOKEN = "test-token";
// Kein eigener stdio-Transport beim Import (siehe _transport.mjs).
process.env.MCP_COMBINED = "1";
delete process.env.AI_CHAT_SESSION;

const { chatSearch, CHAT_SEARCH_NO_SESSION } = await import("./orchestrator-server.mjs");

function startStub(handler) {
  return new Promise((resolve) => {
    const server = http.createServer(handler);
    server.listen(apiPort, "127.0.0.1", () => resolve(server));
  });
}

const stop = (server) => new Promise((resolve) => server.close(resolve));

test("ohne laufendes Gespraech wird nichts durchsucht", async () => {
  let aufgerufen = false;
  const server = await startStub((_req, res) => {
    aufgerufen = true;
    res.end("{}");
  });
  try {
    const result = await chatSearch({ query: "Rechnung" }, undefined);
    assert.equal(result.content[0].text, CHAT_SEARCH_NO_SESSION);
    assert.equal(aufgerufen, false);
  } finally {
    await stop(server);
  }
});

test("das Gespraech kommt aus der Laufzeit, nicht aus den Argumenten", async () => {
  let url = null;
  let kopf = null;
  const server = await startStub((req, res) => {
    url = new URL(req.url, "http://x");
    kopf = req.headers;
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify({
      total: 3,
      matches: [{ message_id: "m9", role: "user", timestamp: "2026-10-01T09:00:00", excerpt: "Rechnung 4711" }],
    }));
  });
  try {
    const result = await chatSearch(
      { query: "  Rechnung 4711 ", limit: 99, session_id: "fremd" },
      "sitzung-1",
    );
    assert.equal(url.pathname, "/api/v1/agents/agent-1/chat/search");
    assert.equal(url.searchParams.get("session_id"), "sitzung-1");
    assert.equal(url.searchParams.get("q"), "Rechnung 4711");
    assert.equal(url.searchParams.get("limit"), "30");
    assert.equal(kopf["x-agent-id"], "agent-1");
    const text = result.content[0].text;
    assert.match(text, /1 of 3 matches/);
    assert.match(text, /\[EXTERNAL-DATA source="chat-history"\]/);
    assert.match(text, /user \(m9\): Rechnung 4711/);
  } finally {
    await stop(server);
  }
});

test("AI_CHAT_SESSION aus der Umgebung ist der Standard", async () => {
  let url = null;
  const server = await startStub((req, res) => {
    url = new URL(req.url, "http://x");
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify({ total: 0, matches: [] }));
  });
  process.env.AI_CHAT_SESSION = "aus-der-umgebung";
  try {
    const result = await chatSearch({ query: "x" });
    assert.equal(url.searchParams.get("session_id"), "aus-der-umgebung");
    assert.match(result.content[0].text, /No messages in this conversation match/);
  } finally {
    delete process.env.AI_CHAT_SESSION;
    await stop(server);
  }
});

test("leere und zu lange Anfragen gehen nicht an den Server", async () => {
  assert.match((await chatSearch({ query: "  " }, "s")).content[0].text, /query cannot be empty/);
  assert.match((await chatSearch({ query: "x".repeat(201) }, "s")).content[0].text, /too long/);
});

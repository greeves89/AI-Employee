import { test } from "node:test";
import assert from "node:assert/strict";
import http from "node:http";

// AGENT_HOOK_URL wird beim Laden gelesen — vor dem Import setzen.
const port = 39080 + Math.floor(Math.random() * 1000);
process.env.AGENT_HOOK_URL = `http://127.0.0.1:${port}/hooks/pretooluse`;
process.env.AGENT_HOOK_TIMEOUT_MS = "300";
const { entscheide } = await import("./pretooluse-hook.mjs");

function stub(antwort, mitschrift) {
  return new Promise((resolve) => {
    const server = http.createServer((req, res) => {
      let body = "";
      req.on("data", (c) => (body += c));
      req.on("end", () => {
        mitschrift?.push(JSON.parse(body));
        res.writeHead(200, { "content-type": "application/json" });
        res.end(JSON.stringify(antwort));
      });
    });
    server.listen(port, "127.0.0.1", () => resolve(server));
  });
}
const stop = (s) => new Promise((r) => s.close(r));

// So sieht die Eingabe aus, die Codex schickt (live mitgeschnitten, gekuerzt).
const CODEX_EINGABE = JSON.stringify({
  hook_event_name: "PreToolUse",
  permission_mode: "bypassPermissions",
  tool_name: "Bash",
  tool_input: { command: "echo hallo > datei.txt" },
  tool_use_id: "exec-1",
});

test("Ablehnung wird im Codex-Format weitergegeben", async () => {
  const mitschrift = [];
  const s = await stub(
    { hookSpecificOutput: { permissionDecision: "deny", permissionDecisionReason: "braucht shell_exec" } },
    mitschrift,
  );
  try {
    const aus = JSON.parse(await entscheide(CODEX_EINGABE));
    assert.equal(aus.hookSpecificOutput.hookEventName, "PreToolUse");
    assert.equal(aus.hookSpecificOutput.permissionDecision, "deny");
    assert.equal(aus.hookSpecificOutput.permissionDecisionReason, "braucht shell_exec");
    assert.deepEqual(mitschrift[0], { tool_name: "Bash", tool_input: { command: "echo hallo > datei.txt" } });
  } finally {
    await stop(s);
  }
});

test("Erlaubnis: keine Ausgabe", async () => {
  const s = await stub({ hookSpecificOutput: { permissionDecision: "allow" } });
  try {
    assert.equal(await entscheide(CODEX_EINGABE), "");
  } finally {
    await stop(s);
  }
});

test("Entscheidungspunkt nicht erreichbar: abgelehnt (fail-closed)", async () => {
  const aus = JSON.parse(await entscheide(CODEX_EINGABE));
  assert.equal(aus.hookSpecificOutput.permissionDecision, "deny");
});

test("Entscheidungspunkt haengt: abgelehnt, bevor die CLI den Hook abbricht", async () => {
  // Ein angehaltener Server nimmt die Verbindung an und antwortet nie.
  const s = await new Promise((resolve) => {
    const server = http.createServer(() => {});
    server.listen(port, "127.0.0.1", () => resolve(server));
  });
  try {
    const start = Date.now();
    const aus = JSON.parse(await entscheide(CODEX_EINGABE));
    assert.equal(aus.hookSpecificOutput.permissionDecision, "deny");
    assert.ok(Date.now() - start < 5_000);
  } finally {
    s.closeAllConnections();
    await stop(s);
  }
});

test("kaputte Eingabe: abgelehnt", async () => {
  const aus = JSON.parse(await entscheide("kein json"));
  assert.equal(aus.hookSpecificOutput.permissionDecision, "deny");
});

test("Fehlerstatus oder Antwort ohne Entscheidung: abgelehnt", async () => {
  const s = await stub({ etwas: "anderes" });
  try {
    const aus = JSON.parse(await entscheide(CODEX_EINGABE));
    assert.equal(aus.hookSpecificOutput.permissionDecision, "deny");
  } finally {
    await stop(s);
  }
});

test("Push-Sperre: Arbeitsverzeichnis und Lauf gehen an den Hook (#1117)", async () => {
  const mitschrift = [];
  const s = await stub({ hookSpecificOutput: { permissionDecision: "allow" } }, mitschrift);
  process.env.AI_EMPLOYEE_LAUF = "t-takt";
  try {
    const eingabe = JSON.stringify({ tool_name: "Bash", tool_input: { command: "git push" }, cwd: "/workspace/r" });
    assert.equal(await entscheide(eingabe), "");
    assert.deepEqual(mitschrift[0], {
      tool_name: "Bash", tool_input: { command: "git push" }, cwd: "/workspace/r", lauf: "t-takt",
    });
  } finally {
    delete process.env.AI_EMPLOYEE_LAUF;
    await stop(s);
  }
});

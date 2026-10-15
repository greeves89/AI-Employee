import { test } from "node:test";
import assert from "node:assert/strict";
import http from "node:http";

// AGENT_HOOK_URL wird beim Laden gelesen — vor dem Import setzen.
const port = 39080 + Math.floor(Math.random() * 1000);
process.env.AGENT_HOOK_URL = `http://127.0.0.1:${port}/hooks/pretooluse`;
const { entscheide } = await import("./codex-hook.mjs");

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

test("Hook nicht erreichbar: keine Ausgabe (fail-open wie bei Claude Code)", async () => {
  assert.equal(await entscheide(CODEX_EINGABE), "");
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

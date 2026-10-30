import { test } from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import { spawnSync } from "node:child_process";

// Adresse und Wartezeit sind im Skript fest verdrahtet; Tests geben sie mit.
const port = 39080 + Math.floor(Math.random() * 1000);
const ZIEL = { url: `http://127.0.0.1:${port}/hooks/pretooluse`, timeoutMs: 300 };
const { entscheide: echt, laufErmitteln } = await import("./pretooluse-hook.mjs");
const entscheide = (roh, extra = {}) => echt(roh, { ...ZIEL, ...extra });

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
  try {
    const eingabe = JSON.stringify({ tool_name: "Bash", tool_input: { command: "git push" }, cwd: "/workspace/r" });
    assert.equal(await entscheide(eingabe, { lauf: "t-takt" }), "");
    assert.deepEqual(mitschrift[0], {
      tool_name: "Bash", tool_input: { command: "git push" }, cwd: "/workspace/r", lauf: "t-takt",
    });
  } finally {
    await stop(s);
  }
});

test("Lauf: aus der eigenen Umgebung (Codex)", () => {
  assert.equal(laufErmitteln({ AI_EMPLOYEE_LAUF: "t-1" }, 999999999), "t-1");
});

test("Lauf: sonst aus der Startumgebung der aufrufenden CLI (Claude Code)", { skip: process.platform !== "linux" }, () => {
  // Eltern = "die CLI" mit Lauf in der Startumgebung; Kind = der Hook mit leerer Umgebung.
  const kind = `import('./pretooluse-hook.mjs').then(m => process.stdout.write(String(m.laufErmitteln())))`;
  const eltern = `const r = require('node:child_process').spawnSync(process.execPath, ['--input-type=module', '-e', ${JSON.stringify(kind)}], { env: {}, encoding: 'utf8' }); process.stdout.write(r.stdout)`;
  const r = spawnSync(process.execPath, ["-e", eltern], {
    env: { AI_EMPLOYEE_LAUF: "t-eltern" }, cwd: new URL(".", import.meta.url).pathname, encoding: "utf8",
  });
  assert.equal(r.stdout, "t-eltern");
});

test("Lauf: unpassende Werte werden verworfen", () => {
  assert.equal(laufErmitteln({ AI_EMPLOYEE_LAUF: "a b; rm -rf /" }, 1), undefined);
  assert.equal(laufErmitteln({ AI_EMPLOYEE_LAUF: "" }, 1), undefined);
});

test("Als Prozess: Ablehnung endet mit Exit 2, Grund auf stderr", () => {
  const skript = new URL("./pretooluse-hook.mjs", import.meta.url).pathname;
  for (const eingabe of ["kein json", JSON.stringify({ tool_name: "Bash", tool_input: { command: "x" } })]) {
    // Ohne Pruefstelle (fester Port 8080 ist im Test nicht belegt) bzw. mit kaputter Eingabe.
    const r = spawnSync(process.execPath, [skript], { input: eingabe, env: {}, encoding: "utf8", timeout: 20_000 });
    assert.equal(r.status, 2, r.stderr);
    assert.equal(r.stdout, "");
    assert.match(r.stderr, /abgelehnt/);
  }
});

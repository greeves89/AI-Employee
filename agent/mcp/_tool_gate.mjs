/**
 * Autonomie-Gate fuer Codex-eigene MCP-Server (Issue #781, #197 Teil 3).
 *
 * Custom-LLM und Claude Code haben seit #780 echte Werkzeug-Durchsetzung —
 * Codex CLI hatte bislang null: `codex exec` kennt keinen Freigabe-Schalter
 * und keinen Hook-Mechanismus. Fuer Codexs MCP-Server (brain/memory/
 * notification/orchestrator/computer-use/read-logs/msgraph/email/hyperframes/
 * skill — Werkzeuge OHNE native Codex-Entsprechung) gibt es aber bereits einen
 * echten Abfangpunkt: den lokalen PreToolUse-Hook, den Claude Code laengst
 * nutzt (`agent/app/health.py::pretooluse_hook_handler`, der wiederum
 * `agent/app/tools/pretooluse_hook.py::decide_async()` ruft). Dieser Endpunkt
 * entpackt `mcp__<server>__<tool>`-Namen bereits selbst und kategorisiert sie
 * — dieses Modul fuehrt bewusst KEINE eigene Kategorisierungslogik ein,
 * sondern ist nur ein duenner Relay zu genau demselben, seit #780 bewaehrten
 * Entscheidungspfad.
 *
 * FAIL-CLOSED bei Netzwerkfehler/Timeout/Nicht-2xx/unlesbarer Antwort (laut
 * geloggt) — wie das Hook-Skript (`pretooluse-hook.mjs`) und die Regelquellen in
 * `executor.py`, die bei einem Ausfall den letzten bekannten Stand nutzen und
 * ohne ihn sperren (P1-8). Bis 1.421 liess das Gate in diesen Faellen durch.
 */

import { CallToolRequestSchema } from "@modelcontextprotocol/sdk/types.js";

const HOOK_URL = process.env.AGENT_HOOK_URL || "http://localhost:8080/hooks/pretooluse";
// Gleicher Wert wie die Wartezeit des verwalteten PreToolUse-Hooks
// (pretooluse-hook.mjs, agent/richtlinie/) — deckt denselben
// Worst-Case ab (zwei sequenzielle, kalte Orchestrator-Aufrufe innerhalb von
// decide_async()).
const HOOK_TIMEOUT_MS = 12_000;
const NICHT_PRUEFBAR = "Werkzeugpruefung nicht erreichbar — aus Sicherheitsgruenden abgelehnt.";

/**
 * Fragt den lokalen PreToolUse-Hook, ob dieser MCP-Werkzeugaufruf unter der
 * aktuellen Autonomie-Konfiguration des Agenten erlaubt ist.
 *
 * @param {string} serverLabel - kosmetisch: `decide()` wertet vom Namen
 *   `mcp__<server>__<tool>` nur das LETZTE "__"-Segment aus, das mittlere
 *   Server-Label beeinflusst die Entscheidung selbst nicht, taucht aber in
 *   Logs/Audit auf. Sollte mit dem Schluessel uebereinstimmen, den
 *   `codex_runner.py`s `builtin_servers`-Dict fuer denselben Server nutzt.
 * @param {string} toolName - `request.params.name`
 * @param {object} [toolInput] - `request.params.arguments`
 * @returns {Promise<{allowed: true} | {allowed: false, reason: string}>}
 *   Wirft NIE — Netzwerk-/Parsefehler, Nicht-2xx-Antworten und Antworten ohne
 *   Entscheidung loesen `{allowed: false}` aus (fail-closed, siehe Moduldoku),
 *   mit einer lauten `console.error`-Zeile fuer die Container-Logs.
 */
export async function checkToolPermission(serverLabel, toolName, toolInput) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), HOOK_TIMEOUT_MS);
  try {
    const res = await fetch(HOOK_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        tool_name: `mcp__${serverLabel}__${toolName}`,
        tool_input: toolInput || {},
      }),
      signal: controller.signal,
    });
    if (!res.ok) {
      console.error(
        `[tool-gate] PreToolUse-Hook antwortete HTTP ${res.status} fuer ${serverLabel}/${toolName} — abgelehnt (fail-closed)`
      );
      return { allowed: false, reason: NICHT_PRUEFBAR };
    }
    const body = await res.json();
    const decision = body?.hookSpecificOutput?.permissionDecision;
    if (decision === "deny") {
      return {
        allowed: false,
        reason:
          body.hookSpecificOutput.permissionDecisionReason ||
          "Werkzeug durch Autonomie-Regeln blockiert.",
      };
    }
    if (decision !== "allow") {
      console.error(
        `[tool-gate] PreToolUse-Hook ohne Entscheidung fuer ${serverLabel}/${toolName} — abgelehnt (fail-closed)`
      );
      return { allowed: false, reason: NICHT_PRUEFBAR };
    }
    return { allowed: true };
  } catch (err) {
    console.error(
      `[tool-gate] PreToolUse-Hook fuer ${serverLabel}/${toolName} nicht erreichbar: ${err.message} — abgelehnt (fail-closed)`
    );
    return { allowed: false, reason: NICHT_PRUEFBAR };
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Umschliesst einen CallTool-Handler mit dem Autonomie-Gate: erst die
 * Entscheidung des lokalen Hooks, dann (nur bei "erlaubt") der Handler.
 * Eine Ablehnung kommt als Werkzeugfehler zurueck, ohne Seitenwirkung.
 *
 * @param {string} serverLabel
 * @param {(request: object) => Promise<object>} handler
 */
export function mitGate(serverLabel, handler) {
  return async (request) => {
    const { name, arguments: args } = request?.params || {};
    const gate = await checkToolPermission(serverLabel, name, args);
    if (!gate.allowed) {
      return { content: [{ type: "text", text: gate.reason }], isError: true };
    }
    return handler(request);
  };
}

/**
 * Schaltet das Gate fuer einen ganzen MCP-Server ein (Issue #781 PR 2): jeder
 * spaeter registrierte CallTool-Handler wird automatisch mit `mitGate`
 * umschlossen. Eine Zeile direkt nach `new Server(...)` genuegt — der Handler
 * selbst bleibt unveraendert, und ein neu hinzugefuegter Server kann das Gate
 * nicht vergessen, solange er diese Zeile traegt (Test in
 * agent/tests/test_mcp_werkzeugrechte.py).
 *
 * @param {import("@modelcontextprotocol/sdk/server/index.js").Server} server
 * @param {string} serverLabel - derselbe Schluessel wie in
 *   `codex_runner.py::_ensure_codex_mcp_config` (`builtin_servers`)
 */
export function gateAktivieren(server, serverLabel) {
  const original = server.setRequestHandler.bind(server);
  server.setRequestHandler = (schema, handler) =>
    original(
      schema,
      schema === CallToolRequestSchema || schema?.shape?.method?.value === "tools/call"
        ? mitGate(serverLabel, handler)
        : handler
    );
  return server;
}

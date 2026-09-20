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
 * Fail-open bei Netzwerkfehler/Timeout/Nicht-2xx (laut geloggt) — konsistent
 * mit `executor.py::_get_allowed_categories()`/`_get_command_policies()`, die
 * aus demselben Grund fail-open sind: ein kurzer Aussetzer des lokalen Hooks
 * soll nicht saemtliche gegateten Werkzeuge in jedem Codex-Container
 * gleichzeitig blockieren. Der Aufruf ist reiner Loopback (derselbe
 * Container) und damit zuverlaessiger als die Orchestrator-Aufrufe, denen
 * dieses Verhalten abgeschaut ist.
 */

const HOOK_URL = process.env.AGENT_HOOK_URL || "http://localhost:8080/hooks/pretooluse";
// Gleicher Wert wie Claude Codes PreToolUse-Hook-Timeout
// (agent_manager.py::_CLAUDE_PRETOOLUSE_SETTINGS_JSON) — deckt denselben
// Worst-Case ab (zwei sequenzielle, kalte Orchestrator-Aufrufe innerhalb von
// decide_async()).
const HOOK_TIMEOUT_MS = 12_000;

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
 *   Wirft NIE — Netzwerk-/Parsefehler und Nicht-2xx-Antworten loesen
 *   `{allowed: true}` aus (fail-open, siehe Moduldoku), mit einer lauten
 *   `console.error`-Zeile fuer Beobachtbarkeit ueber Container-Logs
 *   (`read_logs`).
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
        `[tool-gate] PreToolUse-Hook antwortete HTTP ${res.status} fuer ${serverLabel}/${toolName} — laesst durch (fail-open)`
      );
      return { allowed: true };
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
    return { allowed: true };
  } catch (err) {
    console.error(
      `[tool-gate] PreToolUse-Hook fuer ${serverLabel}/${toolName} nicht erreichbar: ${err.message} — laesst durch (fail-open)`
    );
    return { allowed: true };
  } finally {
    clearTimeout(timer);
  }
}

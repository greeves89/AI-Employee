#!/usr/bin/env node
/**
 * PreToolUse-Hook fuer Codex CLI (Issue #781 PR 3).
 *
 * Codex kann seit den 0.1xx-Versionen Hooks — aber nur als Kommando, nicht als
 * HTTP-Aufruf wie Claude Code. Dieses Skript ist die Bruecke: es liest die
 * Hook-Eingabe von stdin, fragt denselben lokalen Entscheidungspunkt, den
 * Claude Code direkt anspricht (`agent/app/health.py::pretooluse_hook_handler`),
 * und gibt eine Ablehnung im Format aus, das Codex versteht (dasselbe wie bei
 * Claude Code: `hookSpecificOutput.permissionDecision = "deny"`).
 *
 * Abgedeckt sind damit alle Werkzeuge, die Codex durch PreToolUse schickt:
 * Shell (`Bash`), Dateiaenderungen (`apply_patch`) und MCP-Werkzeuge
 * (`mcp__<server>__<werkzeug>`). Am 15.10.2026 gegen codex-cli 0.159 live
 * geprueft: eine Ablehnung verhindert Befehl und Dateiaenderung, auch mit
 * `--dangerously-bypass-approvals-and-sandbox`.
 *
 * Fail-open wie der Claude-Code-Hook: ist der lokale Entscheidungspunkt nicht
 * erreichbar, gibt das Skript nichts aus und endet mit 0 — Codex fuehrt das
 * Werkzeug dann aus. Begruendung siehe `_tool_gate.mjs`.
 */

const HOOK_URL = process.env.AGENT_HOOK_URL || "http://localhost:8080/hooks/pretooluse";
const HOOK_TIMEOUT_MS = 12_000;

async function stdinLesen() {
  const teile = [];
  for await (const teil of process.stdin) teile.push(teil);
  return Buffer.concat(teile).toString("utf8");
}

/**
 * Entscheidung fuer eine Codex-Hook-Eingabe. Gibt den auszugebenden Text
 * zurueck ("" = nichts ausgeben = erlaubt).
 *
 * @param {string} roh - stdin von Codex (JSON)
 * @returns {Promise<string>}
 */
export async function entscheide(roh) {
  let eingabe;
  try {
    eingabe = JSON.parse(roh || "{}");
  } catch {
    console.error("[codex-hook] Eingabe ist kein JSON — laesst durch (fail-open)");
    return "";
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), HOOK_TIMEOUT_MS);
  try {
    const res = await fetch(HOOK_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        tool_name: eingabe.tool_name || "",
        tool_input: eingabe.tool_input || {},
      }),
      signal: controller.signal,
    });
    if (!res.ok) {
      console.error(`[codex-hook] Hook antwortete HTTP ${res.status} — laesst durch (fail-open)`);
      return "";
    }
    const body = await res.json();
    const out = body?.hookSpecificOutput;
    if (out?.permissionDecision !== "deny") return "";
    return JSON.stringify({
      hookSpecificOutput: {
        hookEventName: "PreToolUse",
        permissionDecision: "deny",
        permissionDecisionReason: out.permissionDecisionReason || "Werkzeug durch Autonomie-Regeln blockiert.",
      },
    });
  } catch (err) {
    console.error(`[codex-hook] Hook nicht erreichbar: ${err.message} — laesst durch (fail-open)`);
    return "";
  } finally {
    clearTimeout(timer);
  }
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const ausgabe = await entscheide(await stdinLesen());
  if (ausgabe) process.stdout.write(ausgabe);
  process.exit(0);
}

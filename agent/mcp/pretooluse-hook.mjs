#!/usr/bin/node
/**
 * PreToolUse-Hook fuer Claude Code UND Codex CLI (#781, Sicherheit P0-2).
 *
 * Beide CLIs rufen dieses Skript vor JEDEM Werkzeugaufruf auf. Es liest die
 * Hook-Eingabe von stdin, fragt den lokalen Entscheidungspunkt
 * (`agent/app/health.py::pretooluse_hook_handler`) und gibt eine Ablehnung im
 * gemeinsamen Format aus (`hookSpecificOutput.permissionDecision = "deny"`).
 *
 * Eingetragen ist es nicht mehr in Dateien, die der Agent selbst schreiben kann
 * (`/workspace/.claude/settings.json`, `~/.codex/config.toml`), sondern als
 * verwalteter Hook im Abbild: `/etc/claude-code/managed-settings.json` und
 * `/etc/codex/requirements.toml` (Quelle: `agent/richtlinie/`), beide root-eigen,
 * mit "nur verwaltete Hooks". Live geprueft (Claude Code 2.1.312, Codex 0.165):
 * `disableAllHooks` in Projekt, Nutzer und `--settings` bzw.
 * `-c features.hooks=false` schalten ihn nicht ab.
 *
 * FAIL-CLOSED: Ist der Entscheidungspunkt nicht erreichbar, antwortet er zu
 * spaet, mit Fehlerstatus oder unlesbar, wird abgelehnt. Bis 1.405 liess das
 * Skript bei Nichterreichbarkeit durch, und Claude Codes HTTP-Hook tut das
 * dokumentiert immer — wer den lokalen Server anhielt, war die Pruefung los.
 * Die eigene Wartezeit liegt unter dem Hook-Timeout (15 s): ein Hook, den die
 * CLI wegen Zeitueberschreitung abbricht, blockiert NICHT.
 */

// Fest verdrahtet, NICHT aus der Umgebung: die Umgebung des Hooks erbt der
// Agent von seiner CLI — eine eigene Adresse dort haette die Pruefung umgeleitet.
const HOOK_URL = "http://localhost:8080/hooks/pretooluse";
const HOOK_TIMEOUT_MS = 12_000;

function ablehnung(grund) {
  return JSON.stringify({
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason: grund,
    },
  });
}

async function stdinLesen() {
  const teile = [];
  for await (const teil of process.stdin) teile.push(teil);
  return Buffer.concat(teile).toString("utf8");
}

/**
 * Entscheidung fuer eine Codex-Hook-Eingabe. Gibt den auszugebenden Text
 * zurueck ("" = nichts ausgeben = erlaubt).
 *
 * @param {string} roh - stdin der CLI (JSON)
 * @param {{url?: string, timeoutMs?: number}} [fuerTests] - nur fuer Tests
 * @returns {Promise<string>}
 */
export async function entscheide(roh, { url = HOOK_URL, timeoutMs = HOOK_TIMEOUT_MS } = {}) {
  let eingabe;
  try {
    eingabe = JSON.parse(roh || "{}");
  } catch {
    console.error("[pretooluse-hook] Eingabe ist kein JSON — abgelehnt");
    return ablehnung("Werkzeugpruefung: Eingabe unlesbar.");
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      // cwd und lauf: fuer die Push-Sperre (#1117, agent/app/tools/push_sperre.py)
      // — Arbeitsverzeichnis des Befehls und der laufende Task (AI_EMPLOYEE_LAUF
      // setzt der Codex-Runner). Beides darf fehlen.
      body: JSON.stringify({
        tool_name: eingabe.tool_name || "",
        tool_input: eingabe.tool_input || {},
        ...(typeof eingabe.cwd === "string" ? { cwd: eingabe.cwd } : {}),
        ...(process.env.AI_EMPLOYEE_LAUF ? { lauf: process.env.AI_EMPLOYEE_LAUF } : {}),
      }),
      signal: controller.signal,
    });
    if (!res.ok) {
      console.error(`[pretooluse-hook] Hook antwortete HTTP ${res.status} — abgelehnt`);
      return ablehnung(`Werkzeugpruefung fehlgeschlagen (HTTP ${res.status}).`);
    }
    let out;
    try {
      out = (await res.json())?.hookSpecificOutput;
    } catch {
      return ablehnung("Werkzeugpruefung: Antwort unlesbar.");
    }
    if (out?.permissionDecision === "allow") return "";
    if (out?.permissionDecision === "deny") {
      return ablehnung(out.permissionDecisionReason || "Werkzeug durch Autonomie-Regeln blockiert.");
    }
    return ablehnung("Werkzeugpruefung: keine Entscheidung erhalten.");
  } catch (err) {
    console.error(`[pretooluse-hook] Entscheidungspunkt nicht erreichbar: ${err.message} — abgelehnt`);
    return ablehnung("Werkzeugpruefung nicht erreichbar — aus Sicherheitsgruenden abgelehnt.");
  } finally {
    clearTimeout(timer);
  }
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const ausgabe = await entscheide(await stdinLesen());
  if (ausgabe) process.stdout.write(ausgabe);
  process.exit(0);
}

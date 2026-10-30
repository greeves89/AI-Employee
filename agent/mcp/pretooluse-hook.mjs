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
 * Abgelehnt wird mit Exit-Code 2 (Grund auf stderr), nicht per JSON auf stdout.
 *
 * FAIL-CLOSED: Ist der Entscheidungspunkt nicht erreichbar, antwortet er zu
 * spaet, mit Fehlerstatus oder unlesbar, wird abgelehnt. Bis 1.405 liess das
 * Skript bei Nichterreichbarkeit durch, und Claude Codes HTTP-Hook tut das
 * dokumentiert immer — wer den lokalen Server anhielt, war die Pruefung los.
 * Die eigene Wartezeit liegt unter dem Hook-Timeout (15 s): ein Hook, den die
 * CLI wegen Zeitueberschreitung abbricht, blockiert NICHT.
 */

import { readFileSync } from "node:fs";

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

const LAUF_ENV = "AI_EMPLOYEE_LAUF";
const LAUF_FORM = /^[A-Za-z0-9_.:-]{1,128}$/;

/**
 * Der laufende Task fuer die Push-Sperre (#1117).
 *
 * Codex reicht ihn ueber das Hook-Kommando weiter (``env -i AI_EMPLOYEE_LAUF=...``).
 * Claude Code startet den Hook ohne Shell und mit leerer Umgebung — dort steht
 * der Lauf in der START-Umgebung der aufrufenden CLI (/proc/<ppid>/environ), die
 * der Runner setzt. Was der Agent in ~/.claude/settings.json ("env") eintraegt,
 * landet dort nicht (live geprueft).
 */
export function laufErmitteln(env = process.env, ppid = process.ppid) {
  let wert = env[LAUF_ENV];
  if (wert === undefined) {
    try {
      const zeile = readFileSync(`/proc/${ppid}/environ`, "latin1")
        .split("\0")
        .find((z) => z.startsWith(`${LAUF_ENV}=`));
      wert = zeile ? zeile.slice(LAUF_ENV.length + 1) : undefined;
    } catch {
      wert = undefined;
    }
  }
  return typeof wert === "string" && LAUF_FORM.test(wert) ? wert : undefined;
}

/**
 * Entscheidung fuer eine Codex-Hook-Eingabe. Gibt den auszugebenden Text
 * zurueck ("" = nichts ausgeben = erlaubt).
 *
 * @param {string} roh - stdin der CLI (JSON)
 * @param {{url?: string, timeoutMs?: number}} [fuerTests] - nur fuer Tests
 * @returns {Promise<string>}
 */
export async function entscheide(roh, { url = HOOK_URL, timeoutMs = HOOK_TIMEOUT_MS, lauf = laufErmitteln() } = {}) {
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
      // — Arbeitsverzeichnis des Befehls und der laufende Task. Beides darf fehlen.
      body: JSON.stringify({
        tool_name: eingabe.tool_name || "",
        tool_input: eingabe.tool_input || {},
        ...(typeof eingabe.cwd === "string" ? { cwd: eingabe.cwd } : {}),
        ...(lauf ? { lauf } : {}),
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

// Ablehnen mit Exit-Code 2 und Grund auf stderr — NICHT als JSON auf stdout. Ist
// stdout unlesbar (live gesehen: LD_SHOW_AUXV schreibt Lader-Daten davor), werten
// beide CLIs das als "kein Einwand". Exit 2 blockiert unabhaengig von stdout.
if (import.meta.url === `file://${process.argv[1]}`) {
  // Jeder unerwartete Fehler endet mit Exit 2: Node beendet sich sonst mit 1, und
  // jeder andere Code als 2 gilt den CLIs als "kein Einwand".
  const ablehnen = (fehler) => {
    process.stderr.write(`Werkzeugpruefung fehlgeschlagen — abgelehnt (${fehler?.message || fehler}).\n`);
    process.exit(2);
  };
  process.on("uncaughtException", ablehnen);
  process.on("unhandledRejection", ablehnen);
  let ausgabe;
  try {
    ausgabe = await entscheide(await stdinLesen());
  } catch (fehler) {
    ablehnen(fehler);
  }
  if (ausgabe) {
    let grund = "Werkzeug durch Autonomie-Regeln blockiert.";
    try {
      grund = JSON.parse(ausgabe).hookSpecificOutput.permissionDecisionReason || grund;
    } catch {
      // Grund bleibt allgemein; abgelehnt wird trotzdem.
    }
    process.stderr.write(`${grund}\n`);
    process.exit(2);
  }
  process.exit(0);
}

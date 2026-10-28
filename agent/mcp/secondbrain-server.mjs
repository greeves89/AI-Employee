#!/usr/bin/env node
/**
 * Second-Brain-MCP-Server — der GETEILTE Abteilungs-Vault unter /mnt/brains/<slug>/.
 *   secondbrain_search — Hybrid-Suche (Vektor + Volltext, RRF) ueber die
 *                        dem Agenten zugewiesenen Vaults
 *   secondbrain_write  — Artikel schreiben UND sofort indexieren
 *   secondbrain_read   — eine Vault-Datei lesen (direkt von der Platte)
 *   secondbrain_list   — was im Vault ueberhaupt liegt (direkt von der Platte)
 *
 * Warum dieser Server ueberhaupt existiert (#727): Claude-Code-Agenten hatten
 * GAR KEIN `secondbrain_*`-Werkzeug — `agent/mcp/` kannte nur `brain-server.mjs`
 * (die persoenliche Wissensdatenbank des Nutzers, etwas voellig anderes) und
 * `memory-server.mjs`. Der am meisten genutzte Agententyp war damit der einzige
 * ohne Zugang zum Abteilungswissen.
 *
 * Die ersten zwei gehen ueber den Orchestrator (er hat den Index und kennt die
 * Zuweisung), die letzten zwei direkt auf die Platte — der Vault ist in den
 * Container gemountet, und fuer Lesen und Auflisten braucht es keinen Index.
 *
 * Warum Lesen und Auflisten trotzdem als eigene Werkzeuge noetig sind, obwohl
 * Claude Code Read/Glob hat: die Prompt-Stellen nennen `secondbrain_read` und
 * `secondbrain_list` fuer ALLE Laufzeiten. Gaebe es sie hier nicht, waere das
 * fuer den haeufigsten Agententyp ein Fehlaufruf pro Nachschlagen — und die
 * Datei-Landkarte im Startkontext verweist ausdruecklich auf `secondbrain_list`
 * als Weg zum vollstaendigen Stand. Ein Prompt, der ein Werkzeug nennt, das die
 * Laufzeit nicht hat, ist schlimmer als eine fehlende Empfehlung.
 */

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { startServer } from "./_transport.mjs";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import { gateAktivieren } from "./_tool_gate.mjs";
import fs from "node:fs";
import nodePath from "node:path";

const ORCHESTRATOR_URL = process.env.ORCHESTRATOR_URL || "http://ai-employee-orchestrator:8000";
const AGENT_ID = process.env.AGENT_ID || "";
const AGENT_TOKEN = process.env.AGENT_TOKEN || "";

/** Hinweis, der an jede Fehlermeldung gehaengt wird: der Vault bleibt lesbar. */
const RUECKFALL_HINWEIS =
  "Der Vault ist unter /mnt/brains/ gemountet und bleibt auch ohne Orchestrator " +
  "lesbar — notfalls mit den eigenen Datei-Werkzeugen (Grep/Read) weitersuchen. " +
  "Eine so geschriebene Datei steht allerdings erst nach dem naechsten " +
  "Re-Index-Durchlauf (alle 5 Minuten) im Suchindex.";

export const BRAINS_ROOT = "/mnt/brains";

/** Vault-relativen Pfad auf eine echte Datei unter /mnt/brains aufloesen.
 *
 * Gleiche Schranke wie ``ToolExecutor._resolve_brain_path``: AUFGELOEST
 * vergleichen (``realpath``), nicht die Zeichenkette — sonst kommt ein
 * ``..``-Segment oder ein Verweis am Jail vorbei. Der Mount selbst ist hier die
 * Berechtigung: gemountet ist nur, was dem Agenten zugewiesen wurde.
 */
export function vaultPfad(eingabe, wurzel = BRAINS_ROOT) {
  let root;
  try {
    root = fs.realpathSync(wurzel);
  } catch {
    root = nodePath.resolve(wurzel);
  }
  const roh = String(eingabe || "").trim();
  if (!roh) throw new Error("'path' is required");
  const basis = roh.startsWith(wurzel)
    ? nodePath.join(root, roh.slice(wurzel.length).replace(/^\/+/, ""))
    : nodePath.join(root, roh.replace(/^\/+/, ""));
  // realpath nur, wenn es existiert — sonst normalisieren und danach pruefen.
  let ziel;
  try {
    ziel = fs.realpathSync(basis);
  } catch {
    ziel = nodePath.resolve(basis);
  }
  if (ziel !== root && !ziel.startsWith(root + nodePath.sep)) {
    throw new Error("path escapes /mnt/brains");
  }
  return ziel;
}

/** Die gemounteten Vaults — ein Verzeichnis je zugewiesenem Brain. */
export function vaultVerzeichnisse(wurzel = BRAINS_ROOT) {
  try {
    return fs
      .readdirSync(wurzel, { withFileTypes: true })
      .filter((e) => e.isDirectory())
      .map((e) => nodePath.join(wurzel, e.name))
      .sort();
  } catch {
    return [];
  }
}

export function dateienVon(wurzel) {
  const out = [];
  const lauf = (dir) => {
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
      if (e.name === ".git") continue;
      const voll = nodePath.join(dir, e.name);
      if (e.isDirectory()) lauf(voll);
      else out.push(nodePath.relative(wurzel, voll));
    }
  };
  lauf(wurzel);
  return out.sort();
}


/** Eine Vault-Datei lesen — mit EINEM Zugriff, nicht drei.
 *
 * Pruefen-dann-benutzen (``existsSync``, dann ``statSync``, dann
 * ``readFileSync``) ist eine Wettlaufsituation: zwischen Pruefung und Lesen
 * kann der Pfad ein anderer sein. CodeQL meldet das zu Recht als
 * ``js/file-system-race``. Deshalb wird die Datei EINMAL geoeffnet und alles
 * weitere an der geoeffneten Kennung entschieden (``fstat`` statt ``stat``).
 */
export function leseVaultDatei(ziel, angezeigterPfad = ziel, maxBytes = 2_000_000) {
  let kennung;
  try {
    kennung = fs.openSync(ziel, "r");
  } catch (e) {
    if (e.code === "ENOENT" || e.code === "EISDIR" || e.code === "ENOTDIR") {
      throw new Error(`File not found: ${angezeigterPfad}`);
    }
    throw e;
  }
  try {
    const info = fs.fstatSync(kennung);
    if (!info.isFile()) throw new Error(`File not found: ${angezeigterPfad}`);
    if (info.size > maxBytes) throw new Error("file too large to read");
    const puffer = Buffer.allocUnsafe(info.size);
    let gelesen = 0;
    while (gelesen < info.size) {
      const n = fs.readSync(kennung, puffer, gelesen, info.size - gelesen, gelesen);
      if (n === 0) break;   // Datei wurde unter uns kuerzer
      gelesen += n;
    }
    return puffer.subarray(0, gelesen).toString("utf8");
  } finally {
    fs.closeSync(kennung);
  }
}

export function buildServer() {
  const server = new Server(
    { name: "mcp-secondbrain", version: "1.0.0" },
    { capabilities: { tools: {} } }
  );
  gateAktivieren(server, "secondbrain");  // Autonomie-Gate fuer jeden Werkzeugaufruf (#781)

  async function apiCall(path, options = {}) {
    const url = `${ORCHESTRATOR_URL}/api/v1${path}`;
    const res = await fetch(url, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        "X-Agent-ID": AGENT_ID,
        Authorization: `Bearer ${AGENT_TOKEN}`,
        ...(options.headers || {}),
      },
    });
    if (!res.ok) {
      let detail = (await res.text()).slice(0, 300);
      try {
        detail = JSON.parse(detail).detail ?? detail;
      } catch {
        // kein JSON — Rohtext behalten
      }
      throw new Error(`Second Brain API ${path} → ${res.status}: ${detail}`);
    }
    return res.json();
  }

  server.setRequestHandler(ListToolsRequestSchema, async () => ({
    tools: [
      {
        name: "secondbrain_search",
        description:
          "Search the SHARED department Second Brain vault(s) — the Markdown knowledge base " +
          "mounted under /mnt/brains/<slug>/ and used by many people. This runs the real hybrid " +
          "search (semantic embeddings + full text, fused by Reciprocal Rank Fusion), so it also " +
          "finds paraphrases and crosses language borders (asked in German, filed in English) — " +
          "use it INSTEAD of grep for support / how-to / troubleshooting questions (error codes, " +
          "devices, procedures). This is NOT the user's personal Knowledge Base (that is " +
          "brain_search). Only vaults assigned to this agent are searched.",
        inputSchema: {
          type: "object",
          properties: {
            query: {
              type: "string",
              description: "Keywords — error codes, device names, topics.",
            },
            limit: { type: "number", description: "Max files to return (default 10, max 50)." },
            label: {
              type: "string",
              description:
                "Optional: restrict to ONE vault by slug or label. Anything outside the " +
                "vaults assigned to this agent is rejected, not silently ignored.",
            },
          },
          required: ["query"],
        },
      },
      {
        name: "secondbrain_write",
        description:
          "Create or overwrite a Markdown article in the SHARED department Second Brain vault " +
          "(knowledge the whole department sees). USE THIS to write into the Second Brain / vault " +
          "— not brain_contribute, and not a plain file write: this call also puts the article " +
          "into the search index immediately, so the very next secondbrain_search finds it. A " +
          "plain write to /mnt/brains/... only lands on disk and stays invisible to search until " +
          "the periodic re-index catches up. Path is vault-relative and starts with the vault " +
          "slug, e.g. 'it_operations/Drucker/HP-Fax.md'. Use speaking file names, sensible " +
          "folders, [[wikilinks]] between topics, and plain-text error codes so search hits them. " +
          "A read-only vault refuses the write.",
        inputSchema: {
          type: "object",
          properties: {
            path: {
              type: "string",
              description:
                "Vault-relative path INCLUDING the vault slug, e.g. 'it_operations/Drucker/HP-Fax.md'.",
            },
            content: { type: "string", description: "Full Markdown content of the article." },
          },
          required: ["path", "content"],
        },
      },
      {
        name: "secondbrain_read",
        description:
          "Read ONE file from the SHARED department Second Brain vault, in full. Path is " +
          "vault-relative and starts with the vault slug, e.g. 'it_operations/Drucker/HP-Fax.md' " +
          "— exactly the form secondbrain_search reports. Reads straight from the mounted vault, " +
          "so it also works when the orchestrator is unreachable. Answer from the content and " +
          "cite the file.",
        inputSchema: {
          type: "object",
          properties: {
            path: {
              type: "string",
              description: "Vault-relative path INCLUDING the vault slug.",
            },
          },
          required: ["path"],
        },
      },
      {
        name: "secondbrain_list",
        description:
          "List what is in the assigned Second Brain vault(s) at all — the full file tree per " +
          "vault, with its read-only/read-write mode. Use it to get your bearings when a search " +
          "comes back empty, or to see the complete picture behind the file map in your start " +
          "context. Reads straight from the mounted vault.",
        inputSchema: { type: "object", properties: {} },
      },
    ],
  }));

  server.setRequestHandler(CallToolRequestSchema, async (request) => {
    const { name, arguments: args } = request.params;

    try {
      if (name === "secondbrain_search") {
        const query = (args?.query || "").trim();
        if (!query) throw new Error("'query' is required");
        const limit = Math.min(Math.max(Number(args?.limit) || 10, 1), 50);
        const params = new URLSearchParams({ q: query, limit: String(limit) });
        if (args?.label) params.set("label", String(args.label));

        const result = await apiCall(`/brains/agent/search?${params}`);
        const hits = result.hits || [];
        if (!hits.length) {
          return {
            content: [
              {
                type: "text",
                text: `No matches for '${query}' in any assigned Second Brain. (Suche: ${result.mode || "hybrid"})`,
              },
            ],
          };
        }
        let text = `[SECOND BRAIN — ${result.mode || "hybrid"} | ${hits.length} Treffer]\n\n`;
        for (const h of hits) {
          text += `## ${h.brain}/${h.path}\n`;
          for (const s of (h.snippets || []).slice(0, 4)) text += `  · ${s}\n`;
          text += "\n";
        }
        if (result.mode === "grep") {
          text +=
            "(Hinweis: aus dem Dateiweg beantwortet, nicht aus dem Index — der Index ist " +
            "noch nicht vollstaendig.)\n";
        }
        return { content: [{ type: "text", text }] };
      }

      if (name === "secondbrain_write") {
        const path = (args?.path || "").trim();
        if (!path) throw new Error("'path' is required");
        if (args?.content === undefined || args?.content === null) {
          throw new Error("'content' is required");
        }
        const result = await apiCall("/brains/agent/write", {
          method: "POST",
          body: JSON.stringify({ path, content: args.content }),
        });
        return {
          content: [
            {
              type: "text",
              text:
                `Saved to Second Brain: ${result.brain}/${result.path} ` +
                `(${result.bytes} bytes, ${result.indexed_chunks} chunks indiziert — sofort auffindbar).`,
            },
          ],
        };
      }

      if (name === "secondbrain_read") {
        const ziel = vaultPfad(args?.path);
        return {
          content: [{ type: "text", text: leseVaultDatei(ziel, args?.path) }],
        };
      }

      if (name === "secondbrain_list") {
        const dirs = vaultVerzeichnisse();
        if (!dirs.length) {
          return {
            content: [
              {
                type: "text",
                text:
                  "No Second Brain is mounted for this agent. Ask an admin to assign one " +
                  "(Agent → Wissen → Second Brain).",
              },
            ],
          };
        }
        const bloecke = dirs.map((d) => {
          const slug = nodePath.basename(d);
          let modus = "read-only";
          try {
            fs.accessSync(d, fs.constants.W_OK);
            modus = "read-write";
          } catch {
            // bleibt read-only
          }
          const dateien = dateienVon(d);
          const rumpf = dateien.length
            ? dateien.map((f) => `  ${f}`).join("\n")
            : "  (empty)";
          return `## ${slug}  (${modus})\n${rumpf}`;
        });
        return { content: [{ type: "text", text: bloecke.join("\n\n") }] };
      }

      throw new Error(`Unknown tool: ${name}`);
    } catch (err) {
      return {
        content: [{ type: "text", text: `Error: ${err.message}\n${RUECKFALL_HINWEIS}` }],
        isError: true,
      };
    }
  });

  return server;
}


await startServer("secondbrain", buildServer);

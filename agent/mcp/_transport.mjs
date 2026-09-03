/**
 * Gemeinsamer Transport-Bootstrap fuer die eingebauten MCP-Server (Issue #638).
 *
 * Ohne MCP_HTTP_PORT verhaelt sich `startServer` exakt wie die bisherigen zwei
 * Schlusszeilen jedes Servers (stdio, eine Instanz). Erst mit gesetztem Port
 * entsteht der HTTP-Modus.
 *
 * Warum eine Fabrik statt einer fertigen Instanz uebergeben wird: ein `Server`
 * laesst sich nur an genau einen Transport binden — `connect()` wirft sonst
 * "Already connected to a transport" (SDK shared/protocol.js). Im HTTP-Modus
 * bedient ein Prozess mehrere gleichzeitige Laeufe, also braucht jede Sitzung
 * eine eigene Instanz.
 */

import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import http from "node:http";
import { randomUUID } from "node:crypto";

const MAX_BODY_BYTES = 8 * 1024 * 1024;

// Ein Servername besteht aus Kleinbuchstaben, Ziffern und Bindestrichen — mehr nicht.
const NAME_RE = /^[a-z0-9][a-z0-9-]*$/;
const PATH_RE = /^\/mcp\/([a-z0-9][a-z0-9-]*)$/;

function readJsonBody(req) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    req.on("data", (chunk) => {
      size += chunk.length;
      if (size > MAX_BODY_BYTES) {
        reject(new Error("body too large"));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on("end", () => {
      const raw = Buffer.concat(chunks).toString("utf8");
      if (!raw) return resolve(undefined);
      try {
        resolve(JSON.parse(raw));
      } catch (e) {
        reject(e);
      }
    });
    req.on("error", reject);
  });
}

/**
 * Startet `buildServer` auf dem passenden Transport.
 *
 * @param {string} name         Servername, bildet den HTTP-Pfad `/mcp/<name>`.
 * @param {() => import("@modelcontextprotocol/sdk/server/index.js").Server} buildServer
 */
export async function startServer(name, buildServer) {
  // Der Sammelstart (_all.mjs) importiert die Serverdateien, um an ihre
  // Fabriken zu kommen — dabei laeuft ihre letzte Zeile mit. Ohne diese Sperre
  // wollte jede Datei ihren eigenen HTTP-Dienst auf demselben Port oeffnen und
  // alle bis auf die erste scheiterten mit EADDRINUSE.
  if (process.env.MCP_COMBINED === "1") return;

  const port = Number(process.env.MCP_HTTP_PORT || 0);
  if (!port) {
    await buildServer().connect(new StdioServerTransport());
    return;
  }
  // Einzeln gestartet mit Port: ein Prozess, ein Server. Das ist der Weg fuer
  // einen einzelnen Server im HTTP-Modus; den gemeinsamen Prozess baut _all.mjs.
  await serveHttp(port, { [name]: buildServer });
}

/**
 * Bedient mehrere Server in EINEM Prozess, je unter `/mcp/<name>`.
 *
 * @param {number} port
 * @param {Record<string, () => import("@modelcontextprotocol/sdk/server/index.js").Server>} factories
 */
export async function serveHttp(port, factories) {
  /** @type {Map<string, StreamableHTTPServerTransport>} */
  const sessions = new Map();

  // Der Servername kommt gleich aus der Anfrage-URL. Damit aus dem Pfad nie etwas
  // anderes werden kann als genau einer der hier angemeldeten Namen, wird er beim
  // Start gegen dieselbe enge Form geprueft wie spaeter die Anfrage.
  const known = new Map();
  for (const [name, factory] of Object.entries(factories)) {
    if (!NAME_RE.test(name)) throw new Error(`ungueltiger Servername: ${name}`);
    if (typeof factory !== "function") throw new Error(`${name}: Fabrik erwartet`);
    known.set(name, factory);
  }

  const httpServer = http.createServer(async (req, res) => {
    const path = (req.url || "").split("?")[0].replace(/\/+$/, "") || "/";

    if (path === "/" || path === "/health") {
      res.writeHead(200, { "content-type": "application/json" });
      res.end(JSON.stringify({ ok: true, servers: Object.keys(factories) }));
      return;
    }

    // Erst die Form pruefen, dann nachschlagen, dann den Typ pruefen. Der Name aus
    // der URL waehlt damit ausschliesslich aus den beim Start angemeldeten Fabriken
    // aus; alles andere endet hier und nicht in einem Aufruf.
    const match = PATH_RE.exec(path);
    const factory = match ? known.get(match[1]) : undefined;
    if (typeof factory !== "function") {
      res.writeHead(404, { "content-type": "application/json" });
      res.end(JSON.stringify({ error: "unknown server" }));
      return;
    }
    const name = match[1];

    const header = req.headers["mcp-session-id"];
    const sessionId = header ? String(header) : "";
    // Sitzungsschluessel enthaelt den Servernamen: dieselbe Sitzungs-Id darf nicht
    // versehentlich den Transport eines anderen Servers treffen.
    const key = `${name}#${sessionId}`;
    let transport = sessionId ? sessions.get(key) : undefined;

    if (!transport) {
      transport = new StreamableHTTPServerTransport({
        sessionIdGenerator: () => randomUUID(),
        onsessioninitialized: (sid) => sessions.set(`${name}#${sid}`, transport),
      });
      transport.onclose = () => {
        if (transport.sessionId) sessions.delete(`${name}#${transport.sessionId}`);
      };
      await factory().connect(transport);
    }

    let body;
    if (req.method === "POST") {
      try {
        body = await readJsonBody(req);
      } catch (e) {
        res.writeHead(400, { "content-type": "application/json" });
        res.end(JSON.stringify({ error: `invalid body: ${e.message}` }));
        return;
      }
    }

    try {
      await transport.handleRequest(req, res, body);
    } catch (e) {
      if (!res.headersSent) {
        res.writeHead(500, { "content-type": "application/json" });
        res.end(JSON.stringify({ error: `transport error: ${e.message}` }));
      }
    }
  });

  // Ausschliesslich Loopback: die Server tragen die Identitaet des Containers
  // (AGENT_ID/AGENT_TOKEN). Ueber Containergrenzen hinweg waere das Teilen
  // dieser Identitaet nicht zulaessig.
  await new Promise((resolve) => httpServer.listen(port, "127.0.0.1", resolve));
  console.error(
    `[mcp] http on 127.0.0.1:${port} — ${Object.keys(factories).map((n) => `/mcp/${n}`).join(" ")}`
  );
  return httpServer;
}

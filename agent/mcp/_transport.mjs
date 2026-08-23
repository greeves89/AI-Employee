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
  const port = Number(process.env.MCP_HTTP_PORT || 0);
  if (!port) {
    await buildServer().connect(new StdioServerTransport());
    return;
  }
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
  const paths = new Map(
    Object.entries(factories).map(([name, factory]) => [`/mcp/${name}`, factory])
  );

  const httpServer = http.createServer(async (req, res) => {
    const path = (req.url || "").split("?")[0].replace(/\/+$/, "") || "/";

    if (path === "/" || path === "/health") {
      res.writeHead(200, { "content-type": "application/json" });
      res.end(JSON.stringify({ ok: true, servers: Object.keys(factories) }));
      return;
    }

    const factory = paths.get(path);
    if (!factory) {
      res.writeHead(404, { "content-type": "application/json" });
      res.end(JSON.stringify({ error: "unknown server", path }));
      return;
    }

    const header = req.headers["mcp-session-id"];
    const sessionId = header ? String(header) : "";
    // Sitzungsschluessel enthaelt den Pfad: dieselbe Sitzungs-Id darf nicht
    // versehentlich den Transport eines anderen Servers treffen.
    const key = `${path}#${sessionId}`;
    let transport = sessionId ? sessions.get(key) : undefined;

    if (!transport) {
      transport = new StreamableHTTPServerTransport({
        sessionIdGenerator: () => randomUUID(),
        onsessioninitialized: (sid) => sessions.set(`${path}#${sid}`, transport),
      });
      transport.onclose = () => {
        if (transport.sessionId) sessions.delete(`${path}#${transport.sessionId}`);
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

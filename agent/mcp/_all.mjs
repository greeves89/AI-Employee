/**
 * Alle eingebauten MCP-Server in EINEM Prozess (Issue #638, Phase 3).
 *
 * Bis hierher startete der Agent je Lauf elf einzelne node-Prozesse ueber stdio.
 * Gemessen wurden dabei 81 Threads und rund 691 MB — das 2,4-fache des
 * eigentlichen Modell-Prozesses. Auf einem Host mit knappem Speicher war das
 * der Grund, warum Laeufe abbrachen (#653), und bei den Prozessen der Grund
 * fuer die Deckelung auf vier gleichzeitige Laeufe (#628).
 *
 * Hier laufen sie stattdessen zusammen unter `/mcp/<name>`. Jede Sitzung
 * bekommt ueber die Fabriken eine eigene Server-Instanz — ein `Server` laesst
 * sich nur an genau einen Transport binden.
 *
 * Der alte Weg bleibt vollstaendig erhalten: Ohne `MCP_HTTP_PORT` startet jede
 * Serverdatei weiterhin einzeln ueber stdio, genau wie bisher. Diese Datei wird
 * dann gar nicht benutzt.
 */

// MUSS vor den Server-Importen stehen: sie rufen am Ende `startServer` auf,
// und ohne diese Marke oeffnete jede einzelne ihren eigenen Dienst auf
// demselben Port. Die Importe unten sind bewusst dynamisch (`await import`),
// damit diese Zuweisung wirklich vorher passiert — statische Importe werden
// vorgezogen.
process.env.MCP_COMBINED = "1";

import { serveHttp } from "./_transport.mjs";

const port = Number(process.env.MCP_HTTP_PORT || 0);
if (!port) {
  console.error("[mcp] MCP_HTTP_PORT fehlt — nichts zu tun");
  process.exit(1);
}

// Die Fabriken werden einzeln geladen, damit ein kaputter oder abgeschalteter
// Server nicht alle uebrigen mitreisst. Faellt einer aus, fehlt genau sein Pfad
// — der Agent verliert ein Werkzeug, nicht seine ganze Ausstattung.
const kandidaten = [
  ["bash-approval", "./bash-approval-server.mjs"],
  ["memory", "./memory-server.mjs"],
  ["notifications", "./notification-server.mjs"],
  ["orchestrator", "./orchestrator-server.mjs"],
  ["skills", "./skill-server.mjs"],
  ["desktop", "./computer-use-server.mjs"],
  ["hyperframes", "./hyperframes-server.mjs"],
  ["email", "./email-server.mjs"],
  ["brain", "./brain-server.mjs"],
  ["read-logs", "./read-logs-server.mjs"],
];

// Nur wenn der Orchestrator die Microsoft-Anbindung eingerichtet hat.
if ((process.env.MSGRAPH_ENABLED || "").toLowerCase() === "true") {
  kandidaten.push(["msgraph", "./msgraph-server.mjs"]);
}

const factories = {};
const fehlend = [];
for (const [name, pfad] of kandidaten) {
  try {
    // Die Serverdateien rufen am Ende `startServer` auf. Ohne gesetzten Port
    // waere das ein stdio-Start mitten im Import — deshalb ist MCP_HTTP_PORT
    // hier immer gesetzt, und `startServer` gibt die Fabrik nur weiter.
    const modul = await import(pfad);
    if (typeof modul.buildServer !== "function") {
      fehlend.push(`${name} (keine Fabrik)`);
      continue;
    }
    factories[name] = modul.buildServer;
  } catch (e) {
    fehlend.push(`${name} (${e.message?.slice(0, 80)})`);
  }
}

if (fehlend.length) {
  console.error(`[mcp] nicht geladen: ${fehlend.join(", ")}`);
}
if (!Object.keys(factories).length) {
  console.error("[mcp] kein einziger Server geladen — Abbruch");
  process.exit(1);
}

await serveHttp(port, factories);
console.error(
  `[mcp] ${Object.keys(factories).length} Server in einem Prozess: ` +
  Object.keys(factories).join(", ")
);

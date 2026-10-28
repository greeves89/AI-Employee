import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import nodePath from "node:path";

// `_transport.mjs::startServer()` tut unter MCP_COMBINED=1 nichts — sonst
// wuerde schon der Import eine echte stdio-Verbindung aufbauen wollen.
process.env.MCP_COMBINED = "1";
process.env.ORCHESTRATOR_URL = "http://127.0.0.1:1";
process.env.AGENT_ID = "test-agent";
process.env.AGENT_TOKEN = "test-token";

const { vaultPfad, vaultVerzeichnisse, dateienVon, leseVaultDatei, buildServer } =
  await import("./secondbrain-server.mjs");

function baueVault() {
  const wurzel = fs.mkdtempSync(nodePath.join(os.tmpdir(), "sb-"));
  fs.mkdirSync(nodePath.join(wurzel, "it/Drucker"), { recursive: true });
  fs.writeFileSync(nodePath.join(wurzel, "it/Drucker/x17137.md"), "# x17137\n\nSymptom\n");
  fs.writeFileSync(nodePath.join(wurzel, "it/index.md"), "# Index\n");
  fs.mkdirSync(nodePath.join(wurzel, "finanzen"), { recursive: true });
  fs.writeFileSync(nodePath.join(wurzel, "finanzen/geheim.md"), "nicht fuer diesen Agenten\n");
  return wurzel;
}

test("der Pfad-Kaefig weist jedes Ausbrechen ab", () => {
  const wurzel = baueVault();
  // Aufwaerts-Segmente in jeder Schreibweise. Aufgeloest geprueft, nicht als
  // Zeichenkette — eine reine Prefix-Pruefung faellt auf `..` herein.
  for (const boese of ["../etc/passwd", "it/../../etc/passwd",
                       "it/a/../../../etc/passwd", "it/./../../etc/hosts",
                       ".."]) {
    assert.throws(() => vaultPfad(boese, wurzel), /escapes/,
                  `nicht abgewiesen: ${boese}`);
  }
});

test("die Wurzel selbst ist erlaubt — und wird beim Lesen abgewiesen", () => {
  // `it/..` loest sich auf die Wurzel auf. Der Kaefig laesst sie durch (genau
  // wie `_resolve_brain_path` im Python-Executor: verboten ist nur, was
  // AUSSERHALB liegt), und das ist kein Loch — die Wurzel ist ein Verzeichnis,
  // und daran scheitert das Lesen. Beide Haelften gehoeren zusammen geprueft,
  // sonst verschiebt eine spaetere Aenderung die Zustaendigkeit lautlos.
  const wurzel = baueVault();
  const root = fs.realpathSync(wurzel);
  assert.equal(vaultPfad("it/..", wurzel), root);
  assert.throws(() => leseVaultDatei(root, "it/.."), /File not found/);
});

test("ein absoluter Pfad wird EINGESPERRT, nicht befolgt", () => {
  const wurzel = baueVault();
  const root = fs.realpathSync(wurzel);
  // Wie im Python-Executor (`_resolve_brain_path`): der fuehrende Schraegstrich
  // faellt weg, der Pfad wird vault-relativ gelesen. Entscheidend ist NICHT,
  // dass das einen Fehler gibt, sondern dass das Ergebnis im Kaefig bleibt —
  // ein `/etc/passwd` darf niemals auf das echte /etc/passwd zeigen.
  for (const absolut of ["/etc/passwd", "/root/.ssh/id_rsa", "/mnt/brains/it/index.md"]) {
    const ziel = vaultPfad(absolut, wurzel);
    assert.ok(ziel === root || ziel.startsWith(root + nodePath.sep),
              `bricht aus: ${absolut} -> ${ziel}`);
    assert.ok(!ziel.startsWith("/etc/") && !ziel.startsWith("/root/"),
              `zeigt nach draussen: ${absolut} -> ${ziel}`);
  }
});

test("ein gueltiger Pfad loest sich auf", () => {
  const wurzel = baueVault();
  const ziel = vaultPfad("it/Drucker/x17137.md", wurzel);
  assert.equal(ziel, nodePath.join(fs.realpathSync(wurzel), "it/Drucker/x17137.md"));
});

test("ein leerer Pfad ist ein Fehler, keine Wurzel", () => {
  const wurzel = baueVault();
  assert.throws(() => vaultPfad("   ", wurzel), /required/);
});

test("Lesen liefert den Inhalt", () => {
  const wurzel = baueVault();
  const text = leseVaultDatei(vaultPfad("it/index.md", wurzel));
  assert.equal(text, "# Index\n");
});

test("eine fehlende Datei meldet sich als fehlend, nicht als Absturz", () => {
  const wurzel = baueVault();
  assert.throws(() => leseVaultDatei(nodePath.join(wurzel, "it/gibtsnicht.md"), "it/gibtsnicht.md"),
                /File not found/);
});

test("ein Verzeichnis ist keine Datei", () => {
  const wurzel = baueVault();
  assert.throws(() => leseVaultDatei(nodePath.join(wurzel, "it"), "it"), /File not found/);
});

test("die Groessengrenze greift an der GEOEFFNETEN Kennung", () => {
  const wurzel = baueVault();
  const gross = nodePath.join(wurzel, "it/gross.md");
  fs.writeFileSync(gross, "x".repeat(5000));
  assert.throws(() => leseVaultDatei(gross, "it/gross.md", 1000), /too large/);
  // knapp darunter geht durch — sonst wuerde der Test auch bei einem
  // versehentlich auf 0 gesetzten Deckel gruen bleiben
  assert.equal(leseVaultDatei(gross, "it/gross.md", 5000).length, 5000);
});

test("Auflisten zeigt die Vaults und ihre Dateien", () => {
  const wurzel = baueVault();
  const dirs = vaultVerzeichnisse(wurzel);
  assert.deepEqual(dirs.map((d) => nodePath.basename(d)), ["finanzen", "it"]);
  assert.deepEqual(dateienVon(nodePath.join(wurzel, "it")),
                   ["Drucker/x17137.md", "index.md"]);
});

test(".git bleibt aus der Dateiliste heraus", () => {
  const wurzel = baueVault();
  fs.mkdirSync(nodePath.join(wurzel, "it/.git"), { recursive: true });
  fs.writeFileSync(nodePath.join(wurzel, "it/.git/config"), "x");
  assert.ok(!dateienVon(nodePath.join(wurzel, "it")).some((f) => f.includes(".git")));
});

test("der Server meldet genau die vier Werkzeuge an", async () => {
  // Gegen die Klasse 'Prompt nennt ein Werkzeug, das die Laufzeit nicht hat':
  // diese vier Namen stehen im Werkzeugkatalog des Orchestrators.
  const server = buildServer();
  assert.ok(server, "buildServer liefert keinen Server");
  const quelle = fs.readFileSync(new URL("./secondbrain-server.mjs", import.meta.url), "utf8");
  for (const name of ["secondbrain_search", "secondbrain_write",
                      "secondbrain_read", "secondbrain_list"]) {
    assert.ok(quelle.includes(`name: "${name}"`), `Werkzeug fehlt: ${name}`);
  }
});

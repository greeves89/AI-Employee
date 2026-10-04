import { test } from "node:test";
import assert from "node:assert/strict";
import { pdfAblehnung } from "./_dokument_pruefung.mjs";

const MELDUNG = "Dieses PDF trägt die Kopf-/Fußzeile eines Browsers … `dokument pdf …`";

function exitEins(stdout) {
  return async () => {
    const fehler = new Error("Command failed");
    fehler.status = 1;
    fehler.stdout = stdout;
    throw fehler;
  };
}

test("ein PDF mit Browser-Rand wird mit der Meldung des Werkzeugs abgewiesen", async () => {
  const ausgabe = JSON.stringify({ ok: false, fehler: [{ art: "browser_rand" }], anzeige_meldung: MELDUNG });
  assert.equal(await pdfAblehnung("/workspace/transfer/a.pdf", exitEins(ausgabe)), MELDUNG);
});

test("ein sauberes PDF geht durch", async () => {
  const ausgabe = JSON.stringify({ ok: true, fehler: [], anzeige_meldung: null });
  assert.equal(await pdfAblehnung("/workspace/transfer/a.pdf", async () => ({ stdout: ausgabe })), null);
});

test("Platzhalter allein blockieren die Anzeige nicht", async () => {
  const ausgabe = JSON.stringify({ ok: false, fehler: [{ art: "platzhalter" }], anzeige_meldung: null });
  assert.equal(await pdfAblehnung("/workspace/transfer/a.pdf", exitEins(ausgabe)), null);
});

test("andere Dateien werden gar nicht erst geprüft", async () => {
  let aufgerufen = false;
  await pdfAblehnung("/workspace/transfer/a.docx", async () => { aufgerufen = true; return { stdout: "{}" }; });
  assert.equal(aufgerufen, false);
});

test("ohne Werkzeug (älteres Abbild) wird nicht abgewiesen", async () => {
  const fehlt = async () => {
    const fehler = new Error("spawn dokument ENOENT");
    fehler.code = "ENOENT";
    throw fehler;
  };
  assert.equal(await pdfAblehnung("/workspace/transfer/a.pdf", fehlt), null);
});

test("das Werkzeug bekommt den Pfad und verlangt JSON", async () => {
  let argumente;
  await pdfAblehnung("/workspace/transfer/a.pdf", async (befehl, args) => {
    argumente = [befehl, ...args];
    return { stdout: JSON.stringify({ ok: true, fehler: [], anzeige_meldung: null }) };
  });
  assert.deepEqual(argumente, ["dokument", "pruefen", "--json", "/workspace/transfer/a.pdf"]);
});

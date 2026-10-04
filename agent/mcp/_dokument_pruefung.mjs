// Prüfung eines PDFs vor present_file (#893).
//
// Ein PDF mit der Kopf-/Fußzeile eines Browsers (US-Datum, file://-Pfad) ist
// nicht versandfertig. Die eigentliche Prüfung steckt in `dokument pruefen`
// (agent/app/dokument.py) — der Executor der anderen Laufzeiten ruft dieselbe
// Funktion direkt. Hier wird sie nur aufgerufen, nicht nachgebaut.

import { execFile } from "node:child_process";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

/**
 * Meldung, mit der present_file ein PDF abweist — oder null, wenn es passt.
 *
 * Asynchron, damit die Prüfung im gemeinsamen MCP-Prozess keine anderen
 * Werkzeuge blockiert. Fehlt das Werkzeug (älteres Abbild) oder scheitert die
 * Prüfung selbst, wird NICHT abgewiesen: lieber ein unschönes PDF zeigen als
 * gar keins.
 */
export async function pdfAblehnung(pfad, ausfuehren = execFileAsync) {
  if (!String(pfad).toLowerCase().endsWith(".pdf")) return null;
  let ausgabe = "";
  try {
    const ergebnis = await ausfuehren("dokument", ["pruefen", "--json", pfad], {
      encoding: "utf8",
      timeout: 30000,
    });
    ausgabe = typeof ergebnis === "string" ? ergebnis : ergebnis?.stdout;
  } catch (fehler) {
    // Exit-Code 1 heißt „nicht versandfertig“ — die Antwort steht trotzdem in stdout.
    if (!fehler || fehler.code === "ENOENT" || !fehler.stdout) return null;
    ausgabe = fehler.stdout;
  }
  try {
    const ergebnis = JSON.parse(String(ausgabe));
    return ergebnis && ergebnis.anzeige_meldung ? String(ergebnis.anzeige_meldung) : null;
  } catch {
    return null;
  }
}

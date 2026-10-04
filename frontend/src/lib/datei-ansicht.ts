/** Wie der Datei-Explorer den Arbeitsordner eines Agenten zeigt (#907).
 *
 * Gilt für beide Bäume: den des einzelnen Agenten (Schnellzugriff „Dateien")
 * und den agentenübergreifenden unter /files.
 *
 * - Die Steuerdateien des Agenten (Anleitung, Gedächtnis, Werkzeug-Einstellungen)
 *   liegen oben im Arbeitsordner und standen für Mitglieder ganz vorn — vor dem,
 *   was der Agent tatsächlich abgeliefert hat. In der einfachen Ansicht fallen sie
 *   weg; Admins sehen sie weiter.
 * - Abgelieferte Ergebnisse legt der Agent in ``/workspace/transfer`` ab. Der
 *   Ordner hieß im Baum auch so und stand alphabetisch irgendwo in der Mitte.
 *   Er heißt jetzt „Ergebnisse" und steht oben.
 */
import type { FileEntry } from "./types";

const WURZEL = "/workspace";

/** Steuerdateien des Agenten — nur direkt im Arbeitsordner, nicht in Projekten. */
const SYSTEM_NAMEN = new Set([".claude", ".mcp.json", "CLAUDE.md", "AGENT.md", "knowledge.md"]);

/** Wo der Agent Ergebnisse für den Nutzer ablegt. */
export const ERGEBNIS_ORDNER = `${WURZEL}/transfer`;

export function istSystemDatei(entry: FileEntry): boolean {
  return SYSTEM_NAMEN.has(entry.name) && entry.path === `${WURZEL}/${entry.name}`;
}

export function istErgebnisOrdner(entry: FileEntry): boolean {
  return entry.type === "directory" && entry.path === ERGEBNIS_ORDNER;
}

/** Anzeigename im Baum — der Pfad selbst bleibt unverändert. */
export function dateiAnzeigeName(entry: FileEntry): string {
  return istErgebnisOrdner(entry) ? "Ergebnisse" : entry.name;
}

/** Bereits sortierte Einträge für die Anzeige aufbereiten: Ergebnisse nach
 *  oben, Steuerdateien in der einfachen Ansicht weg. */
export function fuerExplorer(sortiert: FileEntry[], einfacheAnsicht: boolean): FileEntry[] {
  const sichtbar = einfacheAnsicht ? sortiert.filter((e) => !istSystemDatei(e)) : sortiert;
  const ergebnisse = sichtbar.filter(istErgebnisOrdner);
  if (ergebnisse.length === 0) return sichtbar;
  return [...ergebnisse, ...sichtbar.filter((e) => !istErgebnisOrdner(e))];
}

import type { Schedule } from "@/lib/types";

/** Status einer Aufgabe, wie ihn Nutzer lesen — die API liefert englische Schlüssel. */
export const AUFGABEN_STATUS: Record<string, string> = {
  pending: "Wartet",
  queued: "In der Warteschlange",
  running: "Läuft",
  completed: "Erledigt",
  failed: "Fehlgeschlagen",
  cancelled: "Abgebrochen",
};

/** Zeitpläne, die die Plattform selbst je Agent anlegt. Ihr Auftrag ist ein
 *  langer, englischer Systemprompt — für Mitglieder steht hier stattdessen ein
 *  Satz, was der Zeitplan tut. Die Präfixe kommen aus dem Orchestrator
 *  (agent_manager: "[Proactive] ", plan_rhythm: "[Rhythmus] "). */
const SYSTEM_ZEITPLAENE: { praefix: string; titel: string; zweck: string }[] = [
  { praefix: "[Proactive] ", titel: "Eigeninitiative", zweck: "Der Agent schaut regelmäßig selbst, ob es für dich etwas zu tun gibt." },
  { praefix: "[Rhythmus] Abendplanung", titel: "Abendplanung", zweck: "Der Agent plant am Abend den nächsten Arbeitstag." },
  { praefix: "[Rhythmus] Morgencheck", titel: "Morgencheck", zweck: "Der Agent prüft morgens den Plan für heute." },
];

/** Titel und Beschreibung eines Zeitplans. In der vollen Ansicht unverändert;
 *  in der Mitglieder-Ansicht ohne Systemprompt. */
export function zeitplanAnzeige(schedule: Schedule, einfach: boolean): { titel: string; beschreibung: string } {
  if (einfach) {
    const system = SYSTEM_ZEITPLAENE.find((z) => schedule.name.startsWith(z.praefix));
    if (system) return { titel: system.titel, beschreibung: system.zweck };
  }
  return { titel: schedule.name, beschreibung: schedule.prompt };
}

/** Die eine Liste der Rollennamen (#902).
 *
 *  Vorher hatte jede Ansicht ihre eigene Übersetzung — oder keine: in der
 *  Nutzerverwaltung stand „Member“ und „Viewer“, im Benutzermenü „Mitglied“ und
 *  „Betrachter“. Schlüssel wie ``UserRole`` im Server (``models/user.py``);
 *  ``tests/test_deutsche_anzeigenamen.py`` wacht darüber, dass jede Rolle hier
 *  einen Namen hat.
 */
import type { UserRole } from "@/lib/types";

export const ROLLEN_NAMEN: Record<UserRole, string> = {
  admin: "Administrator",
  manager: "Leitung",
  member: "Mitglied",
  viewer: "Betrachter",
  unassigned: "Ohne Rolle",
};

/** Anzeigename einer Rolle; Unbekanntes bleibt sichtbar statt zu verschwinden. */
export function rollenName(rolle: string | null | undefined): string {
  if (!rolle) return ROLLEN_NAMEN.unassigned;
  return ROLLEN_NAMEN[rolle as UserRole] ?? rolle;
}

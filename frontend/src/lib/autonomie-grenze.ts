"use client";

/** Autonomie-Grenze der eigenen Rolle (#910) — für die Oberfläche.
 *
 * Der Server prüft die Grenze auf jedem Weg selbst (403). Hier geht es nur
 * darum, nicht anzubieten, was er ablehnen würde: Stufen über der Grenze
 * ausgrauen, den Sudo/Root-Bereich nur ohne Grenze zeigen. Der Wert kommt so,
 * wie der Server ihn liest (`/roles/me/permissions`, Unbekanntes = L1).
 */

import { useEffect, useState } from "react";
import * as api from "@/lib/api";

export const STUFEN = ["l1", "l2", "l3", "l4"] as const;

export interface AutonomieGrenze {
  /** `undefined` = wird geladen, `null` = unbegrenzt, sonst die höchste erlaubte Stufe. */
  grenze: string | null | undefined;
  /** Liegt diese Stufe über der Grenze? Während des Ladens: nein (der Server prüft ohnehin). */
  ueber: (stufe: string) => boolean;
  /** Darf der Nutzer Sudo-Pakete und Root selbst setzen? Erst nach dem Laden. */
  ohneGrenze: boolean;
}

export function stufeUeber(stufe: string, grenze: string | null | undefined): boolean {
  if (!grenze) return false;
  const i = STUFEN.indexOf(stufe as (typeof STUFEN)[number]);
  return i >= 0 && i > STUFEN.indexOf(grenze as (typeof STUFEN)[number]);
}

export function useAutonomieGrenze(): AutonomieGrenze {
  const [grenze, setGrenze] = useState<string | null | undefined>(undefined);

  useEffect(() => {
    let aktiv = true;
    api.getMyPermissions()
      .then((r) => { if (aktiv) setGrenze(r.permissions.max_autonomy_level ?? null); })
      // Ohne Antwort lieber eng: L1 ausgrauen ist harmlos, der Server entscheidet.
      .catch(() => { if (aktiv) setGrenze("l1"); });
    return () => { aktiv = false; };
  }, []);

  return {
    grenze,
    ueber: (stufe: string) => stufeUeber(stufe, grenze),
    ohneGrenze: grenze === null,
  };
}

"use client";

import { useEffect } from "react";
import { usePathname } from "next/navigation";
import { zaehleAufruf } from "@/lib/api";
import { useAuthStore } from "@/lib/auth";

/** Nutzungsmessung (#1047), Seite der Oberfläche.
 *
 * Gezählt wird beim Wechsel in einen anderen Bereich — nicht bei jedem Reiter
 * oder Unterpfad. Der Server macht aus dem Pfad selbst den Bereich und verwirft
 * Unsinn; hier geht es nur darum, nicht doppelt zu zählen.
 *
 * Die Zahlen nutzt nur die Auswertung der Verwaltung. Die Seitenleiste holt
 * seit 1.393.6 nichts mehr nach Häufigkeit nach oben — sie bleibt schlank.
 */

/** Wie der Server: erster Pfadteil, bei angelegten Seiten „/p/<kurzname>“. */
export function bereichAusPfad(pfad: string): string {
  const teile = pfad.split(/[?#]/)[0].split("/").filter(Boolean);
  if (teile[0] === "p" && teile[1]) return `/p/${teile[1]}`.toLowerCase();
  return `/${teile[0] ?? ""}`.toLowerCase();
}

let zuletzt: string | null = null;

/** Einmal in der App-Hülle: zählt jeden Wechsel in einen anderen Bereich. */
export function useBereichMessung() {
  const pathname = usePathname();
  const uid = useAuthStore((s) => s.user?.id);

  useEffect(() => {
    if (!uid || !pathname) return;
    const bereich = bereichAusPfad(pathname);
    if (bereich === "/" || bereich === zuletzt) return;
    zuletzt = bereich;
    zaehleAufruf(pathname).catch(() => {
      // Messung ist Beiwerk — ein Fehler darf nirgends auffallen
    });
  }, [uid, pathname]);
}

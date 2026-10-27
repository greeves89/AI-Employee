"use client";

import { useEffect } from "react";
import { usePathname } from "next/navigation";
import { create } from "zustand";
import { getMeineNutzung, zaehleAufruf } from "@/lib/api";
import { useAuthStore } from "@/lib/auth";

/** Nutzungsmessung (#1047), Seite der Oberfläche.
 *
 * Gezählt wird beim Wechsel in einen anderen Bereich — nicht bei jedem Reiter
 * oder Unterpfad. Der Server macht aus dem Pfad selbst den Bereich und verwirft
 * Unsinn; hier geht es nur darum, nicht doppelt zu zählen.
 *
 * Die eigenen Zahlen braucht die Seitenleiste: was jemand unter „Weitere
 * Bereiche“ oft öffnet, steht bei ihm wieder oben.
 */

/** Wie der Server: erster Pfadteil, bei angelegten Seiten „/p/<kurzname>“. */
export function bereichAusPfad(pfad: string): string {
  const teile = pfad.split(/[?#]/)[0].split("/").filter(Boolean);
  if (teile[0] === "p" && teile[1]) return `/p/${teile[1]}`.toLowerCase();
  return `/${teile[0] ?? ""}`.toLowerCase();
}

interface NutzungState {
  bereiche: Record<string, number>;
  oftAb: number;
  geladenFuer: string | null;
  laden: (uid: string) => void;
  gezaehlt: (bereich: string) => void;
}

export const useNutzung = create<NutzungState>((set, get) => ({
  bereiche: {},
  oftAb: 3,
  geladenFuer: null,
  laden: (uid) => {
    if (get().geladenFuer === uid) return;
    set({ geladenFuer: uid, bereiche: {} });
    getMeineNutzung()
      .then((n) => set({ bereiche: n.bereiche, oftAb: n.oft_ab }))
      .catch(() => {
        // ohne Zahlen bleibt das Menü eben in seiner Grundform
      });
  },
  gezaehlt: (bereich) =>
    set((s) => ({ bereiche: { ...s.bereiche, [bereich]: (s.bereiche[bereich] ?? 0) + 1 } })),
}));

/** Oft genug geöffnet, um im eigenen Menü oben zu stehen? */
export function useOftGenutzt(): (href: string) => boolean {
  const bereiche = useNutzung((s) => s.bereiche);
  const oftAb = useNutzung((s) => s.oftAb);
  return (href) => (bereiche[bereichAusPfad(href)] ?? 0) >= oftAb;
}

let zuletzt: string | null = null;

/** Einmal in der App-Hülle: zählt jeden Wechsel in einen anderen Bereich. */
export function useBereichMessung() {
  const pathname = usePathname();
  const uid = useAuthStore((s) => s.user?.id);
  const laden = useNutzung((s) => s.laden);
  const gezaehlt = useNutzung((s) => s.gezaehlt);

  useEffect(() => {
    if (uid) laden(uid);
  }, [uid, laden]);

  useEffect(() => {
    if (!uid || !pathname) return;
    const bereich = bereichAusPfad(pathname);
    if (bereich === "/" || bereich === zuletzt) return;
    zuletzt = bereich;
    gezaehlt(bereich);
    zaehleAufruf(pathname).catch(() => {
      // Messung ist Beiwerk — ein Fehler darf nirgends auffallen
    });
  }, [uid, pathname, gezaehlt]);
}

/** Sitzordnung im Büro der Agenten-Seite.
 *
 *  Der Platz kommt aus ``config.buero`` ({bereich, platz}) — vergeben beim
 *  Anlegen, zufällig unter den freien Plätzen (orchestrator: core/buero.py).
 *  Agenten ohne Platz (älter als die Funktion) oder mit einem schon belegten
 *  Platz (Administratoren sehen die Agenten mehrerer Besitzer) bekommen einen
 *  festen, aus der Kennung abgeleiteten freien Platz — so springen sie nicht
 *  bei jedem Laden woanders hin.
 */

import type { Agent } from "@/lib/types";

// Dieselben Zahlen stehen im Orchestrator (core/buero.py).
export const PLAETZE_JE_BEREICH = 6;
export const MIN_BEREICHE = 4;

const BEREICH_NAMEN = ["Werkstatt", "Bibliothek", "Lounge", "Fensterfront"];
const BEREICH_FARBEN = ["#3b82f6", "#8b5cf6", "#f97316", "#14b8a6", "#22c55e", "#eab308", "#ef4444", "#94a3b8"];

export function bereichName(i: number): string {
  return BEREICH_NAMEN[i] ?? `Anbau ${i - BEREICH_NAMEN.length + 1}`;
}

export function bereichFarbe(i: number): string {
  return BEREICH_FARBEN[i % BEREICH_FARBEN.length];
}

export interface Sitz {
  bereich: number;
  platz: number;
}

function gespeichert(agent: Agent): Sitz | null {
  const b = (agent.config as Record<string, unknown> | null)?.buero as Partial<Sitz> | undefined;
  if (!b || !Number.isInteger(b.bereich) || !Number.isInteger(b.platz)) return null;
  if ((b.bereich as number) < 0 || (b.platz as number) < 0 || (b.platz as number) >= PLAETZE_JE_BEREICH) return null;
  return { bereich: b.bereich as number, platz: b.platz as number };
}

/** FNV-1a — ein fester Ausgangspunkt je Kennung. */
function streuwert(text: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}

export function sitzordnung(agents: Agent[]): { sitze: Map<string, Sitz>; bereiche: number } {
  const sitze = new Map<string, Sitz>();
  const belegt = new Set<string>();
  const schluessel = (s: Sitz) => `${s.bereich}:${s.platz}`;

  // Ältere Agenten behalten ihren Platz, wenn zwei denselben haben.
  const nachAlter = [...agents].sort((a, b) => (a.created_at ?? "").localeCompare(b.created_at ?? "") || a.id.localeCompare(b.id));
  const ohne: Agent[] = [];
  for (const a of nachAlter) {
    const s = gespeichert(a);
    if (s && !belegt.has(schluessel(s))) {
      sitze.set(a.id, s);
      belegt.add(schluessel(s));
    } else {
      ohne.push(a);
    }
  }

  const hoechster = Math.max(-1, ...[...sitze.values()].map((s) => s.bereich));
  let bereiche = Math.max(MIN_BEREICHE, hoechster + 1, Math.ceil(agents.length / PLAETZE_JE_BEREICH));
  for (const a of ohne.sort((x, y) => x.id.localeCompare(y.id))) {
    let frei = bereiche * PLAETZE_JE_BEREICH - belegt.size;
    if (frei <= 0) {
      bereiche += 1;
      frei = PLAETZE_JE_BEREICH;
    }
    const plaetze = bereiche * PLAETZE_JE_BEREICH;
    for (let i = 0, start = streuwert(a.id) % plaetze; i < plaetze; i++) {
      const n = (start + i) % plaetze;
      const s = { bereich: Math.floor(n / PLAETZE_JE_BEREICH), platz: n % PLAETZE_JE_BEREICH };
      if (!belegt.has(schluessel(s))) {
        sitze.set(a.id, s);
        belegt.add(schluessel(s));
        break;
      }
    }
  }
  return { sitze, bereiche };
}

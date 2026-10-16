/** Projekte: ein Projekt ist ein Team mit Board und Raum (orchestrator: core/projekte.py). */

export type ProjektSpalte = "wartet" | "in_arbeit" | "zur_abnahme" | "erledigt";

export interface ProjektKarte {
  id: string;
  titel: string;
  status: string;
  spalte: ProjektSpalte;
  agent_id: string | null;
  /** Über das Projekt erteilt: das Ergebnis braucht eine Abnahme. */
  abnahme: boolean;
  /** Teilarbeit des Leads oder eines Kollegen — ohne eigene Abnahme. */
  teilauftrag: boolean;
  zurueckgegeben_als: string | null;
  erstellt: string | null;
  gestartet: string | null;
  fertig: string | null;
  abgenommen: string | null;
  kosten_usd: number | null;
  ergebnis: string | null;
  fehler: string | null;
}

export interface ProjektKennzahlen {
  wartet: number;
  in_arbeit: number;
  zur_abnahme: number;
  heute_fertig: number;
  fertig_7_tage: number;
  verlauf: { tag: string; anzahl: number }[];
}

export interface ProjektBoard {
  spalten: Record<ProjektSpalte, ProjektKarte[]>;
  kennzahlen: ProjektKennzahlen;
}

export const SPALTEN: { key: ProjektSpalte; label: string; leer: string }[] = [
  { key: "wartet", label: "Wartet", leer: "Nichts in der Warteschlange." },
  { key: "in_arbeit", label: "In Arbeit", leer: "Gerade arbeitet niemand." },
  { key: "zur_abnahme", label: "Zur Abnahme", leer: "Nichts abzunehmen." },
  { key: "erledigt", label: "Erledigt", leer: "Noch nichts erledigt." },
];

/** Kennfarben — dieselben Schlüssel wie PROJEKT_FARBEN im Orchestrator (api/teams.py). */
export const PROJEKT_FARBEN: Record<string, { name: string; hex: string }> = {
  blau: { name: "Blau", hex: "#3b82f6" },
  violett: { name: "Violett", hex: "#8b5cf6" },
  gruen: { name: "Grün", hex: "#22c55e" },
  orange: { name: "Orange", hex: "#f97316" },
  rot: { name: "Rot", hex: "#ef4444" },
  tuerkis: { name: "Türkis", hex: "#14b8a6" },
  gelb: { name: "Gelb", hex: "#eab308" },
  grau: { name: "Grau", hex: "#94a3b8" },
};

export function projektFarbe(farbe: string | null | undefined): string {
  return (farbe && PROJEKT_FARBEN[farbe]?.hex) || PROJEKT_FARBEN.blau.hex;
}

/** Wochentag-Kürzel für den Verlauf (Mo, Di …). */
export function wochentag(isoTag: string): string {
  const d = new Date(`${isoTag}T12:00:00`);
  return ["So", "Mo", "Di", "Mi", "Do", "Fr", "Sa"][d.getDay()];
}

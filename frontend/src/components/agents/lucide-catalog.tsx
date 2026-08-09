"use client";

import { icons, Cpu, type LucideIcon } from "lucide-react";

/**
 * Der vollständige lucide-Satz — bewusst in einer eigenen Datei (#523).
 *
 * Diese Datei zieht **alle** Sinnbilder in ein Bündel. Genau deshalb steht sie
 * allein: wer sie importiert, tut das über ``next/dynamic``, und das Bündel wird
 * erst geladen, wenn wirklich ein Sinnbild außerhalb des kuratierten Satzes auf dem
 * Bildschirm steht oder die Auswahl geöffnet wird. Ein direkter Import irgendwo
 * anders macht diese Trennung zunichte und schleppt den ganzen Satz in jede Seite.
 *
 * Der andere Weg — ``DynamicIcon`` von lucide — lädt jedes Sinnbild einzeln nach.
 * Das ergibt beim Bauen knapp zweitausend Bündel; auf dem Raspberry Pi, wo das
 * Frontend beim Ausrollen gebaut wird, ist das die falsche Rechnung. Ein Bündel,
 * das nachgeladen wird, ist hier günstiger als tausend, die es könnten.
 */
export const ALL_ICONS = icons as unknown as Record<string, LucideIcon>;

export const ALL_ICON_NAMES: string[] = Object.keys(ALL_ICONS).sort();

/** Ein Sinnbild über seinen Namen. Unbekannt → Standardsinnbild statt Absturz. */
export function CatalogIcon({
  name,
  className,
}: {
  name: string;
  className?: string;
}) {
  const Icon = ALL_ICONS[name] || Cpu;
  return <Icon className={className} />;
}

/** Namenssuche für die Auswahl: „mess" findet MessageSquare, MessagesSquare, … */
export function searchIcons(query: string, limit = 120): string[] {
  const q = query.trim().toLowerCase();
  if (!q) return ALL_ICON_NAMES.slice(0, limit);
  const starts: string[] = [];
  const contains: string[] = [];
  for (const name of ALL_ICON_NAMES) {
    const lower = name.toLowerCase();
    if (lower.startsWith(q)) starts.push(name);
    else if (lower.includes(q)) contains.push(name);
    if (starts.length >= limit) break;
  }
  // Treffer am Wortanfang zuerst — wer „bug" tippt, meint Bug, nicht Debug.
  return [...starts, ...contains].slice(0, limit);
}

"use client";

/** Statuszeile eines Projekts: Blockiert / Wartet auf dich / Arbeitet / Ruht.
 *
 * Der Text kommt vom Server (core/projekte.status) — eine Stelle für Projektliste und
 * Projektseite; hier wird nur gefärbt.
 */

import { CircleDot, Hand, Loader2, OctagonAlert } from "lucide-react";
import type { ProjektStatus } from "@/lib/projekte";
import { cn } from "@/lib/utils";

const DARSTELLUNG: Record<ProjektStatus["stufe"], { icon: typeof CircleDot; klasse: string }> = {
  blockiert: { icon: OctagonAlert, klasse: "border-red-500/30 bg-red-500/5 text-red-700 dark:text-red-400" },
  wartet: { icon: Hand, klasse: "border-amber-500/30 bg-amber-500/5 text-amber-700 dark:text-amber-400" },
  arbeitet: { icon: Loader2, klasse: "border-sky-500/30 bg-sky-500/5 text-sky-700 dark:text-sky-400" },
  ruht: { icon: CircleDot, klasse: "border-foreground/[0.08] bg-foreground/[0.02] text-muted-foreground" },
};

export function ProjektStatusZeile({ status, className }: { status?: ProjektStatus | null; className?: string }) {
  if (!status) return null;
  const d = DARSTELLUNG[status.stufe] ?? DARSTELLUNG.ruht;
  const Icon = d.icon;
  return (
    <p className={cn("inline-flex max-w-full items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px] font-medium", d.klasse, className)}
      role="status">
      <Icon className={cn("h-3 w-3 shrink-0", status.stufe === "arbeitet" && "animate-spin")} aria-hidden="true" />
      <span className="truncate">{status.text}</span>
    </p>
  );
}

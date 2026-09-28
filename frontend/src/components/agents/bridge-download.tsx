"use client";

import { Apple, Download, Monitor } from "lucide-react";
import { getApiUrl } from "@/lib/config";

/** Computer-Bridge für Mac und Windows herunterladen.
 *
 * Gemeinsam genutzt vom Computer-Use-Bereich und vom Konnektoren-Fenster im
 * Chat — dort findet ein Mitglied die Bridge, ohne den Admin-Reiter zu kennen.
 * Die Installer liefert der Server öffentlich aus (``/download/bridge/…``).
 */
export function BridgeDownload() {
  const basis = getApiUrl().replace(/\/$/, "");
  const knopf =
    "inline-flex items-center gap-2 rounded-lg bg-foreground/[0.06] border border-foreground/[0.08] px-3.5 py-2 text-xs font-medium text-foreground hover:bg-foreground/[0.1] transition-all";
  return (
    <div className="rounded-xl border border-foreground/[0.08] bg-foreground/[0.02] p-4">
      <div className="mb-3 flex items-center gap-3">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-foreground/[0.06]">
          <Download className="h-4 w-4 text-muted-foreground" />
        </div>
        <div>
          <p className="text-sm font-medium text-foreground">Computer-Bridge herunterladen</p>
          <p className="mt-0.5 text-xs text-muted-foreground">
            Damit dein Agent auf deinem Rechner arbeiten kann. Läuft in der Menüleiste bzw. im Infobereich.
          </p>
        </div>
      </div>
      <div className="flex flex-wrap gap-2">
        <a href={`${basis}/api/v1/download/bridge/mac`} className={knopf}>
          <Apple className="h-3.5 w-3.5" />
          macOS (.dmg)
        </a>
        <a href={`${basis}/api/v1/download/bridge/windows`} className={knopf}>
          <Monitor className="h-3.5 w-3.5" />
          Windows (.zip)
        </a>
      </div>
      <p className="mt-2.5 text-[10px] text-muted-foreground">
        Im Menü der Bridge siehst du jederzeit, was der Agent auf deinem Rechner darf.
      </p>
    </div>
  );
}

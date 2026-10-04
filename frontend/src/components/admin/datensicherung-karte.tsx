"use client";

/** Karte „Datensicherung“ unter Admin → Betrieb (#892).
 *
 * Zeigt, was scripts/backup.sh zuletzt gemeldet hat. Die Ampel kommt vom Server
 * (dieselbe Regel wie im Concierge): gelb nach 26 Stunden ohne Sicherung oder
 * wenn noch nie gesichert wurde, rot nach drei Tagen oder wenn der letzte Lauf
 * gescheitert ist.
 */

import { useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, DatabaseBackup, Loader2, XCircle } from "lucide-react";
import * as api from "@/lib/api";
import type { BackupStatus } from "@/lib/api";
import { cn } from "@/lib/utils";

function groesse(bytes: number | null): string {
  if (bytes === null || bytes === undefined) return "–";
  const einheiten = ["B", "KB", "MB", "GB", "TB"];
  let wert = bytes;
  let i = 0;
  while (wert >= 1024 && i < einheiten.length - 1) {
    wert /= 1024;
    i += 1;
  }
  return `${wert.toLocaleString("de-DE", { maximumFractionDigits: 1 })} ${einheiten[i]}`;
}

function zeitpunkt(iso: string | null): string {
  if (!iso) return "–";
  return new Date(iso).toLocaleString("de-DE", { dateStyle: "medium", timeStyle: "short" });
}

const AMPEL = {
  ok: { icon: CheckCircle2, farbe: "text-emerald-500", rahmen: "border-emerald-500/20" },
  gelb: { icon: AlertTriangle, farbe: "text-amber-600 dark:text-amber-400", rahmen: "border-amber-500/30" },
  rot: { icon: XCircle, farbe: "text-red-500", rahmen: "border-red-500/30" },
} as const;

export function DatensicherungKarte() {
  const [stand, setStand] = useState<BackupStatus | null>(null);
  const [fehler, setFehler] = useState<string | null>(null);

  useEffect(() => {
    api.getBackupStatus()
      .then(setStand)
      .catch((e) => setFehler(e instanceof Error ? e.message : "Unbekannter Fehler"));
  }, []);

  if (fehler) {
    return (
      <div className="mb-4 rounded-xl border border-red-500/20 bg-red-500/5 p-4 text-sm text-red-500">
        Stand der Datensicherung nicht abrufbar: {fehler}
      </div>
    );
  }
  if (!stand) {
    return (
      <div className="mb-4 flex items-center gap-2 rounded-xl border border-foreground/[0.06] bg-card/80 p-4 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" /> Datensicherung wird geladen …
      </div>
    );
  }

  const ampel = AMPEL[stand.ampel];
  const Icon = ampel.icon;

  return (
    <div className={cn("mb-4 rounded-xl border bg-card/80 p-4", ampel.rahmen)}>
      <div className="flex items-start gap-3">
        <DatabaseBackup className="mt-0.5 h-5 w-5 shrink-0 text-muted-foreground" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-semibold">Datensicherung</h3>
            <Icon className={cn("h-4 w-4", ampel.farbe)} />
          </div>
          {stand.zuletzt_ok ? (
            <p className="mt-1 text-sm">
              Letzte Sicherung: <span className="font-medium">{zeitpunkt(stand.zuletzt_ok)}</span>
              <span className="text-muted-foreground">
                {" "}· {groesse(stand.groesse_bytes)}
                {stand.volumes !== null && ` · ${stand.volumes} Volume(s)`}
              </span>
            </p>
          ) : (
            <p className="mt-1 text-sm">Noch keine Sicherung gemeldet.</p>
          )}
          {stand.letzter_fehler && (
            <p className="mt-1 text-sm text-red-500">
              Letzter Lauf gescheitert am {zeitpunkt(stand.letzter_fehler.zeit)}
              {stand.letzter_fehler.schritt && ` (Schritt: ${stand.letzter_fehler.schritt})`}.
            </p>
          )}
          {stand.ampel !== "ok" && (
            <p className="mt-2 text-xs text-muted-foreground">
              {stand.eingerichtet
                ? "Auf dem Server täglich scripts/backup.sh ausführen (Einrichtung: scripts/install-backup-cron.sh)."
                : "BACKUP_STATUS_TOKEN fehlt beim Orchestrator: einmal scripts/backup.sh ausführen und den Orchestrator neu starten."}
              {" "}Details im Handbuch, Kapitel „Betrieb & Datenschutz“.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

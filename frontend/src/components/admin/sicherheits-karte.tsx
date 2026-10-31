"use client";

/** Karte „Sicherheitsstatus“ unter Admin → Betrieb.
 *
 * Eine Ampel über die Einstellungen der Anlage, die ein Administrator kennen muss:
 * Redis-Zugriffsregeln, Schlüssel, Sentinel, CORS, Agenten mit sudo, Agenten auf
 * altem Abbild, Docker-Version, Apps im Plattformnetz. Jede Zeile sagt, was der
 * Zustand bedeutet und wie man ihn behebt. Die Karte liest nur — geändert wird in
 * der .env bzw. bei den Agenten.
 *
 * Serverseitig nur für Administratoren (GET /settings/sicherheit,
 * core/sicherheitsstatus.py).
 */

import { useCallback, useEffect, useState } from "react";
import { Info, Loader2, RefreshCw, ShieldAlert, ShieldCheck, ShieldX } from "lucide-react";
import * as api from "@/lib/api";
import type { SicherheitsPunkt, SicherheitsStand } from "@/lib/api";
import { apiFehlertext } from "@/lib/api-fehler";
import { cn } from "@/lib/utils";

const DARSTELLUNG: Record<SicherheitsPunkt["stand"], { icon: typeof ShieldCheck; farbe: string; name: string }> = {
  kritisch: { icon: ShieldX, farbe: "text-red-500", name: "kritisch" },
  warnung: { icon: ShieldAlert, farbe: "text-amber-600 dark:text-amber-400", name: "Warnung" },
  ok: { icon: ShieldCheck, farbe: "text-emerald-600 dark:text-emerald-400", name: "in Ordnung" },
  info: { icon: Info, farbe: "text-muted-foreground", name: "zur Kenntnis" },
};

export function SicherheitsKarte() {
  const [stand, setStand] = useState<SicherheitsStand | null>(null);
  const [fehler, setFehler] = useState<string | null>(null);
  const [laedt, setLaedt] = useState(false);

  const laden = useCallback(async () => {
    setLaedt(true);
    try {
      setStand(await api.getSicherheit());
      setFehler(null);
    } catch (e) {
      setFehler(apiFehlertext(e, "Unbekannter Fehler"));
    } finally {
      setLaedt(false);
    }
  }, []);

  useEffect(() => { laden(); }, [laden]);

  if (fehler) {
    return (
      <div className="mb-4 rounded-xl border border-red-500/20 bg-red-500/5 p-4 text-sm text-red-500">
        Sicherheitsstatus nicht abrufbar: {fehler}
      </div>
    );
  }
  if (!stand) {
    return (
      <div className="mb-4 flex items-center gap-2 rounded-xl border border-foreground/[0.06] bg-card/80 p-4 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" /> Sicherheitsstatus wird geprüft …
      </div>
    );
  }

  const { kritisch, warnung } = stand.zaehlung;
  const Kopf = kritisch ? ShieldX : warnung ? ShieldAlert : ShieldCheck;
  const kopfFarbe = kritisch ? DARSTELLUNG.kritisch.farbe : warnung ? DARSTELLUNG.warnung.farbe : DARSTELLUNG.ok.farbe;

  return (
    <div className="mb-4 rounded-xl border border-foreground/[0.06] bg-card/80 p-4">
      <div className="flex items-start gap-3">
        <Kopf className={cn("mt-0.5 h-5 w-5 shrink-0", kopfFarbe)} />
        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-3">
            <div>
              <h3 className="text-sm font-semibold">Sicherheitsstatus</h3>
              <p className="mt-1 text-xs text-muted-foreground">
                {kritisch
                  ? `${kritisch} kritische${kritisch === 1 ? "r Punkt" : " Punkte"}, ${warnung} Warnung${warnung === 1 ? "" : "en"}.`
                  : warnung
                    ? `Keine kritischen Punkte, ${warnung} Warnung${warnung === 1 ? "" : "en"}.`
                    : "Alles in Ordnung."}{" "}
                Die Prüfung liest nur; geändert wird in der .env oder bei den Agenten.
              </p>
            </div>
            <button
              type="button"
              onClick={laden}
              disabled={laedt}
              className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-foreground/[0.08] px-2.5 py-1.5 text-[11px] font-medium text-muted-foreground hover:bg-foreground/[0.05] hover:text-foreground disabled:opacity-50"
            >
              <RefreshCw className={cn("h-3 w-3", laedt && "animate-spin")} /> Neu prüfen
            </button>
          </div>

          <ul className="mt-3 divide-y divide-foreground/[0.05]">
            {stand.punkte.map((p) => {
              const d = DARSTELLUNG[p.stand];
              const Icon = d.icon;
              return (
                <li key={p.schluessel} className="flex items-start gap-2.5 py-2.5">
                  <Icon className={cn("mt-0.5 h-4 w-4 shrink-0", d.farbe)} aria-label={d.name} />
                  <div className="min-w-0">
                    <p className="text-[13px] font-medium">{p.titel}</p>
                    <p className="text-xs text-muted-foreground">{p.text}</p>
                    {p.beheben && (
                      <p className="mt-0.5 text-xs">
                        <span className="font-medium">Beheben:</span> {p.beheben}
                      </p>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        </div>
      </div>
    </div>
  );
}

"use client";

/** Karte „Agenten aktuell halten“ unter Admin → Betrieb.
 *
 * Nach einem Release brauchen Agenten oft einen neuen Behälter (neues Abbild,
 * Sicherheitsregeln, andere Proxy-Stufe). Ist „Im Leerlauf“ gewählt, erledigt das die
 * Anlage selbst: ein Agent, der gerade nichts tut, wird neu erstellt — höchstens einer
 * je Durchlauf (alle fünf Minuten). Gestoppte Agenten werden beim nächsten Start
 * aktualisiert. Die Karte zeigt, wer noch wartet und warum.
 *
 * Serverseitig nur für Administratoren (GET/PUT /settings/agenten-updates,
 * core/agenten_aktualisieren.py); jede Änderung und jedes Aktualisieren steht im
 * Prüfprotokoll.
 */

import { useCallback, useEffect, useState } from "react";
import { Loader2, RefreshCw, RotateCw } from "lucide-react";
import * as api from "@/lib/api";
import type { AgentenUpdatesStand } from "@/lib/api";
import { apiFehlertext } from "@/lib/api-fehler";
import { useToast } from "@/components/ui/dialog-provider";
import { cn } from "@/lib/utils";

const MODI: { modus: AgentenUpdatesStand["modus"]; name: string }[] = [
  { modus: "leerlauf", name: "Im Leerlauf" },
  { modus: "aus", name: "Aus" },
];

export function AgentenUpdatesKarte() {
  const toast = useToast();
  const [stand, setStand] = useState<AgentenUpdatesStand | null>(null);
  const [fehler, setFehler] = useState<string | null>(null);
  const [laedt, setLaedt] = useState(false);
  const [speichert, setSpeichert] = useState(false);

  const laden = useCallback(async () => {
    setLaedt(true);
    try {
      setStand(await api.getAgentenUpdates());
      setFehler(null);
    } catch (e) {
      setFehler(apiFehlertext(e, "Unbekannter Fehler"));
    } finally {
      setLaedt(false);
    }
  }, []);

  useEffect(() => { laden(); }, [laden]);

  async function umschalten(modus: AgentenUpdatesStand["modus"]) {
    if (!stand || (modus === stand.modus && !stand.angehalten)) return;
    setSpeichert(true);
    try {
      setStand(await api.setAgentenUpdates(modus));
      toast.success(modus === "aus" ? "Automatisches Aktualisieren aus" : "Agenten werden im Leerlauf aktualisiert");
    } catch (e) {
      toast.error("Nicht gespeichert", apiFehlertext(e));
    } finally {
      setSpeichert(false);
    }
  }

  if (fehler) {
    return (
      <div className="mb-4 rounded-xl border border-red-500/20 bg-red-500/5 p-4 text-sm text-red-500">
        Agenten-Updates nicht abrufbar: {fehler}
      </div>
    );
  }
  if (!stand) {
    return (
      <div className="mb-4 flex items-center gap-2 rounded-xl border border-foreground/[0.06] bg-card/80 p-4 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" /> Agenten-Updates werden geladen …
      </div>
    );
  }

  return (
    <div className="mb-4 rounded-xl border border-foreground/[0.06] bg-card/80 p-4">
      <div className="flex items-start gap-3">
        <RotateCw className="mt-0.5 h-5 w-5 shrink-0 text-muted-foreground" />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <h3 className="text-sm font-semibold">Agenten aktuell halten</h3>
              <p className="mt-1 text-xs text-muted-foreground">
                Nach einem Update der Anlage (jetzt Version {stand.version}) brauchen Agenten einen
                neuen Behälter, damit Sicherheitsregeln und neue Funktionen in ihnen gelten. Im
                Leerlauf erledigt das die Anlage selbst: nie während eines Auftrags, einer
                Besprechung oder in den ersten 15 Minuten nach einem Gespräch, höchstens ein Agent
                alle fünf Minuten, nie bei einer offenen Freigabe. Gestoppte Agenten werden beim
                nächsten Start aktualisiert. Arbeitsdaten bleiben erhalten. Scheitert ein Versuch,
                ruht die Automatik, bis du sie hier wieder aufnimmst.
              </p>
            </div>
            <div role="radiogroup" aria-label="Agenten automatisch aktualisieren" className="inline-flex shrink-0 rounded-lg border border-foreground/[0.08] p-0.5">
              {MODI.map((m) => (
                <button
                  key={m.modus}
                  type="button"
                  role="radio"
                  aria-checked={stand.modus === m.modus}
                  disabled={speichert}
                  onClick={() => umschalten(m.modus)}
                  className={cn(
                    "rounded-md px-2.5 py-1 text-xs font-medium transition-colors disabled:opacity-50",
                    stand.modus === m.modus ? "bg-primary/10 text-primary" : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  {m.name}
                </button>
              ))}
            </div>
          </div>

          {stand.angehalten && stand.modus === "leerlauf" && (
            <div className="mt-3 flex flex-wrap items-center justify-between gap-2 rounded-lg bg-amber-500/10 px-3 py-2">
              <p className="text-xs text-amber-700 dark:text-amber-300">
                Ein automatisches Aktualisieren ist gescheitert, die Automatik ruht für diese
                Version. Häufigster Grund: Das Agenten-Abbild fehlt auf dem Server. Prüfen (siehe
                Prüfprotokoll), dann wieder aufnehmen.
              </p>
              <button
                type="button"
                disabled={speichert}
                onClick={() => umschalten("leerlauf")}
                className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-amber-500/30 px-2.5 py-1 text-xs font-medium text-amber-700 hover:bg-amber-500/10 disabled:opacity-50 dark:text-amber-300"
              >
                Wieder aufnehmen
              </button>
            </div>
          )}

          <div className="mt-3 flex items-center justify-between gap-2">
            <p className="text-xs font-medium">
              {stand.ausstehend.length === 0
                ? "Alle Agenten sind auf dem aktuellen Stand."
                : `${stand.ausstehend.length} Agent(en) warten auf ein Update:`}
            </p>
            <button
              type="button"
              onClick={laden}
              disabled={laedt}
              className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-foreground/[0.08] px-2.5 py-1 text-xs font-medium text-muted-foreground hover:bg-foreground/[0.05] hover:text-foreground disabled:opacity-50"
            >
              <RefreshCw className={cn("h-3 w-3", laedt && "animate-spin")} /> Neu laden
            </button>
          </div>
          {stand.ausstehend.length > 0 && (
            <ul className="mt-1.5 divide-y divide-foreground/[0.05]">
              {stand.ausstehend.map((a) => (
                <li key={a.id} className="flex items-center justify-between gap-3 py-1.5 text-xs">
                  <span className="truncate font-medium">{a.name}</span>
                  <span
                    className={cn(
                      "shrink-0 text-right",
                      a.grund === "fehlgeschlagen" ? "text-amber-600 dark:text-amber-400" : "text-muted-foreground",
                    )}
                  >
                    {a.text}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}

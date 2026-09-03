"use client";

// Was DIESER Nutzer benutzen darf — die Member-Sicht auf „Modelle".
//
// Der Reiter zeigte bis 2026-08-15 fuer jeden dieselbe Seite: Provider-
// Konfiguration, ChatGPT-Login der Plattform, Max Turns, Anzahl gleichzeitiger
// Agenten. Fuer einen Member ist dort **nichts** einstellbar — alles davon
// gehoert der Anlage, nicht ihm. Er sah eine Bedienoberflaeche, die auf keinen
// seiner Knopfdruecke reagiert.
//
// Seine Frage ist eine andere: „welche Modelle stehen mir zur Verfuegung?"
// Genau das steht hier — lesend, ohne einen einzigen Schalter.
//
// Die Liste kommt ungefiltert aus der Schnittstelle: ``/ai-accounts`` liefert
// einem Nicht-Administrator ohnehin nur die ihm freigegebenen Konten
// (default-deny). Es wird hier also nichts ausgeblendet, was der Server nicht
// schon zurueckgehalten haette — die Anzeige ist keine Sicherheitsgrenze.

import { useEffect, useState } from "react";
import { Cpu, Info, Loader2 } from "lucide-react";
import * as api from "@/lib/api";
import type { AIAccount } from "@/lib/types";

export function AvailableModels() {
  const [konten, setKonten] = useState<AIAccount[]>([]);
  const [laedt, setLaedt] = useState(true);

  useEffect(() => {
    (async () => {
      try {
        setKonten(await api.listAIAccounts(true));
      } catch {
        setKonten([]);
      } finally {
        setLaedt(false);
      }
    })();
  }, []);

  if (laedt) {
    return (
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <Loader2 className="h-3.5 w-3.5 animate-spin" /> Modelle werden geladen…
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <p className="text-xs text-muted-foreground">
        Diese Modelle hat dein Administrator für dich freigegeben. Du wählst sie
        beim Anlegen eines Agenten aus — hier gibt es nichts einzustellen.
      </p>

      {konten.length === 0 ? (
        /* Der wichtigste Fall: nichts freigegeben. Vorher stand hier eine
           Provider-Auswahl, die nach dem Klick nichts tat — jetzt steht da,
           WARUM nichts da ist und was zu tun ist. */
        <div className="flex items-start gap-2.5 rounded-xl border border-border bg-card/40 px-3 py-3">
          <Info className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground/60" />
          <div className="text-xs">
            <p className="font-medium">Noch kein Modell freigegeben</p>
            <p className="mt-0.5 text-muted-foreground">
              Frag deinen Administrator nach einem Firmen-Zugang — oder verbinde
              unter <span className="font-medium">Meine KI-Zugänge</span> dein
              eigenes Claude- oder Codex-Abo.
            </p>
          </div>
        </div>
      ) : (
        konten.map((k) => {
          const modelle = (k.models || []) as { name?: string }[];
          return (
            <div key={k.id} className="rounded-xl border border-border bg-card/40 p-3">
              <div className="flex items-center gap-2.5">
                <Cpu className="h-4 w-4 shrink-0 text-muted-foreground/60" />
                <div className="min-w-0">
                  <p className="text-sm font-medium">{k.name}</p>
                  <p className="text-[11px] text-muted-foreground">{k.provider_type}</p>
                </div>
              </div>
              {modelle.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {modelle.map((m, i) => (
                    <span
                      key={i}
                      className="rounded-md bg-foreground/[0.06] px-2 py-0.5 font-mono text-[10px]"
                    >
                      {typeof m === "string" ? m : m.name || "?"}
                    </span>
                  ))}
                </div>
              )}
            </div>
          );
        })
      )}
    </div>
  );
}

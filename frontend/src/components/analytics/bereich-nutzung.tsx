"use client";

import { useEffect, useState } from "react";
import { LayoutGrid } from "lucide-react";
import { getNutzungUebersicht, type BereichNutzung } from "@/lib/api";
import { MENUE_PUNKTE } from "@/components/layout/sidebar";
import { bereichAusPfad } from "@/hooks/use-nutzung";
import { cn } from "@/lib/utils";

/** Welche Bereiche der Oberfläche werden auf dieser Anlage genutzt (#1047)?
 *
 * Nur Summen — Aufrufe und wie viele Personen, nie wer. Die festen Menüpunkte
 * stehen immer in der Liste, auch mit 0: genau die sind die Kandidaten, um sie
 * Rollen über die Menüauswahl (Admin → Rechte) gar nicht erst anzubieten.
 */
export function BereichNutzung({ tage }: { tage: number }) {
  const [daten, setDaten] = useState<BereichNutzung[] | null>(null);
  const [fehler, setFehler] = useState(false);

  useEffect(() => {
    let aktiv = true;
    setFehler(false);
    getNutzungUebersicht(tage)
      .then((d) => aktiv && setDaten(d.bereiche))
      .catch(() => aktiv && setFehler(true));
    return () => {
      aktiv = false;
    };
  }, [tage]);

  if (fehler) return null;
  if (!daten) return null;

  const jeBereich = new Map(daten.map((d) => [d.bereich, d]));
  const zeilen = MENUE_PUNKTE.map((m) => {
    const d = jeBereich.get(bereichAusPfad(m.href));
    return { ...m, aufrufe: d?.aufrufe ?? 0, personen: d?.personen ?? 0, zuletzt: d?.zuletzt ?? null };
  }).sort((a, b) => b.aufrufe - a.aufrufe || a.label.localeCompare(b.label, "de"));
  const ungenutzt = zeilen.filter((z) => z.aufrufe === 0).length;
  const hoechster = Math.max(1, ...zeilen.map((z) => z.aufrufe));

  return (
    <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-5">
      <div className="mb-1 flex items-center gap-2">
        <LayoutGrid className="h-4 w-4 text-muted-foreground" />
        <h3 className="text-sm font-semibold">Genutzte Bereiche</h3>
      </div>
      <p className="mb-4 text-[12px] text-muted-foreground">
        Wie oft jeder Bereich in den letzten {tage} Tagen geöffnet wurde — ohne Namen, kleine
        Personenzahlen nur als „unter 3“.
        {ungenutzt > 0 &&
          ` ${ungenutzt} ${ungenutzt === 1 ? "Bereich wurde" : "Bereiche wurden"} von niemandem geöffnet — die lassen sich Rollen über Admin → Rechte gar nicht erst anbieten.`}
      </p>
      <div className="overflow-x-auto">
        <table className="w-full text-left">
          <thead>
            <tr className="border-b border-foreground/[0.06] text-[11px] text-muted-foreground">
              <th className="pb-2 pr-4 font-medium">Bereich</th>
              <th className="pb-2 px-3 font-medium w-1/3">Aufrufe</th>
              <th className="pb-2 px-3 text-right font-medium">Personen</th>
              <th className="pb-2 pl-3 text-right font-medium">Zuletzt</th>
            </tr>
          </thead>
          <tbody>
            {zeilen.map((z) => (
              <tr key={z.href} className="border-b border-foreground/[0.04] last:border-0">
                <td className="py-2 pr-4">
                  <div className={cn("text-[13px] font-medium", z.aufrufe === 0 && "text-muted-foreground")}>{z.label}</div>
                  <div className="text-[11px] text-muted-foreground">
                    {z.gruppe}
                    {z.weitere && " · unter „Weitere Bereiche“"}
                  </div>
                </td>
                <td className="py-2 px-3">
                  <div className="flex items-center gap-2">
                    <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-foreground/[0.06]">
                      <div className="h-full rounded-full bg-primary/70" style={{ width: `${(z.aufrufe / hoechster) * 100}%` }} />
                    </div>
                    <span className="w-10 text-right text-[12px] tabular-nums">{z.aufrufe}</span>
                  </div>
                </td>
                <td className="py-2 px-3 text-right text-[12px] tabular-nums text-muted-foreground">
                  {/* 1 oder 2 hieße auf kleinen Anlagen praktisch „diese Person“. */}
                  {z.personen === 0 ? 0 : z.personen < 3 ? "unter 3" : z.personen}
                </td>
                <td className="py-2 pl-3 text-right text-[12px] text-muted-foreground">
                  {z.zuletzt ? new Date(z.zuletzt).toLocaleDateString("de-DE") : z.aufrufe === 0 ? "nie" : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

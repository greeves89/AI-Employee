"use client";

import { cn } from "@/lib/utils";

/** Uhrzeit im 24-Stunden-Format als zwei Auswahllisten („08“ : „00“).
 *
 *  Das native ``<input type="time">`` richtet sich nach der Sprache des Betriebssystems,
 *  nicht nach der Seite: auf englisch eingestellten Rechnern stand mitten in der
 *  deutschen Oberfläche „08:00 AM“ (Persona Teamleitung, Runde 5 und 7).
 *  Wert und ``onChange`` bleiben „HH:MM“ — wie beim nativen Feld. */
export function UhrzeitFeld({
  value,
  onChange,
  className,
  ariaLabel = "Uhrzeit",
}: {
  value: string;
  onChange: (wert: string) => void;
  className?: string;
  ariaLabel?: string;
}) {
  const [hh = "08", mm = "00"] = (value || "08:00").split(":");
  const minuten = Array.from({ length: 12 }, (_, i) => String(i * 5).padStart(2, "0"));
  // Eine gespeicherte Minute außerhalb des 5-Minuten-Rasters bleibt wählbar.
  if (!minuten.includes(mm)) minuten.push(mm);
  minuten.sort();
  const feld = "rounded-lg border border-foreground/[0.1] bg-background/80 py-1.5 pl-2 text-sm text-foreground";
  return (
    <span className={cn("inline-flex items-center gap-1", className)} role="group" aria-label={ariaLabel}>
      <select aria-label={`${ariaLabel}: Stunde`} value={hh} onChange={(e) => onChange(`${e.target.value}:${mm}`)} className={feld}>
        {Array.from({ length: 24 }, (_, i) => String(i).padStart(2, "0")).map((h) => (
          <option key={h} value={h}>{h}</option>
        ))}
      </select>
      <span className="text-sm text-muted-foreground">:</span>
      <select aria-label={`${ariaLabel}: Minute`} value={mm} onChange={(e) => onChange(`${hh}:${e.target.value}`)} className={feld}>
        {minuten.map((m) => (
          <option key={m} value={m}>{m}</option>
        ))}
      </select>
    </span>
  );
}

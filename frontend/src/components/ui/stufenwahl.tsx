"use client";

import { Minus, Plus } from "lucide-react";
import { cn } from "@/lib/utils";

/** Eine kleine ganze Zahl waehlen: [−] 2 [+].
 *
 * Fuer Werte wie „wie viele gleichzeitig" (1–8) oder „wie oft wiederholen"
 * (0–5). Dort standen ein Schieberegler ueber die volle Breite UND ein
 * Zahlenfeld nebeneinander — zwei Bedienelemente fuer eine Ziffer. Die
 * Stufenwahl ist so breit wie ihr Inhalt und per Tastatur (Pfeile) bedienbar.
 */
export function Stufenwahl({
  wert,
  setWert,
  min = 0,
  max = 10,
  einheit,
  disabled = false,
  label,
}: {
  wert: number;
  setWert: (n: number) => void;
  min?: number;
  max?: number;
  /** Optional hinter der Zahl, z. B. „×" oder „Min." */
  einheit?: string;
  disabled?: boolean;
  /** Fuer Screenreader, wenn kein sichtbares Label daneben steht. */
  label?: string;
}) {
  const setze = (n: number) => setWert(Math.max(min, Math.min(max, n)));
  const knopf =
    "flex h-8 w-8 items-center justify-center text-muted-foreground transition-colors hover:bg-foreground/[0.06] hover:text-foreground disabled:opacity-30 disabled:hover:bg-transparent";
  return (
    <div
      role="spinbutton"
      aria-label={label}
      aria-valuemin={min}
      aria-valuemax={max}
      aria-valuenow={wert}
      tabIndex={disabled ? -1 : 0}
      onKeyDown={(e) => {
        if (disabled) return;
        if (e.key === "ArrowUp" || e.key === "ArrowRight") { e.preventDefault(); setze(wert + 1); }
        if (e.key === "ArrowDown" || e.key === "ArrowLeft") { e.preventDefault(); setze(wert - 1); }
      }}
      className={cn(
        "inline-flex items-center overflow-hidden rounded-lg border border-foreground/[0.1] bg-background/60 outline-none focus-visible:ring-2 focus-visible:ring-primary/40",
        disabled && "opacity-50",
      )}
    >
      <button type="button" tabIndex={-1} className={knopf} disabled={disabled || wert <= min} onClick={() => setze(wert - 1)} aria-label="Weniger">
        <Minus className="h-3.5 w-3.5" />
      </button>
      <span className="min-w-[2.5rem] px-1 text-center text-sm font-medium tabular-nums">
        {wert}
        {einheit && <span className="ml-0.5 text-muted-foreground">{einheit}</span>}
      </span>
      <button type="button" tabIndex={-1} className={knopf} disabled={disabled || wert >= max} onClick={() => setze(wert + 1)} aria-label="Mehr">
        <Plus className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}

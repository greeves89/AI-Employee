"use client";

import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

/** Symbolknopf in der Fußzeile des Chats (Anhängen, Sprechen, Konnektoren …).
 *
 * Am Rechner reicht das Symbol, die Erklärung steht im Tooltip. Auf dem Handy
 * gibt es keinen Tooltip — dort standen fünf, sechs Symbole ohne ein Wort
 * daneben (#907). Unter ``sm`` steht deshalb die Beschriftung unter dem Symbol;
 * ``aria-label`` trägt sie für Bildschirmleser in jeder Breite.
 */
export function ChatSymbolKnopf({
  beschriftung,
  titel,
  onClick,
  disabled,
  children,
}: {
  /** Kurzes Wort unter dem Symbol (Handy) und Name für Bildschirmleser. */
  beschriftung: string;
  /** Ausführlicher Tooltip; ohne Angabe die Beschriftung. */
  titel?: string;
  onClick: () => void;
  disabled?: boolean;
  /** Das Symbol selbst (lucide, h-4 w-4). */
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={beschriftung}
      title={titel ?? beschriftung}
      className={cn(
        "flex min-w-[2.75rem] flex-col items-center justify-center gap-0.5 rounded-lg px-1 py-1 text-muted-foreground/70 transition-all hover:bg-foreground/[0.06] hover:text-foreground disabled:opacity-40",
        "sm:h-8 sm:w-8 sm:min-w-0 sm:p-0",
      )}
    >
      {children}
      <span className="text-[10px] leading-none sm:hidden">{beschriftung}</span>
    </button>
  );
}

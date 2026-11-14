"use client";

import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

/** Symbolknopf in der Fußzeile des Chats (Anhängen, Sprechen, Konnektoren …).
 *
 * Die Beschriftung steht in jeder Breite unter dem Symbol (#907 fürs Handy, seit
 * 14.11.2026 auch am Rechner); ``aria-label`` trägt sie für Bildschirmleser,
 * der Tooltip die ausführliche Erklärung.
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
        // Beschriftet auf allen Größen: am Desktop standen bis zu acht Symbole ohne
        // Text nebeneinander (UI-Bewertung 14.11.2026), auf dem Handy waren sie beschriftet.
        "flex min-w-[2.75rem] flex-col items-center justify-center gap-0.5 rounded-lg px-1.5 py-1 text-muted-foreground transition-all hover:bg-foreground/[0.06] hover:text-foreground disabled:opacity-40",
      )}
    >
      {children}
      <span className="text-[10px] leading-none">{beschriftung}</span>
    </button>
  );
}

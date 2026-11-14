"use client";

import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

/** Symbolknopf in der Fußzeile des Chats (Anhängen, Sprechen, Konnektoren …).
 *
 * Nur für die zwei häufigsten Aktionen (Anhängen, Sprechen); ``aria-label`` trägt die
 * Beschriftung für Bildschirmleser, der Tooltip die Erklärung. Alles Weitere steht
 * beschriftet im Menü „Werkzeuge“ (Agenten-Seite, ``Schnellzugriff``).
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
        // Nur das Symbol: Beschriftungen unter acht Symbolen wirkten unruhig und brachen
        // um (Daniel, 14.11.2026). Selteneres steht beschriftet im Menü „Werkzeuge“.
        "flex h-9 w-9 items-center justify-center rounded-lg text-muted-foreground transition-all hover:bg-foreground/[0.06] hover:text-foreground disabled:opacity-40",
      )}
    >
      {children}
    </button>
  );
}

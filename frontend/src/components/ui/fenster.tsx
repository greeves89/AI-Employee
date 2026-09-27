"use client";

import { useEffect } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";

/** Ein Fenster ueber der Seite: schliesst per Esc, Klick daneben oder X.
 *  Genutzt von der Agenten-Seite (Aufgabe ansehen, Konnektoren, Modell …),
 *  damit man dabei im Chat bleibt. */
export function Fenster({
  offen,
  schliessen,
  titel,
  breite = "max-w-4xl",
  children,
}: {
  offen: boolean;
  schliessen: () => void;
  /** Ohne Titel bringt der Inhalt seinen eigenen Kopf mit. */
  titel?: string;
  breite?: string;
  children: React.ReactNode;
}) {
  useEffect(() => {
    if (!offen) return;
    const beiTaste = (e: KeyboardEvent) => {
      if (e.key === "Escape") schliessen();
    };
    document.addEventListener("keydown", beiTaste);
    return () => document.removeEventListener("keydown", beiTaste);
  }, [offen, schliessen]);

  if (!offen || typeof document === "undefined") return null;
  return createPortal(
    <div className="fixed inset-0 z-[90] flex items-center justify-center p-4" role="dialog" aria-modal="true">
      <div className="absolute inset-0 bg-black/50 backdrop-blur-sm" onClick={schliessen} />
      <div className={cn("relative flex max-h-[88vh] w-full flex-col overflow-hidden rounded-2xl border border-foreground/[0.08] bg-background shadow-2xl", breite)}>
        <div className="flex items-center gap-3 border-b border-foreground/[0.06] px-5 py-3">
          {titel && <h2 className="text-sm font-semibold">{titel}</h2>}
          <button
            onClick={schliessen}
            title="Schließen"
            className="ml-auto flex h-8 w-8 items-center justify-center rounded-lg text-muted-foreground hover:bg-foreground/[0.06] hover:text-foreground transition-colors"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto">{children}</div>
      </div>
    </div>,
    document.body,
  );
}

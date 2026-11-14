"use client";

import type { ElementType, ReactNode } from "react";

/** Ein leerer Zustand für alle Seiten: Symbol, Überschrift, ein Satz, höchstens eine
 *  Aktion. Vorbild ist „Noch kein Projekt“. Bis 14.11.2026 sah jede Seite anders aus
 *  (gestrichelte Kästen, halbe Breite, freistehend), das wirkte uneinheitlich
 *  (UI-Bewertung). */
export function LeerZustand({
  icon: Icon,
  titel,
  text,
  children,
}: {
  icon: ElementType;
  titel: string;
  text?: ReactNode;
  /** Aktion(en) unter dem Text, z. B. ein Knopf. */
  children?: ReactNode;
}) {
  return (
    <div className="mx-auto max-w-md py-16 text-center">
      {/* Getönte Kachel statt blassem Grau: leere Seiten wirkten leblos
          (Gestaltungsprüfung Runde 5). */}
      <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-primary/10 text-primary">
        <Icon className="h-7 w-7" aria-hidden="true" />
      </div>
      <h2 className="mt-3 font-display text-lg font-semibold">{titel}</h2>
      {text && <p className="mt-1 text-sm text-muted-foreground">{text}</p>}
      {children && <div className="mt-4">{children}</div>}
    </div>
  );
}

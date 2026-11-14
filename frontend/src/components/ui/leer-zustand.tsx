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
      <Icon className="mx-auto h-10 w-10 text-muted-foreground/50" aria-hidden="true" />
      <h2 className="mt-3 font-display text-lg font-semibold">{titel}</h2>
      {text && <p className="mt-1 text-sm text-muted-foreground">{text}</p>}
      {children && <div className="mt-4">{children}</div>}
    </div>
  );
}

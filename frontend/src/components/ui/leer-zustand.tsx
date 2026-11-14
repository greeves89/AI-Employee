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
  schritte,
  children,
}: {
  icon: ElementType;
  titel: string;
  text?: ReactNode;
  /** Kurzer Ablauf („1 … 2 … 3 …“): zeigt, was hier entsteht, statt nur eines
   *  Satzes über einer leeren Fläche (Gestaltungsprüfung Runde 7). */
  schritte?: string[];
  /** Aktion(en) unter dem Text, z. B. ein Knopf. */
  children?: ReactNode;
}) {
  return (
    <div className="mx-auto max-w-xl py-16 text-center">
      {/* Getönte Kachel statt blassem Grau: leere Seiten wirkten leblos
          (Gestaltungsprüfung Runde 5). */}
      <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-primary/10 text-primary">
        <Icon className="h-7 w-7" aria-hidden="true" />
      </div>
      <h2 className="mt-3 font-display text-lg font-semibold">{titel}</h2>
      {text && <p className="mt-1 text-sm text-muted-foreground">{text}</p>}
      {schritte && schritte.length > 0 && (
        <ol className="mx-auto mt-5 grid max-w-md gap-2 text-left sm:grid-cols-3">
          {schritte.map((s, i) => (
            <li key={s} className="flex items-start gap-2 rounded-xl border border-border bg-card p-3 text-[13px] leading-snug">
              <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-semibold text-primary">{i + 1}</span>
              <span>{s}</span>
            </li>
          ))}
        </ol>
      )}
      {children && <div className="mt-4">{children}</div>}
    </div>
  );
}

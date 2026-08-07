"use client";

import { Plus, Trash2 } from "lucide-react";
import { cn } from "@/lib/utils";
import type {
  Responsibility,
  ResponsibilityPriority,
  ResponsibilityRhythm,
} from "@/lib/types";

// Spiegelt die serverseitigen Grenzen (agents.py / templates.py) — hier nur, damit die
// UI gar nicht erst in ein 422 laeuft.
export const MAX_RESPONSIBILITIES = 20;

export const RHYTHMS: { value: ResponsibilityRhythm; label: string }[] = [
  { value: "daily", label: "taeglich" },
  { value: "weekly", label: "woechentlich" },
  { value: "monthly", label: "monatlich" },
  { value: "continuous", label: "laufend" },
];

export const PRIORITIES: { value: ResponsibilityPriority; label: string }[] = [
  { value: "high", label: "hoch" },
  { value: "normal", label: "normal" },
  { value: "low", label: "niedrig" },
];

/**
 * Verantwortungsbereiche bearbeiten — EIN Editor fuer Agent und Vorlage.
 *
 * Der Editor stand nur an einem Agenten. Vorlagen konnten seit dem Backend zwar
 * Bereiche tragen, aber niemand konnte sie eintragen: jeder neue Agent musste von
 * Hand eingerichtet werden. Statt einer zweiten, leicht abweichenden Fassung liegt
 * er hier — wer eine Regel aendert (Grenze, Takt, Prioritaet), aendert sie fuer beide.
 */
export function ResponsibilitiesEditor({
  value,
  onChange,
  emptyHint,
  footnote,
}: {
  value: Responsibility[];
  onChange: (next: Responsibility[]) => void;
  emptyHint?: string;
  footnote?: string;
}) {
  const add = () =>
    onChange(
      value.length >= MAX_RESPONSIBILITIES
        ? value
        : [...value, { title: "", rhythm: "daily", priority: "normal", notes: "" }],
    );
  const patch = (idx: number, p: Partial<Responsibility>) =>
    onChange(value.map((d, i) => (i === idx ? { ...d, ...p } : d)));
  const remove = (idx: number) => onChange(value.filter((_, i) => i !== idx));

  return (
    <div>
      <div className="mb-1 flex items-center justify-between gap-2">
        <span className="text-[10px] uppercase tracking-wide text-muted-foreground/40">
          Verantwortungsbereiche
        </span>
        <button
          type="button"
          onClick={add}
          disabled={value.length >= MAX_RESPONSIBILITIES}
          className={cn(
            "inline-flex items-center gap-1 rounded-lg border border-foreground/[0.08] px-2 py-0.5 text-[10px] text-muted-foreground transition-colors hover:bg-foreground/[0.04]",
            value.length >= MAX_RESPONSIBILITIES && "cursor-not-allowed opacity-40",
          )}
        >
          <Plus className="h-3 w-3" />
          Bereich
        </button>
      </div>
      {value.length === 0 ? (
        <div className="rounded-lg border border-dashed border-foreground/[0.08] p-2.5 text-[10px] leading-relaxed text-muted-foreground/50">
          {emptyHint ??
            "Noch keine Bereiche. Ohne sie plant der Agent nur, was jemand als Todo angelegt hat — mit ihnen weiss er, wofuer er dauerhaft zustaendig ist, und baut sich daraus selbst den Tag."}
        </div>
      ) : (
        <div className="space-y-1.5">
          {value.map((duty, idx) => (
            <div
              key={idx}
              className="rounded-lg border border-foreground/[0.06] bg-background/60 p-2"
            >
              <div className="flex items-center gap-1.5">
                <input
                  type="text"
                  value={duty.title}
                  onChange={(e) => patch(idx, { title: e.target.value })}
                  placeholder="z.B. Posteingang sichten"
                  className="min-w-0 flex-1 rounded-md border border-foreground/[0.08] bg-background/60 px-2 py-1 text-[11px] text-foreground placeholder:text-muted-foreground/30 focus:border-emerald-500/40 focus:outline-none"
                />
                <select
                  value={duty.rhythm}
                  onChange={(e) => patch(idx, { rhythm: e.target.value as ResponsibilityRhythm })}
                  className="rounded-md border border-foreground/[0.08] bg-background/60 px-1.5 py-1 text-[10px] text-foreground focus:border-emerald-500/40 focus:outline-none"
                >
                  {RHYTHMS.map((r) => (
                    <option key={r.value} value={r.value}>{r.label}</option>
                  ))}
                </select>
                <select
                  value={duty.priority}
                  onChange={(e) => patch(idx, { priority: e.target.value as ResponsibilityPriority })}
                  className="rounded-md border border-foreground/[0.08] bg-background/60 px-1.5 py-1 text-[10px] text-foreground focus:border-emerald-500/40 focus:outline-none"
                >
                  {PRIORITIES.map((p) => (
                    <option key={p.value} value={p.value}>{p.label}</option>
                  ))}
                </select>
                <button
                  type="button"
                  onClick={() => remove(idx)}
                  title="Bereich entfernen"
                  className="shrink-0 rounded-md p-1 text-muted-foreground/40 transition-colors hover:bg-foreground/[0.06] hover:text-red-400"
                >
                  <Trash2 className="h-3 w-3" />
                </button>
              </div>
              <input
                type="text"
                value={duty.notes ?? ""}
                onChange={(e) => patch(idx, { notes: e.target.value })}
                placeholder="Praezisierung (optional) — woran genau erkennt er, dass es erledigt ist?"
                className="mt-1.5 w-full rounded-md border border-foreground/[0.06] bg-background/40 px-2 py-1 text-[10px] text-muted-foreground placeholder:text-muted-foreground/25 focus:border-emerald-500/40 focus:outline-none"
              />
            </div>
          ))}
        </div>
      )}
      <div className="mt-1.5 text-[10px] text-muted-foreground/40">
        {footnote ??
          "Dauerauftraege, keine Todos: der Lauf leitet daraus die Aufgaben des Tages ab (STEP 1). Ein Bereich wird nie „fertig\" — abgehakt wird der heutige Durchgang."}
      </div>
    </div>
  );
}

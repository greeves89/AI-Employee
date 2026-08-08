"use client";

import { useCallback, useEffect, useState } from "react";
import { Loader2, Sparkles, Calendar, AlertCircle } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { listSyntheses, synthesizeNow, type Synthesis } from "@/lib/api";
import { useToast } from "@/components/ui/dialog-provider";

/**
 * Wochensynthese (#384) — was zieht sich durch die letzten sieben Tage?
 *
 * Eine Synthese ist ein ganz normaler Wissenseintrag mit dem Merkmal
 * `created_by = "synthesis"`. Diese Ansicht ist deshalb nur eine gefilterte Sicht
 * auf den Graphen, kein zweiter Speicher — wer den Eintrag anklickt, landet in
 * derselben Detailansicht wie bei jedem anderen Wissenseintrag.
 */
export function SynthesisView({ onOpenEntry }: { onOpenEntry?: (id: number) => void }) {
  const [items, setItems] = useState<Synthesis[]>([]);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const toast = useToast();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setItems((await listSyntheses()).syntheses);
    } catch {
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const runNow = async () => {
    setRunning(true);
    try {
      const res = await synthesizeNow();
      if (res.written > 0) {
        toast.success("Synthese erstellt", "Der neue Eintrag steht oben.");
        await load();
      } else if (res.errors?.length) {
        // Der häufigste Fall ist ein fehlender LLM-Zugang — das gehört benannt,
        // sonst wirkt der Knopf einfach kaputt.
        toast.error("Nicht möglich", res.errors[0]);
      } else {
        toast.warning(
          "Zu wenig Material",
          "In den letzten sieben Tagen kam zu wenig zusammen, um ein Muster zu erkennen."
        );
      }
    } catch (e) {
      toast.error("Fehler", e instanceof Error ? e.message : "Synthese fehlgeschlagen");
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4">
      <div className="flex items-center justify-between gap-3">
        <p className="text-[12px] text-muted-foreground">
          Einmal pro Woche wird zusammengefasst, welches Thema sich durchzieht, wo Neues
          einer älteren Überzeugung widerspricht und was der größte Hebel wäre.
        </p>
        <button
          onClick={runNow}
          disabled={running}
          className="flex shrink-0 items-center gap-2 rounded-xl bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-40"
        >
          {running ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
          Jetzt synthetisieren
        </button>
      </div>

      {loading ? (
        <div className="flex flex-1 items-center justify-center">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      ) : items.length === 0 ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-2 text-center">
          <AlertCircle className="h-6 w-6 text-muted-foreground/40" />
          <p className="text-sm text-muted-foreground">Noch keine Synthese vorhanden.</p>
          <p className="max-w-md text-[12px] text-muted-foreground/60">
            Sie entsteht montags automatisch, sobald genug Material da ist — oder sofort
            über den Knopf oben.
          </p>
        </div>
      ) : (
        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto pr-1">
          {items.map((s) => (
            <article
              key={s.id}
              onClick={() => onOpenEntry?.(s.id)}
              className="cursor-pointer rounded-xl border border-foreground/[0.06] bg-card/80 p-5 transition-colors hover:bg-foreground/[0.03]"
            >
              <header className="mb-2 flex items-center gap-2">
                <Calendar className="h-3.5 w-3.5 text-primary" />
                <h3 className="text-sm font-medium">{s.title}</h3>
                <span className="ml-auto text-[11px] text-muted-foreground/50">
                  {s.created_at ? new Date(s.created_at).toLocaleDateString("de-DE") : ""}
                </span>
              </header>
              <div className="prose prose-sm dark:prose-invert max-w-none text-[13px] text-muted-foreground [&_h2]:mb-1 [&_h2]:mt-3 [&_h2]:text-[12px] [&_h2]:font-semibold [&_h2]:uppercase [&_h2]:tracking-wide [&_h2]:text-muted-foreground/50">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{s.content}</ReactMarkdown>
              </div>
            </article>
          ))}
        </div>
      )}
    </div>
  );
}

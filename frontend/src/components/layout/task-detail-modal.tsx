"use client";

import { useEffect, useState, type ReactNode } from "react";
import { X, Loader2 } from "lucide-react";
import * as api from "@/lib/api";
import type { Task } from "@/lib/types";
import { cn } from "@/lib/utils";

const statusStyle: Record<string, string> = {
  completed: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
  failed: "bg-red-500/10 text-red-400 border-red-500/20",
  running: "bg-blue-500/10 text-blue-400 border-blue-500/20",
  pending: "bg-amber-500/10 text-amber-400 border-amber-500/20",
  cancelled: "bg-zinc-500/10 text-zinc-400 border-zinc-500/20",
};

function fmt(s: string | null): string {
  if (!s) return "—";
  try {
    return new Date(s).toLocaleString("de-DE");
  } catch {
    return s;
  }
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-foreground/[0.06] bg-foreground/[0.02] px-3 py-2">
      <div className="text-[10px] uppercase tracking-wider text-muted-foreground/50">{label}</div>
      <div className="text-sm font-medium mt-0.5">{value}</div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div>
      <div className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground/60 mb-1.5">{title}</div>
      <div className="rounded-lg border border-foreground/[0.06] bg-background/50 p-3 max-h-60 overflow-auto">{children}</div>
    </div>
  );
}

export function TaskDetailModal({ taskId, onClose }: { taskId: string | null; onClose: () => void }) {
  const [task, setTask] = useState<Task | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!taskId) return;
    setLoading(true);
    setError(null);
    setTask(null);
    api
      .getTask(taskId)
      .then(setTask)
      .catch((e) => setError(e instanceof Error ? e.message : "Konnte Task nicht laden"))
      .finally(() => setLoading(false));
  }, [taskId]);

  if (!taskId) return null;

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/50" onClick={onClose} />
      <div className="relative z-10 w-full max-w-2xl max-h-[85vh] overflow-auto rounded-2xl border border-foreground/10 bg-card shadow-2xl">
        <div className="sticky top-0 flex items-center justify-between border-b border-foreground/[0.06] bg-card px-5 py-3.5">
          <h3 className="text-sm font-semibold">Task-Details</h3>
          <button onClick={onClose} className="p-1 rounded hover:bg-foreground/[0.06]" title="Schließen">
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="p-5 space-y-4">
          {loading && (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" /> Lade Task…
            </div>
          )}
          {error && <div className="text-sm text-red-400">{error}</div>}
          {task && (
            <>
              <div>
                <div className="flex items-center gap-2 flex-wrap">
                  <span className={cn("inline-flex items-center rounded-full border px-2.5 py-0.5 text-[11px] font-medium", statusStyle[task.status] || statusStyle.pending)}>
                    {task.status}
                  </span>
                  {task.model && <span className="text-[11px] font-mono text-muted-foreground/70">{task.model}</span>}
                </div>
                <h4 className="mt-2 text-sm font-medium break-words">{task.title}</h4>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <Stat label="Kosten" value={task.cost_usd != null ? `$${task.cost_usd.toFixed(4)}` : "—"} />
                <Stat label="Dauer" value={task.duration_ms != null ? `${(task.duration_ms / 1000).toFixed(1)}s` : "—"} />
                <Stat label="Tokens (in/out)" value={`${task.input_tokens ?? 0} / ${task.output_tokens ?? 0}`} />
                <Stat label="Schritte" value={String(task.num_turns ?? "—")} />
                <Stat label="Erstellt" value={fmt(task.created_at)} />
                <Stat label="Fertig" value={fmt(task.completed_at)} />
              </div>
              {task.prompt && (
                <Section title="Auftrag">
                  <pre className="whitespace-pre-wrap break-words text-[12px] text-muted-foreground">{task.prompt}</pre>
                </Section>
              )}
              {task.result && (
                <Section title="Ergebnis">
                  <pre className="whitespace-pre-wrap break-words text-[12px]">{task.result}</pre>
                </Section>
              )}
              {task.error && (
                <Section title="Fehler">
                  <pre className="whitespace-pre-wrap break-words text-[12px] text-red-400">{task.error}</pre>
                </Section>
              )}
              {task.agent_id && (
                <a href={`/agents/${task.agent_id}`} className="inline-block text-[12px] text-primary hover:underline">
                  → Zum Agent
                </a>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

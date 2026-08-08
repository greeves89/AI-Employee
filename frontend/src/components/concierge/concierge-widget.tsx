"use client";

import { useCallback, useEffect, useState } from "react";
import {
  LifeBuoy,
  X,
  Loader2,
  RefreshCw,
  CircleCheck,
  CircleAlert,
  Clock,
  Play,
  Square,
  RotateCw,
  Stethoscope,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { getConciergeOverview, runConciergeAction, type ConciergeOverview } from "@/lib/api";
import { useAuthStore } from "@/lib/auth";
import { useConfirm, useToast } from "@/components/ui/dialog-provider";

/**
 * Admin-Concierge (#11) — „läuft alles?" in einer Antwort.
 *
 * Die Zahlen gab es alle schon, nur auf fünf verschiedenen Seiten. Bewusst ohne
 * Sprachmodell dahinter: ein Concierge, der eine Zahl halluziniert, ist schlimmer als
 * gar keiner. Hier steht ausschließlich, was die vorhandenen Abfragen liefern.
 *
 * Nur für Administratoren — und die Aktionsliste wird serverseitig geprüft, nicht hier:
 * ein Widget, das nur die sicheren Knöpfe zeigt, ist keine Absicherung.
 */
const VERDICT: Record<string, { label: string; className: string; Icon: React.ElementType }> = {
  "alles ruhig": {
    label: "Alles ruhig",
    className: "text-emerald-500 dark:text-emerald-400",
    Icon: CircleCheck,
  },
  "wartet auf dich": {
    label: "Wartet auf dich",
    className: "text-amber-500 dark:text-amber-400",
    Icon: Clock,
  },
  handlungsbedarf: {
    label: "Handlungsbedarf",
    className: "text-red-500 dark:text-red-400",
    Icon: CircleAlert,
  },
};

const ACTION_ICON: Record<string, React.ElementType> = {
  restart_agent: RotateCw,
  stop_agent: Square,
  start_agent: Play,
  run_self_test: Stethoscope,
};

export function ConciergeWidget() {
  const isAdmin = useAuthStore((s) => s.user?.role) === "admin";
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<ConciergeOverview | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  const confirm = useConfirm();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setData(await getConciergeOverview());
    } catch {
      setData(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (open) load();
  }, [open, load]);

  if (!isAdmin) return null;

  const verdict = VERDICT[data?.verdict ?? ""] ?? VERDICT["alles ruhig"];
  const VerdictIcon = verdict.Icon;

  const act = async (action: string, agentId?: string, label?: string) => {
    const ok = await confirm({
      title: label ?? action,
      message: agentId ? `Agent ${agentId}` : "Für die ganze Plattform ausführen?",
      confirmLabel: "Ausführen",
    });
    if (!ok) return;
    setBusy(true);
    try {
      await runConciergeAction(action, agentId);
      toast.success("Erledigt");
      await load();
    } catch (e) {
      toast.error("Fehlgeschlagen", e instanceof Error ? e.message : undefined);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      {!open && (
        <button
          onClick={() => setOpen(true)}
          title="Concierge"
          className="fixed bottom-5 right-5 z-40 flex h-11 w-11 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-lg shadow-primary/25 transition-transform hover:scale-105"
        >
          <LifeBuoy className="h-5 w-5" />
        </button>
      )}

      {open && (
        <div className="fixed bottom-5 right-5 z-40 flex max-h-[70dvh] w-[min(23rem,calc(100vw-2.5rem))] flex-col overflow-hidden rounded-2xl border border-foreground/[0.08] bg-card/95 shadow-2xl backdrop-blur-md">
          <div className="flex items-center justify-between border-b border-foreground/[0.06] px-4 py-3">
            <div className="flex items-center gap-2">
              <LifeBuoy className="h-4 w-4 text-primary" />
              <span className="text-sm font-semibold">Concierge</span>
            </div>
            <div className="flex items-center gap-1">
              <button
                onClick={load}
                disabled={loading}
                className="rounded-lg p-1.5 text-muted-foreground hover:bg-foreground/[0.06]"
              >
                <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />
              </button>
              <button
                onClick={() => setOpen(false)}
                className="rounded-lg p-1.5 text-muted-foreground hover:bg-foreground/[0.06]"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto p-4">
            {loading && !data ? (
              <div className="flex justify-center py-8">
                <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
              </div>
            ) : !data ? (
              <p className="py-6 text-center text-[12px] text-muted-foreground">
                Keine Daten abrufbar.
              </p>
            ) : (
              <div className="space-y-4">
                <div className={cn("flex items-center gap-2 text-sm font-medium", verdict.className)}>
                  <VerdictIcon className="h-4 w-4" />
                  {verdict.label}
                </div>

                <dl className="grid grid-cols-2 gap-2 text-[12px]">
                  <div className="rounded-lg border border-foreground/[0.06] bg-foreground/[0.02] p-2.5">
                    <dt className="text-[10px] uppercase tracking-wide text-muted-foreground/50">
                      Agenten
                    </dt>
                    <dd className="mt-0.5 font-medium">{data.agents.total}</dd>
                  </div>
                  <div className="rounded-lg border border-foreground/[0.06] bg-foreground/[0.02] p-2.5">
                    <dt className="text-[10px] uppercase tracking-wide text-muted-foreground/50">
                      Freigaben offen
                    </dt>
                    <dd
                      className={cn(
                        "mt-0.5 font-medium",
                        data.pending_approvals > 0 && "text-amber-400"
                      )}
                    >
                      {data.pending_approvals}
                    </dd>
                  </div>
                  <div className="rounded-lg border border-foreground/[0.06] bg-foreground/[0.02] p-2.5">
                    <dt className="text-[10px] uppercase tracking-wide text-muted-foreground/50">
                      Aufgaben 24h
                    </dt>
                    <dd className="mt-0.5 font-medium">
                      {data.tasks_24h.total}
                      {data.tasks_24h.failed > 0 && (
                        <span className="ml-1 text-[11px] text-red-400">
                          ({data.tasks_24h.failed} rot)
                        </span>
                      )}
                    </dd>
                  </div>
                  <div className="rounded-lg border border-foreground/[0.06] bg-foreground/[0.02] p-2.5">
                    <dt className="text-[10px] uppercase tracking-wide text-muted-foreground/50">
                      Kosten 24h
                    </dt>
                    <dd className="mt-0.5 font-medium">
                      ${data.cost_24h_usd.toFixed(2)}
                    </dd>
                  </div>
                </dl>

                {data.tasks_24h.stale > 0 && (
                  <p className="rounded-lg border border-amber-500/30 bg-amber-500/[0.07] p-2.5 text-[11px] text-amber-700 dark:text-amber-300">
                    {data.tasks_24h.stale} Aufgabe(n) hängen seit über 30 Minuten.
                  </p>
                )}

                {data.agents.unhealthy.length > 0 && (
                  <div>
                    <div className="mb-1.5 text-[10px] uppercase tracking-wide text-muted-foreground/50">
                      Braucht Aufmerksamkeit
                    </div>
                    <div className="space-y-1.5">
                      {data.agents.unhealthy.map((a) => (
                        <div
                          key={a.id}
                          className="flex items-center justify-between gap-2 rounded-lg border border-foreground/[0.06] bg-foreground/[0.02] px-2.5 py-2"
                        >
                          <div className="min-w-0">
                            <div className="truncate text-[12px] font-medium">{a.name}</div>
                            <div className="text-[10px] text-muted-foreground/50">{a.state}</div>
                          </div>
                          <button
                            onClick={() => act("restart_agent", a.id, "Agent neu starten")}
                            disabled={busy}
                            className="shrink-0 rounded-lg border border-foreground/[0.08] px-2 py-1 text-[11px] hover:bg-foreground/[0.06] disabled:opacity-40"
                          >
                            Neu starten
                          </button>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                <div>
                  <div className="mb-1.5 text-[10px] uppercase tracking-wide text-muted-foreground/50">
                    Plattform
                  </div>
                  {data.actions
                    .filter((a) => a.id === "run_self_test")
                    .map((a) => {
                      const Icon = ACTION_ICON[a.id] ?? Play;
                      return (
                        <button
                          key={a.id}
                          onClick={() => act(a.id, undefined, a.label)}
                          disabled={busy}
                          className="flex w-full items-center gap-2 rounded-lg border border-foreground/[0.08] px-2.5 py-2 text-[12px] hover:bg-foreground/[0.06] disabled:opacity-40"
                        >
                          <Icon className="h-3.5 w-3.5" />
                          {a.label}
                        </button>
                      );
                    })}
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </>
  );
}

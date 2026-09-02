"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  LifeBuoy,
  ArrowRight,
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
import { formatMoney } from "@/lib/money";

/**
 * Admin-Concierge (#11) — „läuft alles?" in einer Antwort.
 *
 * Die Zahlen gab es alle schon, nur auf fünf verschiedenen Seiten. Bewusst ohne
 * Sprachmodell dahinter: ein Concierge, der eine Zahl halluziniert, ist schlimmer als
 * gar keiner. Hier steht ausschließlich, was die vorhandenen Abfragen liefern.
 *
 * Nur für Administratoren — und die Aktionsliste wird serverseitig geprüft, nicht hier:
 * ein Widget, das nur die sicheren Knöpfe zeigt, ist keine Absicherung.
 *
 * Geöffnet über den Concierge-Knopf im Sidebar-Kopf (Event "concierge-widget:open") —
 * der frühere schwebende Knopf unten rechts ist raus, weil er Eingabefelder überdeckt hat.
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

  // Einziger Einstieg: der Concierge-Knopf im Sidebar-Kopf.
  useEffect(() => {
    const openPanel = () => setOpen(true);
    window.addEventListener("concierge-widget:open", openPanel);
    return () => window.removeEventListener("concierge-widget:open", openPanel);
  }, []);

  if (!isAdmin) return null;

  const verdict = VERDICT[data?.verdict ?? ""] ?? VERDICT["alles ruhig"];
  const VerdictIcon = verdict.Icon;
  const items = data?.items ?? [];
  // Fallback auf die alten Felder: waere der Orchestrator noch aelter, staende hier
  // sonst gar nichts statt wenigstens der Zahlen.
  const stats = data?.stats ?? {
    agents: data?.agents.total ?? 0,
    resting: data?.agents.resting?.length ?? 0,
    tasks_24h: data?.tasks_24h.total ?? 0,
    failed_24h: data?.tasks_24h.failed ?? 0,
    cost_24h_usd: data?.cost_24h_usd ?? 0,
  };

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

                {/* Die Liste. Nur was eine Entscheidung oder einen Handgriff
                    braucht — jeder Punkt mit genau einer Sache, die man dagegen
                    tun kann. Vorher standen hier vier Zahlenkacheln, und die
                    Ampel musste den Alarm allein tragen. */}
                {items.length === 0 ? (
                  <p className="rounded-lg border border-foreground/[0.06] bg-foreground/[0.02] p-3 text-[12px] text-muted-foreground">
                    Nichts, was auf dich wartet. Zahlen und Verläufe stehen im
                    Dashboard.
                  </p>
                ) : (
                  <div className="space-y-1.5">
                    {items.map((it, i) => {
                      const broken = it.severity === "broken";
                      return (
                        <div
                          key={`${it.kind}-${it.agent_id ?? i}`}
                          className={cn(
                            "rounded-lg border px-2.5 py-2",
                            broken
                              ? "border-red-500/20 bg-red-500/[0.05]"
                              : "border-amber-500/20 bg-amber-500/[0.05]",
                          )}
                        >
                          <div className="flex items-start justify-between gap-2">
                            <div className="min-w-0">
                              <div className="flex items-center gap-1.5">
                                {broken ? (
                                  <CircleAlert className="h-3 w-3 shrink-0 text-red-400" />
                                ) : (
                                  <Clock className="h-3 w-3 shrink-0 text-amber-700 dark:text-amber-400" />
                                )}
                                <span className="truncate text-[12px] font-medium">
                                  {it.title}
                                </span>
                              </div>
                              <div className="mt-0.5 text-[10px] text-muted-foreground/60">
                                {it.detail}
                              </div>
                            </div>
                            <div className="flex shrink-0 items-center gap-1">
                              {it.action && (
                                <button
                                  onClick={() =>
                                    act(it.action as string, it.agent_id ?? undefined,
                                        it.action_label ?? it.action ?? "")
                                  }
                                  disabled={busy}
                                  className="rounded-lg border border-foreground/[0.08] px-2 py-1 text-[11px] hover:bg-foreground/[0.06] disabled:opacity-40"
                                >
                                  {it.action_label ?? "Ausführen"}
                                </button>
                              )}
                              {it.link && (
                                <Link
                                  href={it.link}
                                  onClick={() => setOpen(false)}
                                  title="Ansehen"
                                  className="rounded-lg p-1.5 text-muted-foreground/60 hover:bg-foreground/[0.06] hover:text-foreground"
                                >
                                  <ArrowRight className="h-3.5 w-3.5" />
                                </Link>
                              )}
                            </div>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}

                {/* Die Zahlen bleiben — aber als Fussnote. Sie verlangen keine
                    Handlung, und als vier Kacheln haben sie die Liste verdraengt. */}
                <p className="border-t border-foreground/[0.06] pt-2.5 text-[10px] text-muted-foreground/50">
                  {stats.agents} Agent{stats.agents !== 1 ? "en" : ""}
                  {stats.resting > 0 && ` · ${stats.resting} ruhen`}
                  {" · "}
                  {stats.tasks_24h} Aufgabe{stats.tasks_24h !== 1 ? "n" : ""} in 24 h
                  {stats.failed_24h > 0 && ` (${stats.failed_24h} rot)`}
                  {" · "}{formatMoney(stats.cost_24h_usd)}
                </p>

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

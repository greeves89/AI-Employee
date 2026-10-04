"use client";

import { useState, useEffect, useCallback, useMemo, useRef, Suspense } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { motion } from "framer-motion";
import {
  Plus, CheckCircle2, XCircle, Clock, Loader2, RotateCcw, Timer,
  Hash, Cpu, Trash2, Ban, Pause, Play, PlayCircle, CalendarClock,
  GitBranch, ChevronRight, Bot,
} from "lucide-react";
import { useTasks } from "@/hooks/use-tasks";
import { useAgents } from "@/hooks/use-agents";
import { Header } from "@/components/layout/header";
import { formatDuration, formatCost, timeAgo } from "@/lib/utils";
import { cn } from "@/lib/utils";
import * as api from "@/lib/api";
import type { Schedule } from "@/lib/types";
import { useSimpleMode } from "@/hooks/use-simple-mode";
import { AUFGABEN_STATUS, zeitplanAnzeige } from "@/lib/aufgaben-anzeige";
import { ZeitplanFormular } from "@/components/schedules/zeitplan-formular";
import { useConfirm, useToast } from "@/components/ui/dialog-provider";
import { apiFehlertext } from "@/lib/api-fehler";

/* ─── Single Tasks Config ─────────────────────────────────────────── */

const statusConfig: Record<string, { icon: typeof CheckCircle2; badge: string; color: string }> = {
  pending: { icon: Clock, badge: "bg-amber-500/10 text-amber-700 dark:text-amber-400 border-amber-500/20", color: "text-amber-700 dark:text-amber-400" },
  queued: { icon: Clock, badge: "bg-blue-500/10 text-blue-400 border-blue-500/20", color: "text-blue-400" },
  running: { icon: Loader2, badge: "bg-blue-500/10 text-blue-400 border-blue-500/20", color: "text-blue-400" },
  completed: { icon: CheckCircle2, badge: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20", color: "text-emerald-400" },
  failed: { icon: XCircle, badge: "bg-red-500/10 text-red-400 border-red-500/20", color: "text-red-400" },
  cancelled: { icon: Ban, badge: "bg-zinc-500/10 text-zinc-400 border-zinc-500/20", color: "text-zinc-400" },
};

const filterTabs = [
  { key: "active", label: "Aktiv" },
  { key: "all", label: "Alle" },
  { key: "completed", label: "Erledigt" },
  { key: "failed", label: "Fehlgeschlagen" },
];

const ACTIVE_STATUSES = ["pending", "queued", "running"];

/* ─── Schedule Helpers ─────────────────────────────────────────────── */

const containerVariants = {
  hidden: { opacity: 0 },
  visible: { opacity: 1, transition: { staggerChildren: 0.06 } },
};

const itemVariants = {
  hidden: { opacity: 0, y: 12 },
  visible: { opacity: 1, y: 0, transition: { duration: 0.35, ease: [0.25, 0.46, 0.45, 0.94] as const } },
};

function formatInterval(seconds: number): string {
  if (seconds < 3600) return `alle ${Math.round(seconds / 60)} Min`;
  if (seconds < 86400) return `alle ${Math.round(seconds / 3600)} Std`;
  return `alle ${Math.round(seconds / 86400)} Tage`;
}

function formatRelative(dateStr: string | null): string {
  if (!dateStr) return "nie";
  const date = new Date(dateStr);
  const now = new Date();
  const diffMs = date.getTime() - now.getTime();
  const absDiff = Math.abs(diffMs);

  if (absDiff < 60000) return diffMs > 0 ? "in < 1 Min" : "vor < 1 Min";
  if (absDiff < 3600000) {
    const m = Math.round(absDiff / 60000);
    return diffMs > 0 ? `in ${m} Min` : `vor ${m} Min`;
  }
  if (absDiff < 86400000) {
    const h = Math.round(absDiff / 3600000);
    return diffMs > 0 ? `in ${h} Std` : `vor ${h} Std`;
  }
  // Weiter weg: Wochentag + Datum + Uhrzeit — „Nächster: Mo., 05.10., 07:30"
  // sagt mehr als nur das Datum.
  return date.toLocaleString("de-DE", {
    weekday: "short", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
  });
}

/* ─── Main Page ────────────────────────────────────────────────────── */

type ViewMode = "single" | "scheduled";

// `useSearchParams` braucht eine Suspense-Grenze, sonst bricht der statische
// Build dieser Seite ab.
export default function TasksPage() {
  return (
    <Suspense fallback={null}>
      <TasksPageInner />
    </Suspense>
  );
}

function TasksPageInner() {
  // `/schedules` (und der Kalender mit ?schedule=<id>) leiten hierher um:
  // ?ansicht=zeitplaene öffnet die Zeitpläne, ?schedule=<id> hebt einen hervor.
  const searchParams = useSearchParams();
  const hervorgehoben = searchParams.get("schedule") || "";
  const [viewMode, setViewMode] = useState<ViewMode>(
    searchParams.get("ansicht") === "zeitplaene" || hervorgehoben ? "scheduled" : "single"
  );

  return (
    <div>
      <Header
        title="Aufgaben"
        subtitle={viewMode === "single" ? "Alle Aufgaben deiner Agenten" : "Wiederkehrende Aufgaben, die von selbst laufen"}
        actions={
          viewMode === "single" ? (
            <Link
              href="/tasks/new"
              className="inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground shadow-lg shadow-primary/20 hover:bg-primary/90 transition-all duration-200"
            >
              <Plus className="h-4 w-4" />
              Neue Aufgabe
            </Link>
          ) : null
        }
      />

      <div className="px-4 py-6 sm:px-8">
        {/* View mode toggle */}
        <div className="mb-6 flex gap-1 p-1 rounded-xl bg-foreground/[0.03] border border-foreground/[0.06] w-fit">
          <button
            onClick={() => setViewMode("single")}
            className={cn(
              "rounded-lg px-4 py-2 text-xs font-medium transition-all duration-150",
              viewMode === "single"
                ? "bg-foreground/[0.08] text-foreground shadow-sm"
                : "text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04]"
            )}
          >
            Einzelaufgaben
          </button>
          <button
            onClick={() => setViewMode("scheduled")}
            className={cn(
              "rounded-lg px-4 py-2 text-xs font-medium transition-all duration-150",
              viewMode === "scheduled"
                ? "bg-foreground/[0.08] text-foreground shadow-sm"
                : "text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04]"
            )}
          >
            <span className="inline-flex items-center gap-1.5">
              <Clock className="h-3 w-3" />
              Zeitpläne
            </span>
          </button>
        </div>

        {viewMode === "single" ? <SingleTasksView /> : <ScheduledTasksView hervorgehoben={hervorgehoben} />}
      </div>
    </div>
  );
}

/* ─── Single Tasks View ────────────────────────────────────────────── */

function SingleTasksView() {
  const { tasks, loading, refresh, total } = useTasks();
  const { agents } = useAgents();
  const [filter, setFilter] = useState<string>("active");
  const [deleting, setDeleting] = useState<Set<string>>(new Set());
  const { simpleMode } = useSimpleMode();

  const agentNameById = agents.reduce<Record<string, string>>((acc, a) => {
    acc[a.id] = a.name;
    return acc;
  }, {});

  const filteredTasks = (() => {
    if (filter === "all") return tasks;
    if (filter === "active") return tasks.filter((t) => ACTIVE_STATUSES.includes(t.status));
    return tasks.filter((t) => t.status === filter);
  })();

  const activeCount = tasks.filter((t) => ACTIVE_STATUSES.includes(t.status)).length;

  const retryTask = async (task: { title: string; prompt: string; agent_id: string | null; model: string | null }) => {
    try {
      await api.createTask({
        title: task.title,
        prompt: task.prompt,
        agent_id: task.agent_id || undefined,
        model: task.model || undefined,
      });
      refresh();
    } catch {
      // ignore
    }
  };

  const handleDelete = async (e: React.MouseEvent, taskId: string) => {
    e.preventDefault();
    e.stopPropagation();
    setDeleting((prev) => new Set(prev).add(taskId));
    try {
      await api.deleteTask(taskId);
      refresh();
    } catch {
      // ignore
    } finally {
      setDeleting((prev) => {
        const next = new Set(prev);
        next.delete(taskId);
        return next;
      });
    }
  };

  const handleCancel = async (e: React.MouseEvent, taskId: string) => {
    e.preventDefault();
    e.stopPropagation();
    try {
      await api.cancelTask(taskId);
      refresh();
    } catch {
      // ignore
    }
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
    >
      {/* Filter tabs */}
      <div className="mb-6 flex gap-1 p-1 rounded-xl bg-foreground/[0.03] border border-foreground/[0.06] w-fit max-w-full overflow-x-auto scrollbar-thin">
        {filterTabs.map((tab) => {
          const count = tab.key === "active"
            ? activeCount
            : tab.key === "all"
            ? total || tasks.length
            : tasks.filter((t) => t.status === tab.key).length;
          return (
            <button
              key={tab.key}
              onClick={() => setFilter(tab.key)}
              className={cn(
                "shrink-0 whitespace-nowrap rounded-lg px-4 py-2 text-xs font-medium transition-all duration-150",
                filter === tab.key
                  ? "bg-foreground/[0.08] text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04]"
              )}
            >
              {tab.label}
              <span className="ml-1.5 tabular-nums text-muted-foreground/60">
                {count}
              </span>
            </button>
          );
        })}
      </div>

      {loading && tasks.length === 0 ? (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => (
            <div
              key={i}
              className="rounded-xl border border-foreground/[0.06] bg-card/50 p-5 h-24 animate-shimmer bg-[length:200%_100%] bg-gradient-to-r from-foreground/[0.03] via-foreground/[0.06] to-foreground/[0.03]"
            />
          ))}
        </div>
      ) : filteredTasks.length === 0 ? (
        <div className="rounded-xl border border-dashed border-foreground/[0.1] bg-card/30 p-12 text-center text-muted-foreground">
          {filter === "active"
            ? "Keine aktiven Aufgaben."
            : filter === "all"
            ? "Noch keine Aufgaben."
            : "Keine Aufgaben in dieser Ansicht."}
        </div>
      ) : (
        <div className="space-y-2">
          {filteredTasks.map((task, i) => {
            const cfg = statusConfig[task.status] ?? statusConfig.pending;
            const Icon = cfg.icon;
            const canDelete = task.status !== "running";
            // Laufende Aufgaben waren hier nie abbrechbar — passend dazu wies
            // der Server sie ab. Genau daran scheiterte das Stoppen: der Nutzer
            // hatte KEINEN Weg, eine laufende Aufgabe anzuhalten, und die
            // Sprachfront meldete trotzdem Erfolg (21.08.2026).
            const läuft = task.status === "running";
            const canCancel = läuft || task.status === "queued" || task.status === "pending";
            return (
              <Link key={task.id} href={`/tasks/${task.id}`}>
              <motion.div
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: i * 0.03, duration: 0.25 }}
                className="group rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-4 hover:border-foreground/[0.1] hover:bg-card/90 cursor-pointer transition-all duration-200"
              >
                <div className="flex items-center gap-4">
                  {/* Status icon */}
                  <div className={cn("shrink-0", cfg.color)}>
                    <Icon className={cn("h-5 w-5", task.status === "running" && "animate-spin")} />
                  </div>

                  {/* Content */}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-3">
                      {task.parent_task_id && (
                        <GitBranch className="h-3.5 w-3.5 text-muted-foreground/40 shrink-0" />
                      )}
                      <h4 className="font-medium text-sm truncate">{task.title}</h4>
                      <span className={cn(
                        "shrink-0 inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-medium",
                        cfg.badge
                      )}>
                        {AUFGABEN_STATUS[task.status] ?? task.status}
                      </span>
                      {task.parent_task_id && (
                        <span className="shrink-0 inline-flex items-center rounded-full border border-blue-500/20 bg-blue-500/10 px-2 py-0.5 text-[10px] font-medium text-blue-400">
                          Teilaufgabe
                        </span>
                      )}
                    </div>
                    <p className="text-[12px] text-muted-foreground/70 mt-0.5 line-clamp-1">
                      {task.prompt}
                    </p>
                  </div>

                  {/* Actions */}
                  <div className="shrink-0 flex items-center gap-2">
                    {task.status === "failed" && (
                      <button
                        onClick={(e) => { e.preventDefault(); retryTask(task); }}
                        className="inline-flex items-center gap-1.5 rounded-lg bg-orange-500/10 border border-orange-500/20 px-3 py-1.5 text-[11px] font-medium text-orange-400 hover:bg-orange-500/20 transition-colors"
                      >
                        <RotateCcw className="h-3 w-3" />
                        Wiederholen
                      </button>
                    )}
                    {canCancel && (
                      <button
                        onClick={(e) => handleCancel(e, task.id)}
                        className={cn(
                          "inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-[11px] font-medium transition-colors",
                          läuft
                            // Bei einer laufenden Aufgabe NICHT erst beim Überfahren
                            // zeigen: wer sie stoppen will, sucht den Knopf sofort.
                            ? "bg-red-500/10 border-red-500/20 text-red-400 hover:bg-red-500/20"
                            : "bg-amber-500/10 border-amber-500/20 text-amber-700 dark:text-amber-400 hover:bg-amber-500/20 opacity-0 group-hover:opacity-100",
                        )}
                        title={läuft
                          ? "Laufende Aufgabe stoppen — der Agent bricht seine Arbeit ab"
                          : "Wartende Aufgabe aus der Warteschlange nehmen"}
                      >
                        <Ban className="h-3 w-3" />
                        {läuft ? "Stoppen" : "Abbrechen"}
                      </button>
                    )}
                    {canDelete && (
                      <button
                        onClick={(e) => handleDelete(e, task.id)}
                        disabled={deleting.has(task.id)}
                        className="inline-flex items-center rounded-lg p-1.5 text-muted-foreground/40 hover:text-red-400 hover:bg-red-500/10 transition-colors opacity-0 group-hover:opacity-100 disabled:opacity-50"
                        title="Aufgabe löschen"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    )}
                  </div>
                </div>

                {/* Meta row */}
                <div className="mt-2.5 ml-9 flex items-center gap-4 text-[11px] text-muted-foreground/60">
                  {!simpleMode && <span className="flex items-center gap-1 font-mono">
                    <Hash className="h-3 w-3" />{task.id.slice(0, 8)}
                  </span>}
                  {!simpleMode && task.parent_task_id && (
                    <span className="flex items-center gap-1 text-blue-400/60">
                      <GitBranch className="h-3 w-3" />parent: {task.parent_task_id.slice(0, 8)}
                    </span>
                  )}
                  {task.agent_id && (
                    <span className="flex items-center gap-1" title={task.agent_id}>
                      <Cpu className="h-3 w-3" />{agentNameById[task.agent_id] ?? task.agent_id}
                    </span>
                  )}
                  {task.duration_ms && (
                    <span className="flex items-center gap-1 tabular-nums">
                      <Timer className="h-3 w-3" />{formatDuration(task.duration_ms)}
                    </span>
                  )}
                  {!simpleMode && task.num_turns && (
                    <span className="tabular-nums">{task.num_turns} Züge</span>
                  )}
                  {!simpleMode && task.cost_usd ? (
                    <span className="tabular-nums">{formatCost(task.cost_usd)}</span>
                  ) : null}
                  <span>{timeAgo(task.created_at)}</span>
                </div>

                {/* Error */}
                {task.error && (
                  <div className="mt-2.5 ml-9 rounded-lg bg-red-500/5 border border-red-500/10 px-3 py-2 text-[11px] text-red-400/80">
                    {task.error}
                  </div>
                )}
              </motion.div>
              </Link>
            );
          })}
        </div>
      )}
    </motion.div>
  );
}

/* ─── Scheduled Tasks View ─────────────────────────────────────────── */

function ScheduledTasksView({ hervorgehoben = "" }: { hervorgehoben?: string }) {
  const [schedules, setSchedules] = useState<Schedule[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [triggering, setTriggering] = useState<string | null>(null);
  const confirm = useConfirm();
  const toast = useToast();
  const hervorRef = useRef<HTMLDivElement | null>(null);
  const { simpleMode } = useSimpleMode();
  const { agents } = useAgents();
  //: Welche Agenten aufgeklappt sind. Startet LEER — bei einem Dutzend Agenten
  //: mit je mehreren Zeitplaenen ist eine flache Liste nicht mehr lesbar.
  const [offeneGruppen, setOffeneGruppen] = useState<Set<string>>(new Set());

  //: Nach Agent gruppiert, Gruppen alphabetisch, Zeitplaene innerhalb nach dem
  //: nächsten Lauf — was als naechstes dran ist, steht oben.
  const gruppen = useMemo(() => {
    const namen = new Map(agents.map((a) => [a.id, a.name]));
    const nach = new Map<string, { id: string; name: string; plaene: Schedule[] }>();
    for (const plan of schedules) {
      const id = plan.agent_id ?? "";
      if (!nach.has(id)) {
        nach.set(id, {
          id,
          // Ein Zeitplan ohne Agenten läuft über die Lastverteilung — er
          // gehoert trotzdem sichtbar irgendwohin, sonst faellt er unter den
          // Tisch.
          name: id ? (namen.get(id) ?? `Unbekannter Agent (${id})`) : "Ohne festen Agenten",
          plaene: [],
        });
      }
      nach.get(id)!.plaene.push(plan);
    }
    return Array.from(nach.values())
      .map((g) => ({
        ...g,
        plaene: [...g.plaene].sort((a, b) => a.next_run_at.localeCompare(b.next_run_at)),
        aktiv: g.plaene.filter((p) => p.enabled).length,
      }))
      .sort((a, b) => a.name.localeCompare(b.name));
  }, [schedules, agents]);

  const gruppeUmschalten = (id: string) =>
    setOffeneGruppen((vorher) => {
      const naechste = new Set(vorher);
      if (naechste.has(id)) naechste.delete(id);
      else naechste.add(id);
      return naechste;
    });

  const alleOffen = gruppen.length > 0 && offeneGruppen.size === gruppen.length;

  const refresh = useCallback(async () => {
    try {
      const data = await api.getSchedules();
      setSchedules(data.schedules);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 20000);
    return () => clearInterval(interval);
  }, [refresh]);

  // Kommt man mit ?schedule=<id> (Kalender, Aktivität), muss der gemeinte
  // Zeitplan aufgeklappt und im Blick sein — nicht in einer zugeklappten Gruppe.
  useEffect(() => {
    if (!hervorgehoben) return;
    const plan = schedules.find((s) => s.id === hervorgehoben);
    if (!plan) return;
    const gruppe = plan.agent_id ?? "";
    setOffeneGruppen((vorher) => (vorher.has(gruppe) ? vorher : new Set(vorher).add(gruppe)));
  }, [hervorgehoben, schedules]);

  useEffect(() => {
    if (hervorgehoben && hervorRef.current) {
      hervorRef.current.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }, [hervorgehoben, offeneGruppen]);

  const handleToggle = async (schedule: Schedule) => {
    try {
      if (schedule.enabled) {
        await api.pauseSchedule(schedule.id);
      } else {
        await api.resumeSchedule(schedule.id);
      }
      await refresh();
    } catch (e) {
      toast.error(schedule.enabled ? "Pausieren fehlgeschlagen" : "Fortsetzen fehlgeschlagen", apiFehlertext(e));
    }
  };

  const handleTrigger = async (id: string) => {
    setTriggering(id);
    try {
      await api.triggerSchedule(id);
      await refresh();
    } catch (e) {
      toast.error("Ausführen fehlgeschlagen", apiFehlertext(e));
    } finally {
      setTriggering(null);
    }
  };

  const handleDelete = async (schedule: Schedule) => {
    const ok = await confirm({
      title: `Zeitplan „${zeitplanAnzeige(schedule, simpleMode).titel}“ löschen?`,
      message: "Der Zeitplan läuft danach nicht mehr. Bereits erledigte Aufgaben bleiben erhalten.",
      variant: "destructive",
      confirmLabel: "Löschen",
    });
    if (!ok) return;
    try {
      await api.deleteSchedule(schedule.id);
      await refresh();
    } catch (e) {
      toast.error("Löschen fehlgeschlagen", apiFehlertext(e));
    }
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
      className="space-y-6"
    >
      {/* New Schedule button */}
      <div className="flex justify-end">
        <button
          onClick={() => setShowCreate(!showCreate)}
          className="flex items-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground shadow-lg shadow-primary/25 transition-all hover:shadow-primary/40 hover:brightness-110"
        >
          <Plus className="h-4 w-4" />
          Neuer Zeitplan
        </button>
      </div>

      {/* Anlegen — ein gemeinsames Formular (#901) */}
      {showCreate && (
        <ZeitplanFormular
          agents={agents}
          onAbbrechen={() => setShowCreate(false)}
          onAngelegt={async () => {
            setShowCreate(false);
            await refresh();
          }}
        />
      )}

      {/* Schedule List */}
      {loading ? (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => (
            <div
              key={i}
              className="h-24 animate-pulse rounded-2xl bg-foreground/[0.03] border border-foreground/[0.04]"
            />
          ))}
        </div>
      ) : schedules.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-20 text-center">
          <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-foreground/[0.04] mb-4">
            <CalendarClock className="h-7 w-7 text-muted-foreground/50" />
          </div>
          <p className="text-sm font-medium text-muted-foreground">
            Noch keine Zeitpläne
          </p>
          <p className="mt-1 text-xs text-muted-foreground/60">
            Lege eine wiederkehrende Aufgabe an, die dein Agent selbst erledigt
          </p>
        </div>
      ) : (
        <motion.div
          variants={containerVariants}
          initial="hidden"
          animate="visible"
          className="space-y-3"
        >
          {/* Alles auf einmal auf- oder zuklappen — bei vielen Agenten spart
              das ein Dutzend Klicks. */}
          <div className="flex justify-end">
            <button
              onClick={() =>
                setOffeneGruppen(alleOffen ? new Set() : new Set(gruppen.map((g) => g.id)))
              }
              className="text-[11px] text-muted-foreground/60 hover:text-foreground transition-colors"
            >
              {alleOffen ? "Alle zuklappen" : "Alle aufklappen"}
            </button>
          </div>

          {gruppen.map((gruppe) => {
            const offen = offeneGruppen.has(gruppe.id);
            return (
          <div key={gruppe.id || "ohne-agent"} className="space-y-3">
            <button
              onClick={() => gruppeUmschalten(gruppe.id)}
              className="flex w-full items-center gap-2.5 rounded-xl border border-foreground/[0.06] bg-card/50 px-4 py-2.5 text-left transition-colors hover:border-foreground/[0.1] hover:bg-card/80"
            >
              <ChevronRight
                className={`h-3.5 w-3.5 shrink-0 text-muted-foreground/50 transition-transform duration-150 ${offen ? "rotate-90" : ""}`}
              />
              <Bot className="h-3.5 w-3.5 shrink-0 text-muted-foreground/60" />
              <span className="text-sm font-medium truncate">{gruppe.name}</span>
              <span className="ml-auto flex items-center gap-3 text-[11px] text-muted-foreground/60 shrink-0">
                <span>
                  {gruppe.plaene.length} {gruppe.plaene.length === 1 ? "Zeitplan" : "Zeitpläne"}
                </span>
                {gruppe.aktiv > 0 && (
                  <span className="inline-flex items-center gap-1 text-emerald-400">
                    <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
                    {gruppe.aktiv} aktiv
                  </span>
                )}
                {!offen && gruppe.plaene[0] && (
                  <span title="Nächster Lauf in dieser Gruppe" className="hidden sm:inline">
                    Nächster: {formatRelative(gruppe.plaene[0].next_run_at)}
                  </span>
                )}
              </span>
            </button>

            {offen && gruppe.plaene.map((schedule) => (
            <motion.div
              key={schedule.id}
              ref={schedule.id === hervorgehoben ? hervorRef : undefined}
              variants={itemVariants}
              className={cn(
                "ml-2 sm:ml-4 rounded-2xl border bg-card/80 p-4 sm:p-5 backdrop-blur-sm transition-all",
                schedule.id === hervorgehoben
                  ? "border-emerald-500/60 ring-2 ring-emerald-500/30"
                  : "border-foreground/[0.06] hover:border-foreground/[0.1]"
              )}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-3">
                    <h3 className="text-sm font-semibold tracking-tight truncate">
                      {zeitplanAnzeige(schedule, simpleMode).titel}
                    </h3>
                    <span
                      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium ${
                        schedule.enabled
                          ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20"
                          : "bg-foreground/[0.04] text-muted-foreground border border-foreground/[0.06]"
                      }`}
                    >
                      <span
                        className={`h-1.5 w-1.5 rounded-full ${
                          schedule.enabled ? "bg-emerald-400" : "bg-muted-foreground"
                        }`}
                      />
                      {schedule.enabled ? "Aktiv" : "Pausiert"}
                    </span>
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground/70 line-clamp-1">
                    {zeitplanAnzeige(schedule, simpleMode).beschreibung}
                  </p>
                </div>

                {/* Aktionen — immer sichtbar: am Handy gibt es kein Überfahren mit der Maus. */}
                <div className="flex shrink-0 items-center gap-1.5">
                  <button
                    onClick={() => handleTrigger(schedule.id)}
                    disabled={triggering === schedule.id}
                    title="Jetzt ausführen"
                    aria-label="Jetzt ausführen"
                    className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10 border border-primary/20 text-primary hover:bg-primary/20 backdrop-blur-sm transition-colors disabled:opacity-50"
                  >
                    {triggering === schedule.id ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <PlayCircle className="h-3.5 w-3.5" />
                    )}
                  </button>
                  <button
                    onClick={() => handleToggle(schedule)}
                    title={schedule.enabled ? "Pausieren" : "Fortsetzen"}
                    aria-label={schedule.enabled ? "Pausieren" : "Fortsetzen"}
                    className={`flex h-8 w-8 items-center justify-center rounded-lg border backdrop-blur-sm transition-colors ${
                      schedule.enabled
                        ? "bg-amber-500/10 border-amber-500/20 text-amber-700 dark:text-amber-400 hover:bg-amber-500/20"
                        : "bg-emerald-500/10 border-emerald-500/20 text-emerald-400 hover:bg-emerald-500/20"
                    }`}
                  >
                    {schedule.enabled ? (
                      <Pause className="h-3.5 w-3.5" />
                    ) : (
                      <Play className="h-3.5 w-3.5" />
                    )}
                  </button>
                  <button
                    onClick={() => handleDelete(schedule)}
                    title="Löschen"
                    aria-label="Zeitplan löschen"
                    className="flex h-8 w-8 items-center justify-center rounded-lg bg-red-500/10 border border-red-500/20 text-red-400 hover:bg-red-500/20 backdrop-blur-sm transition-colors"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>

              {/* Stats Row */}
              <div className="mt-4 flex flex-wrap items-center gap-x-6 gap-y-2 text-xs text-muted-foreground">
                <div className="flex items-center gap-1.5">
                  <Clock className="h-3.5 w-3.5" />
                  <span title={schedule.cron_expression ?? undefined}>{schedule.takt || formatInterval(schedule.interval_seconds)}</span>
                </div>
                <div className="flex items-center gap-1.5">
                  <Timer className="h-3.5 w-3.5" />
                  <span>Nächster: {schedule.enabled ? formatRelative(schedule.next_run_at) : "pausiert"}</span>
                </div>
                <div className="flex items-center gap-1.5">
                  <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400" />
                  <span>{schedule.success_count}</span>
                </div>
                <div className="flex items-center gap-1.5">
                  <XCircle className="h-3.5 w-3.5 text-red-400" />
                  <span>{schedule.fail_count}</span>
                </div>
                <div className="text-muted-foreground/50">
                  {schedule.total_runs} {schedule.total_runs === 1 ? "Lauf" : "Läufe"}
                  {schedule.total_runs > 0 && (
                    <> &middot; {Math.round(schedule.success_rate * 100)} % erfolgreich</>
                  )}
                </div>
                {schedule.last_run_at && (
                  <div className="text-muted-foreground/50">
                    Zuletzt: {formatRelative(schedule.last_run_at)}
                  </div>
                )}
              </div>
            </motion.div>
            ))}
          </div>
            );
          })}
        </motion.div>
      )}
    </motion.div>
  );
}

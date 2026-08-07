"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { CalendarDays, ChevronLeft, ChevronRight, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { formatCost, formatDuration } from "@/lib/utils";
import * as api from "@/lib/api";
import type { ActivityAgentTimeline, ActivityScheduleMark, ActivityTaskBar, DayPlanItem } from "@/lib/types";
import { CalendarClock, X } from "lucide-react";

// Tagesschluessel in LOKALER Zeit — toISOString() wuerde vor 02:00 MESZ auf den
// Vortag zeigen und den Plan des falschen Tages laden.
// Was im Block steht: „geplant" war frueher fest verdrahtet — auch dann noch, wenn
// die Arbeit laengst lief oder fertig war.
function statusLabel(status: string): string {
  if (status === "running") return "läuft";
  if (status === "done") return "erledigt";
  if (status === "dropped") return "gestrichen";
  return "geplant";
}

function localDateKey(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

const DAY_MS = 24 * 60 * 60 * 1000;
const HOUR_MARKS = [0, 6, 12, 18, 24];
// Every task bar in the horizontal (multi-agent) view is at least this wide,
// regardless of duration — wide enough to fit a truncated title inline, not
// just a colored hairline that only means something on hover.
const MIN_BAR_PX = 34;
// Vertical (single-agent) view: pixel height of one hour row, and the
// minimum height of a task block so a near-instant task still reads as a block.
const HOUR_PX = 88;
const MIN_BLOCK_PX = 22;
// Vertical gap between two blocks that are back-to-back in time (e.g. a
// schedule that fires every few minutes) — without it, adjacent short blocks
// touch with zero seam and read as one solid wall instead of distinct runs.
const BLOCK_GAP_PX = 3;

const statusStyle: Record<string, string> = {
  running: "bg-blue-500/70 border-blue-400",
  completed: "bg-emerald-500/70 border-emerald-400",
  failed: "bg-red-500/70 border-red-400",
  cancelled: "bg-zinc-500/50 border-zinc-400",
  pending: "bg-amber-500/60 border-amber-400",
  queued: "bg-amber-500/60 border-amber-400",
};

// Softer fill for the vertical view's larger blocks — full-opacity status
// colors across a tall block would be harsh; a left accent bar carries the
// status instead (same idiom as most calendar UIs).
const statusAccent: Record<string, string> = {
  running: "border-l-blue-400 bg-blue-500/15",
  completed: "border-l-emerald-400 bg-emerald-500/15",
  failed: "border-l-red-400 bg-red-500/15",
  cancelled: "border-l-zinc-400 bg-zinc-500/10",
  pending: "border-l-amber-400 bg-amber-500/15",
  queued: "border-l-amber-400 bg-amber-500/15",
};

function startOfDay(d: Date): Date {
  const x = new Date(d);
  x.setHours(0, 0, 0, 0);
  return x;
}

// Calendar-date arithmetic, not +/- 24h: on a DST fall-back day the local day
// is 25 hours long, so `getTime() + DAY_MS` lands back on the SAME calendar
// date (23:00) and "Next day" becomes a silent no-op. Date's month/day
// rollover handles this correctly.
function addCalendarDays(d: Date, delta: number): Date {
  const x = new Date(d);
  x.setDate(x.getDate() + delta);
  return x;
}

function fmtDayHeading(d: Date): string {
  return d.toLocaleDateString(undefined, {
    weekday: "long",
    year: "numeric",
    month: "long",
    day: "numeric",
  });
}

function fmtTime(iso: string): string {
  return new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

function pct(ms: number): number {
  return Math.min(100, Math.max(0, (ms / DAY_MS) * 100));
}

interface ActivityTimelineProps {
  /** Scope to a single agent — switches to a vertical day-agenda layout
   * (hours stacked top-to-bottom, like a normal calendar day view) instead
   * of the horizontal one-row-per-agent comparison strip. */
  agentId?: string;
  /** Show the big page-level heading. Off when embedded in another page's own chrome. */
  showHeading?: boolean;
}

export function ActivityTimeline({ agentId, showHeading = true }: ActivityTimelineProps) {
  const [day, setDay] = useState<Date>(() => startOfDay(new Date()));
  const [agents, setAgents] = useState<ActivityAgentTimeline[]>([]);
  const [loading, setLoading] = useState(true);
  const [now, setNow] = useState<Date>(new Date());

  const dayStart = useMemo(() => startOfDay(day), [day]);
  // Calendar arithmetic (see addCalendarDays) — a DST fall-back day is 25 real
  // hours, so dayStart + DAY_MS would land at 23:00 the SAME day and the query
  // range would silently miss the last hour of data.
  const dayEnd = useMemo(() => addCalendarDays(dayStart, 1), [dayStart]);
  const isToday = startOfDay(new Date()).getTime() === dayStart.getTime();

  const load = useCallback(async () => {
    try {
      const res = await api.getActivityTimeline(dayStart, dayEnd, agentId);
      setAgents(res.agents);
    } catch {
      // keep showing the last good data on a transient fetch error
    }
    setLoading(false);
  }, [dayStart, dayEnd, agentId]);

  useEffect(() => {
    setLoading(true);
    load();
  }, [load]);

  // Keep running-task bars and the "now" line live while looking at today.
  useEffect(() => {
    if (!isToday) return;
    const interval = setInterval(() => {
      setNow(new Date());
      load();
    }, 15000);
    return () => clearInterval(interval);
  }, [isToday, load]);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        {showHeading ? (
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">Activity</h1>
            <p className="mt-1 text-sm text-muted-foreground">
              What every agent has planned and what it actually did, one day at a time
            </p>
          </div>
        ) : (
          <div className="text-sm font-medium text-muted-foreground">Tageskalender</div>
        )}
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setDay((d) => addCalendarDays(d, -1))}
            className="flex h-9 w-9 items-center justify-center rounded-lg border border-foreground/[0.08] text-muted-foreground transition-colors hover:bg-foreground/[0.04] hover:text-foreground"
            aria-label="Previous day"
          >
            <ChevronLeft className="h-4 w-4" />
          </button>
          <div className="flex min-w-[220px] items-center justify-center gap-2 rounded-lg border border-foreground/[0.08] px-3 py-2 text-sm font-medium">
            <CalendarDays className="h-4 w-4 text-muted-foreground/60" />
            {fmtDayHeading(dayStart)}
          </div>
          <button
            type="button"
            onClick={() => setDay((d) => addCalendarDays(d, 1))}
            className="flex h-9 w-9 items-center justify-center rounded-lg border border-foreground/[0.08] text-muted-foreground transition-colors hover:bg-foreground/[0.04] hover:text-foreground"
            aria-label="Next day"
          >
            <ChevronRight className="h-4 w-4" />
          </button>
          {!isToday && (
            <button
              type="button"
              onClick={() => setDay(startOfDay(new Date()))}
              className="rounded-lg border border-foreground/[0.08] px-3 py-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-foreground/[0.04] hover:text-foreground"
            >
              Today
            </button>
          )}
        </div>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-24 text-muted-foreground">
          <Loader2 className="h-5 w-5 animate-spin" />
        </div>
      ) : agents.length === 0 ? (
        <div className="rounded-2xl border border-foreground/[0.06] bg-card/50 py-16 text-center text-sm text-muted-foreground">
          No agents visible.
        </div>
      ) : agentId ? (
        <DayAgenda
          agent={agents[0]}
          dayStart={dayStart}
          now={now}
          isToday={isToday}
        />
      ) : (
        <MultiAgentStrip agents={agents} dayStart={dayStart} now={now} isToday={isToday} />
      )}
    </div>
  );
}

// ── Multi-agent comparison strip (global /activity page) ──────────────────
// One horizontal 24h row per agent — good for spotting patterns ACROSS
// agents at a glance, at the cost of cramming a busy day into one thin track.

function MultiAgentStrip({
  agents, dayStart, now, isToday,
}: { agents: ActivityAgentTimeline[]; dayStart: Date; now: Date; isToday: boolean }) {
  const router = useRouter();
  const nowPct = isToday ? pct(now.getTime() - dayStart.getTime()) : null;

  return (
    <>
      <div className="flex pl-[calc(11rem+0.75rem)] pr-1 text-[10px] text-muted-foreground/40">
        {HOUR_MARKS.map((h, i) => (
          <div key={h} className={cn("flex-1", i === HOUR_MARKS.length - 1 ? "text-right" : "text-left")}>
            {String(h).padStart(2, "0")}:00
          </div>
        ))}
      </div>
      <div className="space-y-3">
        {agents.map((a) => (
          <div key={a.agent_id} className="flex items-center gap-3">
            <div className="w-44 shrink-0 truncate text-sm font-medium" title={a.name}>
              {a.name}
            </div>
            <div className="relative h-16 flex-1 overflow-hidden rounded-lg border border-foreground/[0.06] bg-foreground/[0.02]">
              {HOUR_MARKS.slice(1, -1).map((h) => (
                <div key={h} className="absolute bottom-0 top-0 w-px bg-foreground/[0.04]" style={{ left: `${(h / 24) * 100}%` }} />
              ))}

              {a.tasks.map((t) => {
                const startedMs = new Date(t.started_at).getTime() - dayStart.getTime();
                const endedMs = (t.completed_at ? new Date(t.completed_at).getTime() : now.getTime()) - dayStart.getTime();
                const left = pct(startedMs);
                const width = Math.max(pct(endedMs) - left, 0.6);
                const costSuffix = t.cost_usd != null ? ` — ${formatCost(t.cost_usd)}` : "";
                const durationSuffix = t.duration_ms ? ` — ${formatDuration(t.duration_ms)}` : "";
                const timeRange = t.completed_at ? `${fmtTime(t.started_at)}–${fmtTime(t.completed_at)}` : `${fmtTime(t.started_at)}–läuft`;
                return (
                  <button
                    type="button"
                    key={t.task_id}
                    onClick={() => router.push(`/tasks/${t.task_id}`)}
                    className={cn(
                      "group absolute bottom-1 top-1 overflow-hidden rounded-md border px-1.5 py-1 text-left transition-opacity hover:z-10 hover:opacity-80",
                      statusStyle[t.status] || "border-foreground/30 bg-foreground/20",
                      t.status === "running" && "animate-pulse"
                    )}
                    style={{ left: `${left}%`, width: `${width}%`, minWidth: `${MIN_BAR_PX}px` }}
                  >
                    <div className="truncate text-[10px] font-medium leading-tight text-foreground">{t.title}</div>
                    <div className="truncate text-[9px] leading-tight text-foreground/70">{fmtTime(t.started_at)}</div>
                    <div className="pointer-events-none absolute bottom-full left-1/2 z-20 mb-1.5 hidden -translate-x-1/2 whitespace-nowrap rounded-md border border-foreground/10 bg-card px-2 py-1 text-left text-[11px] shadow-lg group-hover:block">
                      <div className="max-w-[280px] truncate font-medium text-foreground">{t.title}</div>
                      <div className="text-muted-foreground/70">{timeRange} · {t.status}{durationSuffix}{costSuffix}</div>
                    </div>
                  </button>
                );
              })}

              {a.scheduled_marks.map((m, i) => (
                <div
                  key={`${m.schedule_id}-${i}`}
                  title={`${m.schedule_name} — ${fmtTime(m.time)}`}
                  className="absolute top-0 h-2 w-2 -translate-x-1/2 rotate-45 border border-foreground/30 bg-background"
                  style={{ left: `${pct(new Date(m.time).getTime() - dayStart.getTime())}%` }}
                />
              ))}

              {nowPct !== null && (
                <div className="absolute bottom-0 top-0 w-px bg-primary/70" style={{ left: `${nowPct}%` }} />
              )}
            </div>
          </div>
        ))}
      </div>
    </>
  );
}

// ── Single-agent day agenda (per-agent "Kalender" sub-tab) ────────────────
// A normal vertical day view — hours stacked top-to-bottom, tasks as blocks
// positioned by time with the title readable directly on the block.

type LanedTask = ActivityTaskBar & { lane: number; laneCount: number };

function layoutLanes(tasks: ActivityTaskBar[], now: Date): LanedTask[] {
  const withTimes = tasks
    .map((t) => ({
      task: t,
      start: new Date(t.started_at).getTime(),
      end: (t.completed_at ? new Date(t.completed_at).getTime() : now.getTime()),
    }))
    .sort((a, b) => a.start - b.start);

  // Greedy interval scheduling: each task takes the first lane whose last
  // task already ended before this one starts; otherwise open a new lane.
  const laneEndTimes: number[] = [];
  const placed = withTimes.map(({ task, start, end }) => {
    let lane = laneEndTimes.findIndex((endTime) => endTime <= start);
    if (lane === -1) {
      lane = laneEndTimes.length;
      laneEndTimes.push(end);
    } else {
      laneEndTimes[lane] = end;
    }
    return { task, lane };
  });
  const laneCount = Math.max(1, laneEndTimes.length);
  return placed.map(({ task, lane }) => ({ ...task, lane, laneCount }));
}

function DayAgenda({
  agent, dayStart, now, isToday,
}: { agent: ActivityAgentTimeline; dayStart: Date; now: Date; isToday: boolean }) {
  const router = useRouter();
  const scrollRef = useRef<HTMLDivElement>(null);
  // Was der Agent sich VORGENOMMEN hat — die Gegenprobe zu den Task-Balken, die nur
  // zeigen, was schon gelaufen ist. Ohne das bleibt "was hat das Ding heute vor?"
  // unbeantwortbar (der Plan lag bisher nur als Datei im Container).
  const [plan, setPlan] = useState<DayPlanItem[]>([]);
  const planDate = useMemo(() => localDateKey(dayStart), [dayStart]);
  const loadPlan = useCallback(async () => {
    try {
      const res = await api.getDayPlan(agent.agent_id, planDate);
      setPlan(res.items);
    } catch {
      setPlan([]);   // kein Plan ist ein gueltiger Zustand, kein Fehlerzustand
    }
  }, [agent.agent_id, planDate]);
  useEffect(() => { loadPlan(); }, [loadPlan]);
  const undatedPlan = useMemo(() => plan.filter((p) => !p.planned_start), [plan]);

  const dropBlock = async (item: DayPlanItem) => {
    // Streichen statt loeschen: der Tag bleibt nachvollziehbar, und der Agent sieht
    // beim naechsten Lauf, dass dieser Block vom Tisch ist.
    const next = item.status === "dropped" ? "planned" : "dropped";
    try {
      await api.patchDayPlanItem(item.id, { status: next });
      await loadPlan();
    } catch {
      // stiller Fehlschlag: der naechste Load holt den echten Stand
    }
  };
  // Ein Plan-Block laeuft ueber einen Zeitplan — der taucht sonst NOCH EINMAL als
  // Balken und als Rautenmarke auf. Der Block links ist die Wahrheit; alles mit
  // '[Plan]' wird hier ausgeblendet, sonst steht dieselbe Sache dreifach im Tag.
  const ownTasks = useMemo(
    () => agent.tasks.filter((t) => !t.title?.includes("[Plan]")),
    [agent.tasks],
  );
  const ownMarks = useMemo(
    () => agent.scheduled_marks.filter((m) => !m.schedule_name?.startsWith("[Plan]")),
    [agent.scheduled_marks],
  );
  const laned = useMemo(() => layoutLanes(ownTasks, now), [ownTasks, now]);
  const nowOffsetPx = isToday ? ((now.getTime() - dayStart.getTime()) / DAY_MS) * 24 * HOUR_PX : null;

  // Open the agenda scrolled to something useful instead of dumping the user
  // at midnight: "now" for today, else the first hour that actually has
  // something on it, else a plain business-hours default.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    let targetHour = 7;
    if (isToday) {
      targetHour = now.getHours();
    } else if (ownTasks.length > 0) {
      const earliest = ownTasks.reduce(
        (min, t) => Math.min(min, new Date(t.started_at).getHours()), 24
      );
      if (earliest < 24) targetHour = earliest;
    }
    el.scrollTop = Math.max(0, targetHour - 1) * HOUR_PX;
  }, [agent.agent_id, dayStart, isToday]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div
      ref={scrollRef}
      className="max-h-[calc(100vh-260px)] min-h-[400px] overflow-y-auto rounded-lg border border-foreground/[0.06]"
    >
      <div className="relative flex" style={{ height: 24 * HOUR_PX }}>
        <div className="sticky left-0 w-14 shrink-0 border-r border-foreground/[0.06] bg-background">
          {Array.from({ length: 24 }).map((_, h) => (
            <div
              key={h}
              className="relative text-right text-[11px] text-muted-foreground/50"
              style={{ height: HOUR_PX }}
            >
              <span className="absolute -top-2 right-2">{String(h).padStart(2, "0")}:00</span>
            </div>
          ))}
        </div>

        <div className="relative flex-1">
          {Array.from({ length: 24 }).map((_, h) => (
            <div
              key={h}
              className="absolute inset-x-0 border-t border-foreground/[0.05]"
              style={{ top: h * HOUR_PX }}
            />
          ))}

          {/* Geplante Bloecke: gestrichelt und halbtransparent, damit auf einen Blick
              klar ist, was VORHABEN ist und was tatsaechlich gelaufen ist. Sie liegen
              in einer eigenen schmalen Spur links, damit sie die Task-Balken nicht
              verdecken. */}
          {plan.map((item) => {
            const startMs = item.planned_start
              ? new Date(item.planned_start).getTime() - dayStart.getTime()
              : null;
            const top = startMs === null
              ? null
              : (Math.min(Math.max(startMs, 0), DAY_MS) / DAY_MS) * 24 * HOUR_PX + BLOCK_GAP_PX / 2;
            if (top === null) return null;   // ohne Zeit: unterhalb als Liste, s.u.
            const height = Math.max(
              ((item.estimated_minutes * 60_000) / DAY_MS) * 24 * HOUR_PX - BLOCK_GAP_PX,
              MIN_BLOCK_PX
            );
            const dropped = item.status === "dropped";
            return (
              <div
                key={`plan-${item.id}`}
                role={item.task_id ? "button" : undefined}
                onClick={item.task_id ? () => router.push(`/tasks/${item.task_id}`) : undefined}
                title={
                  item.task_id
                    ? `${statusLabel(item.status)}: ${item.title} — klicken für Ergebnis und Dateien`
                    : `Geplant: ${item.title}${item.notes ? ` — ${item.notes}` : ""}`
                }
                className={cn(
                  "group absolute left-0 w-[26%] overflow-hidden rounded-md border px-2 py-1",
                  item.status === "done" ? "border-solid" : "border-dashed",
                  item.task_id && "cursor-pointer hover:opacity-80",
                  item.status === "running" && "animate-pulse",
                  dropped
                    ? "border-foreground/15 bg-foreground/[0.02] opacity-50"
                    : "border-sky-400/40 bg-sky-400/[0.07]",
                  item.status === "done" && "border-emerald-400/40 bg-emerald-400/[0.07]"
                )}
                style={{ top, height }}
              >
                <div className={cn(
                  "flex items-start gap-1 text-[11px] font-medium",
                  dropped ? "text-muted-foreground/60 line-through" : "text-sky-200"
                )}>
                  <CalendarClock className="mt-[2px] h-3 w-3 shrink-0 opacity-70" />
                  <span className="truncate">{item.title}</span>
                </div>
                {height >= 30 && (
                  <div className="truncate text-[10px] text-muted-foreground/60">
                    {statusLabel(item.status)} · {item.estimated_minutes} Min
                    {item.task_id && " · Ergebnis ansehen"}
                  </div>
                )}
                <button
                  type="button"
                  onClick={() => dropBlock(item)}
                  title={dropped ? "Wieder einplanen" : "Streichen — der Agent lässt es dann liegen"}
                  className="absolute right-1 top-1 hidden rounded p-0.5 text-muted-foreground/50 hover:bg-foreground/10 hover:text-foreground group-hover:block"
                >
                  <X className="h-3 w-3" />
                </button>
              </div>
            );
          })}

          {ownMarks.map((m: ActivityScheduleMark, i) => {
            const top = ((new Date(m.time).getTime() - dayStart.getTime()) / DAY_MS) * 24 * HOUR_PX;
            return (
              <div
                key={`${m.schedule_id}-${i}`}
                title={`${m.schedule_name} — ${fmtTime(m.time)}`}
                className="absolute left-1 h-2 w-2 -translate-y-1/2 rotate-45 border border-foreground/30 bg-background"
                style={{ top }}
              />
            );
          })}

          {laned.map((t) => {
            // Clamp to [0, DAY_MS]: a task that started yesterday (still running
            // past midnight) would otherwise get a negative `top` and render
            // partly or entirely above/below the visible grid — showing LESS of
            // it than its true duration, not more. Clamping shows exactly the
            // portion that falls on this calendar day, anchored correctly at
            // 00:00/24:00 instead of drifting off-grid.
            const startedMs = Math.min(
              Math.max(new Date(t.started_at).getTime() - dayStart.getTime(), 0),
              DAY_MS
            );
            const endedMsRaw = (t.completed_at ? new Date(t.completed_at).getTime() : now.getTime()) - dayStart.getTime();
            const endedMs = Math.min(Math.max(endedMsRaw, 0), DAY_MS);
            const top = (startedMs / DAY_MS) * 24 * HOUR_PX + BLOCK_GAP_PX / 2;
            const height = Math.max(
              ((endedMs - startedMs) / DAY_MS) * 24 * HOUR_PX - BLOCK_GAP_PX,
              MIN_BLOCK_PX
            );
            const laneWidthPct = 100 / t.laneCount;
            const costSuffix = t.cost_usd != null ? ` — ${formatCost(t.cost_usd)}` : "";
            const durationSuffix = t.duration_ms ? ` — ${formatDuration(t.duration_ms)}` : "";
            const timeRange = t.completed_at
              ? `${fmtTime(t.started_at)}–${fmtTime(t.completed_at)}`
              : `${fmtTime(t.started_at)}–läuft`;
            return (
              <button
                type="button"
                key={t.task_id}
                onClick={() => router.push(`/tasks/${t.task_id}`)}
                className={cn(
                  "absolute overflow-hidden rounded-md border-l-2 px-2 py-1 text-left transition-opacity hover:z-10 hover:opacity-80",
                  statusAccent[t.status] || "border-l-foreground/30 bg-foreground/10",
                  t.status === "running" && "animate-pulse"
                )}
                style={{
                  top,
                  height,
                  // Die Planspur belegt die linken 26 % — die Aufgaben teilen sich den Rest.
                  left: `calc(28% + ${t.lane * laneWidthPct * 0.72}% + 2px)`,
                  width: `calc(${laneWidthPct * 0.72}% - 4px)`,
                }}
              >
                <div className="truncate text-[12px] font-medium text-foreground">{t.title}</div>
                {height >= 32 && (
                  <div className="truncate text-[10px] text-muted-foreground/70">
                    {timeRange} · {t.status}{durationSuffix}{costSuffix}
                  </div>
                )}
              </button>
            );
          })}

          {nowOffsetPx !== null && (
            <div className="absolute inset-x-0 z-10 flex items-center" style={{ top: nowOffsetPx }}>
              <div className="h-2 w-2 rounded-full bg-primary" />
              <div className="h-px flex-1 bg-primary/70" />
            </div>
          )}
        </div>
      </div>

      {/* Vorgenommenes OHNE feste Uhrzeit — es hat im Raster keinen Platz, darf aber
          nicht verschwinden: sonst faellt genau das unter den Tisch, was der Agent
          "heute noch" erledigen wollte. */}
      {undatedPlan.length > 0 && (
        <div className="border-t border-foreground/[0.06] px-3 py-2">
          <div className="mb-1 flex items-center gap-1.5 text-[10px] uppercase tracking-wide text-muted-foreground/40">
            <CalendarClock className="h-3 w-3" />
            Heute vorgenommen, ohne feste Zeit
          </div>
          <div className="space-y-1">
            {undatedPlan.map((item) => (
              <div key={`undated-${item.id}`} className="group flex items-center gap-2 text-[11px]">
                <span className={cn(
                  "flex-1 truncate",
                  item.status === "dropped" ? "text-muted-foreground/50 line-through" : "text-foreground/80"
                )}>
                  {item.title}
                </span>
                <span className="shrink-0 text-[10px] text-muted-foreground/40">
                  {item.estimated_minutes} Min
                </span>
                <button
                  type="button"
                  onClick={() => dropBlock(item)}
                  title={item.status === "dropped" ? "Wieder einplanen" : "Streichen"}
                  className="shrink-0 rounded p-0.5 text-muted-foreground/40 opacity-0 transition-opacity hover:bg-foreground/10 hover:text-foreground group-hover:opacity-100"
                >
                  <X className="h-3 w-3" />
                </button>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

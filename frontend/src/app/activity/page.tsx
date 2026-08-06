"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { CalendarDays, ChevronLeft, ChevronRight, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { formatCost, formatDuration } from "@/lib/utils";
import * as api from "@/lib/api";
import type { ActivityAgentTimeline } from "@/lib/types";

const DAY_MS = 24 * 60 * 60 * 1000;
const HOUR_MARKS = [0, 6, 12, 18, 24];

const statusStyle: Record<string, string> = {
  running: "bg-blue-500/70 border-blue-400",
  completed: "bg-emerald-500/70 border-emerald-400",
  failed: "bg-red-500/70 border-red-400",
  cancelled: "bg-zinc-500/50 border-zinc-400",
  pending: "bg-amber-500/60 border-amber-400",
  queued: "bg-amber-500/60 border-amber-400",
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

export default function ActivityPage() {
  const router = useRouter();
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
      const res = await api.getActivityTimeline(dayStart, dayEnd);
      setAgents(res.agents);
    } catch {
      // keep showing the last good data on a transient fetch error
    }
    setLoading(false);
  }, [dayStart, dayEnd]);

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

  const nowPct = isToday ? pct(now.getTime() - dayStart.getTime()) : null;

  return (
    <div className="px-8 py-8 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Activity</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            What every agent has planned and what it actually did, one day at a time
          </p>
        </div>
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

      {/* Hour scale, aligned with the timeline tracks below (11rem label + 0.75rem gap) */}
      <div className="flex pl-[calc(11rem+0.75rem)] pr-1 text-[10px] text-muted-foreground/40">
        {HOUR_MARKS.map((h, i) => (
          <div
            key={h}
            className={cn("flex-1", i === HOUR_MARKS.length - 1 ? "text-right" : "text-left")}
          >
            {String(h).padStart(2, "0")}:00
          </div>
        ))}
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-24 text-muted-foreground">
          <Loader2 className="h-5 w-5 animate-spin" />
        </div>
      ) : agents.length === 0 ? (
        <div className="rounded-2xl border border-foreground/[0.06] bg-card/50 py-16 text-center text-sm text-muted-foreground">
          No agents visible.
        </div>
      ) : (
        <div className="space-y-2">
          {agents.map((a) => (
            <div key={a.agent_id} className="flex items-center gap-3">
              <div className="w-44 shrink-0 truncate text-sm font-medium" title={a.name}>
                {a.name}
              </div>
              <div className="relative h-10 flex-1 overflow-hidden rounded-lg border border-foreground/[0.06] bg-foreground/[0.02]">
                {HOUR_MARKS.slice(1, -1).map((h) => (
                  <div
                    key={h}
                    className="absolute bottom-0 top-0 w-px bg-foreground/[0.04]"
                    style={{ left: `${(h / 24) * 100}%` }}
                  />
                ))}

                {a.tasks.map((t) => {
                  const startedMs = new Date(t.started_at).getTime() - dayStart.getTime();
                  const endedMs =
                    (t.completed_at ? new Date(t.completed_at).getTime() : now.getTime()) -
                    dayStart.getTime();
                  const left = pct(startedMs);
                  const width = Math.max(pct(endedMs) - left, 0.6);
                  const costSuffix = t.cost_usd != null ? ` — ${formatCost(t.cost_usd)}` : "";
                  const durationSuffix = t.duration_ms ? ` — ${formatDuration(t.duration_ms)}` : "";
                  return (
                    <button
                      type="button"
                      key={t.task_id}
                      onClick={() => router.push(`/tasks/${t.task_id}`)}
                      title={`${t.title} — ${t.status}${durationSuffix}${costSuffix}`}
                      className={cn(
                        "absolute bottom-1 top-1 rounded-md border transition-opacity hover:opacity-80",
                        statusStyle[t.status] || "border-foreground/30 bg-foreground/20",
                        t.status === "running" && "animate-pulse"
                      )}
                      style={{ left: `${left}%`, width: `${width}%` }}
                    />
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
                  <div
                    className="absolute bottom-0 top-0 w-px bg-primary/70"
                    style={{ left: `${nowPct}%` }}
                  />
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

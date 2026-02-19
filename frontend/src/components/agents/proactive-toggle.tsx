"use client";

import { useCallback, useEffect, useState } from "react";
import { Activity, Clock, Loader2, Zap } from "lucide-react";
import { cn } from "@/lib/utils";
import { getProactiveConfig, updateProactiveConfig } from "@/lib/api";
import type { ProactiveResponse } from "@/lib/types";

const INTERVALS = [
  { label: "15 min", seconds: 900 },
  { label: "30 min", seconds: 1800 },
  { label: "1h", seconds: 3600 },
  { label: "2h", seconds: 7200 },
  { label: "4h", seconds: 14400 },
  { label: "8h", seconds: 28800 },
];

interface ProactiveToggleProps {
  agentId: string;
}

export function ProactiveToggle({ agentId }: ProactiveToggleProps) {
  const [data, setData] = useState<ProactiveResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [toggling, setToggling] = useState(false);

  const load = useCallback(async () => {
    try {
      const res = await getProactiveConfig(agentId);
      setData(res);
    } catch {
      // ignore
    }
    setLoading(false);
  }, [agentId]);

  useEffect(() => {
    load();
  }, [load]);

  const handleToggle = async () => {
    if (!data) return;
    setToggling(true);
    try {
      const newEnabled = !data.proactive?.enabled;
      await updateProactiveConfig(agentId, {
        enabled: newEnabled,
        interval_seconds: data.proactive?.interval_seconds || 3600,
      });
      await load();
    } catch {
      // ignore
    }
    setToggling(false);
  };

  const handleIntervalChange = async (seconds: number) => {
    if (!data) return;
    try {
      await updateProactiveConfig(agentId, {
        enabled: data.proactive?.enabled ?? true,
        interval_seconds: seconds,
      });
      await load();
    } catch {
      // ignore
    }
  };

  if (loading) return null;

  const enabled = data?.proactive?.enabled ?? false;
  const interval = data?.proactive?.interval_seconds ?? 3600;
  const schedule = data?.schedule;

  const formatTime = (iso: string | null) => {
    if (!iso) return "never";
    const d = new Date(iso);
    const now = new Date();
    const diffMs = now.getTime() - d.getTime();
    const diffMin = Math.floor(diffMs / 60000);
    if (diffMin < 0) {
      const absMin = Math.abs(diffMin);
      if (absMin < 60) return `in ${absMin}m`;
      return `in ${Math.floor(absMin / 60)}h`;
    }
    if (diffMin < 1) return "just now";
    if (diffMin < 60) return `${diffMin}m ago`;
    const diffH = Math.floor(diffMin / 60);
    if (diffH < 24) return `${diffH}h ago`;
    return d.toLocaleDateString();
  };

  return (
    <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className={cn(
            "flex h-8 w-8 items-center justify-center rounded-lg transition-colors",
            enabled ? "bg-emerald-500/10" : "bg-foreground/[0.04]"
          )}>
            <Zap className={cn(
              "h-4 w-4 transition-colors",
              enabled ? "text-emerald-400" : "text-muted-foreground/40"
            )} />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium">Proactive Mode</span>
              {enabled && (
                <span className="flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                  <span className="relative flex h-1.5 w-1.5">
                    <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
                    <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-emerald-500" />
                  </span>
                  Active
                </span>
              )}
            </div>
            <p className="text-[11px] text-muted-foreground/60">
              Agent checks periodically for work to do on its own
            </p>
          </div>
        </div>

        {/* Toggle switch */}
        <button
          onClick={handleToggle}
          disabled={toggling}
          className={cn(
            "relative inline-flex h-6 w-11 items-center rounded-full transition-colors",
            enabled ? "bg-emerald-500" : "bg-foreground/[0.1]"
          )}
        >
          {toggling ? (
            <Loader2 className="h-3 w-3 animate-spin mx-auto text-white" />
          ) : (
            <span
              className={cn(
                "inline-block h-4 w-4 rounded-full bg-white shadow-sm transition-transform",
                enabled ? "translate-x-6" : "translate-x-1"
              )}
            />
          )}
        </button>
      </div>

      {/* Interval + stats (only when enabled) */}
      {enabled && (
        <div className="mt-3 pt-3 border-t border-foreground/[0.04] space-y-2">
          {/* Interval selector */}
          <div className="flex items-center gap-2">
            <Clock className="h-3 w-3 text-muted-foreground/40" />
            <span className="text-[11px] text-muted-foreground/60">Check every:</span>
            <div className="flex gap-1">
              {INTERVALS.map((opt) => (
                <button
                  key={opt.seconds}
                  onClick={() => handleIntervalChange(opt.seconds)}
                  className={cn(
                    "px-2 py-0.5 rounded text-[10px] font-medium transition-colors",
                    interval === opt.seconds
                      ? "bg-foreground/[0.08] text-foreground"
                      : "text-muted-foreground/50 hover:text-foreground hover:bg-foreground/[0.04]"
                  )}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          </div>

          {/* Stats row */}
          {schedule && (
            <div className="flex items-center gap-4 text-[10px] text-muted-foreground/50">
              <span className="flex items-center gap-1">
                <Activity className="h-2.5 w-2.5" />
                {schedule.total_runs} runs
              </span>
              {schedule.last_run_at && (
                <span>Last: {formatTime(schedule.last_run_at)}</span>
              )}
              {schedule.next_run_at && (
                <span>Next: {formatTime(schedule.next_run_at)}</span>
              )}
              {schedule.total_runs > 0 && (
                <span className={cn(
                  schedule.success_count / schedule.total_runs >= 0.8
                    ? "text-emerald-400/60"
                    : "text-amber-400/60"
                )}>
                  {Math.round((schedule.success_count / schedule.total_runs) * 100)}% success
                </span>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

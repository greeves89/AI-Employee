"use client";

import Link from "next/link";
import { CheckCircle2, XCircle, Clock, Loader2, ArrowRight } from "lucide-react";
import type { Task } from "@/lib/types";
import { cn } from "@/lib/utils";
import { formatDuration, timeAgo } from "@/lib/utils";

const statusIcons: Record<string, { icon: typeof CheckCircle2; color: string }> = {
  completed: { icon: CheckCircle2, color: "text-emerald-400" },
  failed: { icon: XCircle, color: "text-red-400" },
  running: { icon: Loader2, color: "text-blue-400" },
  queued: { icon: Clock, color: "text-amber-400" },
  pending: { icon: Clock, color: "text-muted-foreground" },
  cancelled: { icon: XCircle, color: "text-zinc-400" },
};

interface RecentTasksProps {
  tasks: Task[];
}

export function RecentTasks({ tasks }: RecentTasksProps) {
  const recent = tasks.slice(0, 8);

  return (
    <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-5 py-3.5 border-b border-foreground/[0.06]">
        <div className="flex items-center gap-2">
          <div className="h-2 w-2 rounded-full bg-emerald-500 animate-pulse" />
          <span className="text-sm font-semibold tracking-tight">Recent Activity</span>
        </div>
        <Link
          href="/tasks"
          className="flex items-center gap-1 text-[12px] text-muted-foreground hover:text-foreground transition-colors"
        >
          All tasks <ArrowRight className="h-3 w-3" />
        </Link>
      </div>

      {/* Task list */}
      {recent.length === 0 ? (
        <div className="px-5 py-8 text-center text-sm text-muted-foreground">
          No tasks yet. Create one to get started.
        </div>
      ) : (
        <div className="divide-y divide-foreground/[0.04]">
          {recent.map((task) => {
            const statusCfg = statusIcons[task.status] ?? statusIcons.pending;
            const Icon = statusCfg.icon;
            return (
              <div
                key={task.id}
                className="flex items-center gap-4 px-5 py-3 hover:bg-foreground/[0.02] transition-colors"
              >
                <div className={cn("shrink-0", statusCfg.color)}>
                  <Icon className={cn("h-4 w-4", task.status === "running" && "animate-spin")} />
                </div>

                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium truncate">{task.title}</p>
                  <p className="text-[11px] text-muted-foreground/70 truncate">
                    {task.prompt.slice(0, 80)}
                  </p>
                </div>

                <div className="shrink-0 flex items-center gap-4 text-[11px] text-muted-foreground tabular-nums">
                  {task.duration_ms && (
                    <span>{formatDuration(task.duration_ms)}</span>
                  )}
                  {task.num_turns && (
                    <span>{task.num_turns}t</span>
                  )}
                  <span className="w-14 text-right">{timeAgo(task.created_at)}</span>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

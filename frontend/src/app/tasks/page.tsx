"use client";

import { useState } from "react";
import Link from "next/link";
import { motion } from "framer-motion";
import { Plus, CheckCircle2, XCircle, Clock, Loader2, RotateCcw, Timer, Hash, Cpu } from "lucide-react";
import { useTasks } from "@/hooks/use-tasks";
import { Header } from "@/components/layout/header";
import { formatDuration, formatCost, timeAgo } from "@/lib/utils";
import { cn } from "@/lib/utils";
import * as api from "@/lib/api";

const statusConfig: Record<string, { icon: typeof CheckCircle2; badge: string; color: string }> = {
  pending: { icon: Clock, badge: "bg-amber-500/10 text-amber-400 border-amber-500/20", color: "text-amber-400" },
  queued: { icon: Clock, badge: "bg-blue-500/10 text-blue-400 border-blue-500/20", color: "text-blue-400" },
  running: { icon: Loader2, badge: "bg-blue-500/10 text-blue-400 border-blue-500/20", color: "text-blue-400" },
  completed: { icon: CheckCircle2, badge: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20", color: "text-emerald-400" },
  failed: { icon: XCircle, badge: "bg-red-500/10 text-red-400 border-red-500/20", color: "text-red-400" },
  cancelled: { icon: XCircle, badge: "bg-zinc-500/10 text-zinc-400 border-zinc-500/20", color: "text-zinc-400" },
};

const filterTabs = [
  { key: "all", label: "All" },
  { key: "running", label: "Running" },
  { key: "queued", label: "Queued" },
  { key: "completed", label: "Completed" },
  { key: "failed", label: "Failed" },
];

export default function TasksPage() {
  const { tasks, loading } = useTasks();
  const [filter, setFilter] = useState<string>("all");

  const filteredTasks =
    filter === "all" ? tasks : tasks.filter((t) => t.status === filter);

  const retryTask = async (task: { title: string; prompt: string; agent_id: string | null; model: string | null }) => {
    try {
      await api.createTask({
        title: task.title,
        prompt: task.prompt,
        agent_id: task.agent_id || undefined,
        model: task.model || undefined,
      });
    } catch {
      // ignore
    }
  };

  return (
    <div>
      <Header
        title="Tasks"
        subtitle="All tasks across agents"
        actions={
          <Link
            href="/tasks/new"
            className="inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground shadow-lg shadow-primary/20 hover:bg-primary/90 transition-all duration-200"
          >
            <Plus className="h-4 w-4" />
            New Task
          </Link>
        }
      />

      <motion.div
        className="px-8 py-8"
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
      >
        {/* Filter tabs */}
        <div className="mb-6 flex gap-1 p-1 rounded-xl bg-foreground/[0.03] border border-foreground/[0.06] w-fit">
          {filterTabs.map((tab) => (
            <button
              key={tab.key}
              onClick={() => setFilter(tab.key)}
              className={cn(
                "rounded-lg px-4 py-2 text-xs font-medium transition-all duration-150",
                filter === tab.key
                  ? "bg-foreground/[0.08] text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04]"
              )}
            >
              {tab.label}
              {tab.key !== "all" && (
                <span className="ml-1.5 tabular-nums text-muted-foreground/60">
                  {tasks.filter((t) => t.status === tab.key).length}
                </span>
              )}
            </button>
          ))}
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
            No tasks {filter !== "all" ? `with status "${filter}"` : "yet"}.
          </div>
        ) : (
          <div className="space-y-2">
            {filteredTasks.map((task, i) => {
              const cfg = statusConfig[task.status] ?? statusConfig.pending;
              const Icon = cfg.icon;
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
                        <h4 className="font-medium text-sm truncate">{task.title}</h4>
                        <span className={cn(
                          "shrink-0 inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-medium",
                          cfg.badge
                        )}>
                          {task.status}
                        </span>
                      </div>
                      <p className="text-[12px] text-muted-foreground/70 mt-0.5 line-clamp-1">
                        {task.prompt}
                      </p>
                    </div>

                    {/* Actions + Meta */}
                    <div className="shrink-0 flex items-center gap-3">
                      {task.status === "failed" && (
                        <button
                          onClick={(e) => { e.preventDefault(); retryTask(task); }}
                          className="inline-flex items-center gap-1.5 rounded-lg bg-orange-500/10 border border-orange-500/20 px-3 py-1.5 text-[11px] font-medium text-orange-400 hover:bg-orange-500/20 transition-colors"
                        >
                          <RotateCcw className="h-3 w-3" />
                          Retry
                        </button>
                      )}
                    </div>
                  </div>

                  {/* Meta row */}
                  <div className="mt-2.5 ml-9 flex items-center gap-4 text-[11px] text-muted-foreground/60">
                    <span className="flex items-center gap-1 font-mono">
                      <Hash className="h-3 w-3" />{task.id.slice(0, 8)}
                    </span>
                    {task.agent_id && (
                      <span className="flex items-center gap-1">
                        <Cpu className="h-3 w-3" />{task.agent_id}
                      </span>
                    )}
                    {task.duration_ms && (
                      <span className="flex items-center gap-1 tabular-nums">
                        <Timer className="h-3 w-3" />{formatDuration(task.duration_ms)}
                      </span>
                    )}
                    {task.num_turns && (
                      <span className="tabular-nums">{task.num_turns} turns</span>
                    )}
                    {task.cost_usd ? (
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
    </div>
  );
}

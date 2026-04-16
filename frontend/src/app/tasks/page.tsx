"use client";

import { useState, useEffect, useCallback } from "react";
import Link from "next/link";
import { motion } from "framer-motion";
import {
  Plus, CheckCircle2, XCircle, Clock, Loader2, RotateCcw, Timer,
  Hash, Cpu, Trash2, Ban, Pause, Play, PlayCircle, CalendarClock, Sparkles,
  GitBranch,
} from "lucide-react";
import { useTasks } from "@/hooks/use-tasks";
import { useAgents } from "@/hooks/use-agents";
import { Header } from "@/components/layout/header";
import { formatDuration, formatCost, timeAgo } from "@/lib/utils";
import { cn } from "@/lib/utils";
import * as api from "@/lib/api";
import type { Schedule } from "@/lib/types";

/* ─── Single Tasks Config ─────────────────────────────────────────── */

const statusConfig: Record<string, { icon: typeof CheckCircle2; badge: string; color: string }> = {
  pending: { icon: Clock, badge: "bg-amber-500/10 text-amber-400 border-amber-500/20", color: "text-amber-400" },
  queued: { icon: Clock, badge: "bg-blue-500/10 text-blue-400 border-blue-500/20", color: "text-blue-400" },
  running: { icon: Loader2, badge: "bg-blue-500/10 text-blue-400 border-blue-500/20", color: "text-blue-400" },
  completed: { icon: CheckCircle2, badge: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20", color: "text-emerald-400" },
  failed: { icon: XCircle, badge: "bg-red-500/10 text-red-400 border-red-500/20", color: "text-red-400" },
  cancelled: { icon: Ban, badge: "bg-zinc-500/10 text-zinc-400 border-zinc-500/20", color: "text-zinc-400" },
};

const filterTabs = [
  { key: "active", label: "Active" },
  { key: "all", label: "All" },
  { key: "completed", label: "Completed" },
  { key: "failed", label: "Failed" },
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
  if (seconds < 3600) return `Every ${Math.round(seconds / 60)} min`;
  if (seconds < 86400) {
    const h = Math.round(seconds / 3600);
    return `Every ${h} hour${h > 1 ? "s" : ""}`;
  }
  const d = Math.round(seconds / 86400);
  return `Every ${d} day${d > 1 ? "s" : ""}`;
}

function formatRelative(dateStr: string | null): string {
  if (!dateStr) return "Never";
  const date = new Date(dateStr);
  const now = new Date();
  const diffMs = date.getTime() - now.getTime();
  const absDiff = Math.abs(diffMs);

  if (absDiff < 60000) return diffMs > 0 ? "in < 1 min" : "< 1 min ago";
  if (absDiff < 3600000) {
    const m = Math.round(absDiff / 60000);
    return diffMs > 0 ? `in ${m} min` : `${m} min ago`;
  }
  if (absDiff < 86400000) {
    const h = Math.round(absDiff / 3600000);
    return diffMs > 0 ? `in ${h}h` : `${h}h ago`;
  }
  return date.toLocaleDateString();
}

const INTERVAL_PRESETS = [
  { label: "5 min", seconds: 300 },
  { label: "15 min", seconds: 900 },
  { label: "30 min", seconds: 1800 },
  { label: "1 hour", seconds: 3600 },
  { label: "6 hours", seconds: 21600 },
  { label: "12 hours", seconds: 43200 },
  { label: "24 hours", seconds: 86400 },
];

/* ─── Main Page ────────────────────────────────────────────────────── */

type ViewMode = "single" | "scheduled";

export default function TasksPage() {
  const [viewMode, setViewMode] = useState<ViewMode>("single");

  return (
    <div>
      <Header
        title="Tasks"
        subtitle={viewMode === "single" ? "All tasks across agents" : "Recurring tasks that run automatically"}
        actions={
          viewMode === "single" ? (
            <Link
              href="/tasks/new"
              className="inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground shadow-lg shadow-primary/20 hover:bg-primary/90 transition-all duration-200"
            >
              <Plus className="h-4 w-4" />
              New Task
            </Link>
          ) : null
        }
      />

      <div className="px-8 py-6">
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
            Single Tasks
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
              Scheduled
            </span>
          </button>
        </div>

        {viewMode === "single" ? <SingleTasksView /> : <ScheduledTasksView />}
      </div>
    </div>
  );
}

/* ─── Single Tasks View ────────────────────────────────────────────── */

function SingleTasksView() {
  const { tasks, loading, refresh } = useTasks();
  const [filter, setFilter] = useState<string>("active");
  const [deleting, setDeleting] = useState<Set<string>>(new Set());

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
      <div className="mb-6 flex gap-1 p-1 rounded-xl bg-foreground/[0.03] border border-foreground/[0.06] w-fit">
        {filterTabs.map((tab) => {
          const count = tab.key === "active"
            ? activeCount
            : tab.key === "all"
            ? tasks.length
            : tasks.filter((t) => t.status === tab.key).length;
          return (
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
            ? "No active tasks. All tasks are completed or idle."
            : filter === "all"
            ? "No tasks yet."
            : `No ${filter} tasks.`}
        </div>
      ) : (
        <div className="space-y-2">
          {filteredTasks.map((task, i) => {
            const cfg = statusConfig[task.status] ?? statusConfig.pending;
            const Icon = cfg.icon;
            const canDelete = task.status !== "running";
            const canCancel = task.status === "queued" || task.status === "pending";
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
                        {task.status}
                      </span>
                      {task.parent_task_id && (
                        <span className="shrink-0 inline-flex items-center rounded-full border border-blue-500/20 bg-blue-500/10 px-2 py-0.5 text-[10px] font-medium text-blue-400">
                          Subtask
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
                        Retry
                      </button>
                    )}
                    {canCancel && (
                      <button
                        onClick={(e) => handleCancel(e, task.id)}
                        className="inline-flex items-center gap-1.5 rounded-lg bg-amber-500/10 border border-amber-500/20 px-3 py-1.5 text-[11px] font-medium text-amber-400 hover:bg-amber-500/20 transition-colors opacity-0 group-hover:opacity-100"
                        title="Cancel task"
                      >
                        <Ban className="h-3 w-3" />
                        Cancel
                      </button>
                    )}
                    {canDelete && (
                      <button
                        onClick={(e) => handleDelete(e, task.id)}
                        disabled={deleting.has(task.id)}
                        className="inline-flex items-center rounded-lg p-1.5 text-muted-foreground/40 hover:text-red-400 hover:bg-red-500/10 transition-colors opacity-0 group-hover:opacity-100 disabled:opacity-50"
                        title="Delete task"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    )}
                  </div>
                </div>

                {/* Meta row */}
                <div className="mt-2.5 ml-9 flex items-center gap-4 text-[11px] text-muted-foreground/60">
                  <span className="flex items-center gap-1 font-mono">
                    <Hash className="h-3 w-3" />{task.id.slice(0, 8)}
                  </span>
                  {task.parent_task_id && (
                    <span className="flex items-center gap-1 text-blue-400/60">
                      <GitBranch className="h-3 w-3" />parent: {task.parent_task_id.slice(0, 8)}
                    </span>
                  )}
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
  );
}

/* ─── Scheduled Tasks View ─────────────────────────────────────────── */

function ScheduledTasksView() {
  const [schedules, setSchedules] = useState<Schedule[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [creating, setCreating] = useState(false);
  const [triggering, setTriggering] = useState<string | null>(null);
  const { agents } = useAgents();

  // Create form state
  const [name, setName] = useState("");
  const [prompt, setPrompt] = useState("");
  const [intervalSeconds, setIntervalSeconds] = useState(3600);
  const [priority, setPriority] = useState(1);
  const [agentId, setAgentId] = useState("");

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
    const interval = setInterval(refresh, 10000);
    return () => clearInterval(interval);
  }, [refresh]);

  const handleCreate = async () => {
    if (!name.trim() || !prompt.trim()) return;
    setCreating(true);
    try {
      await api.createSchedule({
        name: name.trim(),
        prompt: prompt.trim(),
        interval_seconds: intervalSeconds,
        priority,
        agent_id: agentId || undefined,
      });
      setName("");
      setPrompt("");
      setIntervalSeconds(3600);
      setPriority(1);
      setAgentId("");
      setShowCreate(false);
      await refresh();
    } finally {
      setCreating(false);
    }
  };

  const handleToggle = async (schedule: Schedule) => {
    if (schedule.enabled) {
      await api.pauseSchedule(schedule.id);
    } else {
      await api.resumeSchedule(schedule.id);
    }
    await refresh();
  };

  const handleTrigger = async (id: string) => {
    setTriggering(id);
    try {
      await api.triggerSchedule(id);
      await refresh();
    } finally {
      setTriggering(null);
    }
  };

  const handleDelete = async (id: string) => {
    await api.deleteSchedule(id);
    await refresh();
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
          New Schedule
        </button>
      </div>

      {/* Create Form */}
      {showCreate && (
        <motion.div
          initial={{ opacity: 0, height: 0 }}
          animate={{ opacity: 1, height: "auto" }}
          exit={{ opacity: 0, height: 0 }}
          className="overflow-hidden rounded-2xl border border-foreground/[0.06] bg-card/80 p-6 backdrop-blur-sm"
        >
          <div className="mb-4 flex items-center gap-2">
            <Sparkles className="h-4 w-4 text-primary" />
            <h3 className="text-sm font-semibold">Create Recurring Task</h3>
          </div>
          <div className="space-y-4">
            <div>
              <label className="mb-1.5 block text-xs font-medium text-muted-foreground">
                Name
              </label>
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. Daily Code Review"
                className="w-full rounded-xl border border-foreground/[0.06] bg-foreground/[0.03] px-4 py-2.5 text-sm placeholder:text-muted-foreground/40 focus:border-primary/50 focus:outline-none focus:ring-1 focus:ring-primary/25"
              />
            </div>
            <div>
              <label className="mb-1.5 block text-xs font-medium text-muted-foreground">
                Prompt
              </label>
              <textarea
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                rows={3}
                placeholder="What should the agent do each time?"
                className="w-full rounded-xl border border-foreground/[0.06] bg-foreground/[0.03] px-4 py-2.5 text-sm placeholder:text-muted-foreground/40 focus:border-primary/50 focus:outline-none focus:ring-1 focus:ring-primary/25 resize-none"
              />
            </div>

            {/* Interval Picker */}
            <div>
              <label className="mb-1.5 block text-xs font-medium text-muted-foreground">
                Interval
              </label>
              <div className="flex flex-wrap gap-2">
                {INTERVAL_PRESETS.map((preset) => (
                  <button
                    key={preset.seconds}
                    onClick={() => setIntervalSeconds(preset.seconds)}
                    className={`rounded-lg px-3 py-1.5 text-xs font-medium transition-all ${
                      intervalSeconds === preset.seconds
                        ? "bg-primary/20 text-primary border border-primary/30"
                        : "bg-foreground/[0.04] text-muted-foreground border border-foreground/[0.06] hover:bg-foreground/[0.08]"
                    }`}
                  >
                    {preset.label}
                  </button>
                ))}
              </div>
            </div>

            {/* Priority + Agent */}
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="mb-1.5 block text-xs font-medium text-muted-foreground">
                  Priority
                </label>
                <div className="flex gap-2">
                  {[
                    { value: 0, label: "Low", color: "text-slate-400" },
                    { value: 1, label: "Normal", color: "text-blue-400" },
                    { value: 2, label: "High", color: "text-amber-400" },
                    { value: 3, label: "Urgent", color: "text-red-400" },
                  ].map((p) => (
                    <button
                      key={p.value}
                      onClick={() => setPriority(p.value)}
                      className={`flex-1 rounded-lg px-2 py-1.5 text-xs font-medium transition-all ${
                        priority === p.value
                          ? `bg-foreground/[0.08] ${p.color} border border-foreground/[0.12]`
                          : "bg-foreground/[0.03] text-muted-foreground border border-foreground/[0.06] hover:bg-foreground/[0.06]"
                      }`}
                    >
                      {p.label}
                    </button>
                  ))}
                </div>
              </div>
              <div>
                <label className="mb-1.5 block text-xs font-medium text-muted-foreground">
                  Agent (optional)
                </label>
                <select
                  value={agentId}
                  onChange={(e) => setAgentId(e.target.value)}
                  className="w-full rounded-xl border border-foreground/[0.06] bg-foreground/[0.03] px-4 py-2.5 text-sm focus:border-primary/50 focus:outline-none focus:ring-1 focus:ring-primary/25"
                >
                  <option value="">Auto-assign</option>
                  {agents
                    .filter((a) => a.state === "running" || a.state === "idle")
                    .map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.name}
                      </option>
                    ))}
                </select>
              </div>
            </div>

            <div className="flex justify-end gap-3 pt-2">
              <button
                onClick={() => setShowCreate(false)}
                className="rounded-xl px-4 py-2 text-sm text-muted-foreground hover:text-foreground transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleCreate}
                disabled={!name.trim() || !prompt.trim() || creating}
                className="flex items-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground transition-all hover:brightness-110 disabled:opacity-50"
              >
                {creating && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                Create Schedule
              </button>
            </div>
          </div>
        </motion.div>
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
            No schedules yet
          </p>
          <p className="mt-1 text-xs text-muted-foreground/60">
            Create a recurring task to automate agent work
          </p>
        </div>
      ) : (
        <motion.div
          variants={containerVariants}
          initial="hidden"
          animate="visible"
          className="space-y-3"
        >
          {schedules.map((schedule) => (
            <motion.div
              key={schedule.id}
              variants={itemVariants}
              className="group rounded-2xl border border-foreground/[0.06] bg-card/80 p-5 backdrop-blur-sm transition-all hover:border-foreground/[0.1]"
            >
              <div className="flex items-start justify-between">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-3">
                    <h3 className="text-sm font-semibold tracking-tight truncate">
                      {schedule.name}
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
                      {schedule.enabled ? "Active" : "Paused"}
                    </span>
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground/70 line-clamp-1">
                    {schedule.prompt}
                  </p>
                </div>

                {/* Actions */}
                <div className="flex items-center gap-1.5 ml-4 opacity-0 group-hover:opacity-100 transition-opacity">
                  <button
                    onClick={() => handleTrigger(schedule.id)}
                    disabled={triggering === schedule.id}
                    title="Run Now"
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
                    className={`flex h-8 w-8 items-center justify-center rounded-lg border backdrop-blur-sm transition-colors ${
                      schedule.enabled
                        ? "bg-amber-500/10 border-amber-500/20 text-amber-400 hover:bg-amber-500/20"
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
                    onClick={() => handleDelete(schedule.id)}
                    className="flex h-8 w-8 items-center justify-center rounded-lg bg-red-500/10 border border-red-500/20 text-red-400 hover:bg-red-500/20 backdrop-blur-sm transition-colors"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>

              {/* Stats Row */}
              <div className="mt-4 flex items-center gap-6 text-xs text-muted-foreground">
                <div className="flex items-center gap-1.5">
                  <Clock className="h-3.5 w-3.5" />
                  <span>{formatInterval(schedule.interval_seconds)}</span>
                </div>
                <div className="flex items-center gap-1.5">
                  <Timer className="h-3.5 w-3.5" />
                  <span>Next: {formatRelative(schedule.next_run_at)}</span>
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
                  {schedule.total_runs} runs
                  {schedule.total_runs > 0 && (
                    <> &middot; {Math.round(schedule.success_rate * 100)}% success</>
                  )}
                </div>
                {schedule.last_run_at && (
                  <div className="text-muted-foreground/50">
                    Last: {formatRelative(schedule.last_run_at)}
                  </div>
                )}
              </div>
            </motion.div>
          ))}
        </motion.div>
      )}
    </motion.div>
  );
}

"use client";

import { useState, useEffect, useCallback } from "react";
import { motion } from "framer-motion";
import {
  Clock,
  Plus,
  Pause,
  Play,
  Trash2,
  CheckCircle2,
  XCircle,
  Timer,
  Loader2,
  CalendarClock,
  Sparkles,
} from "lucide-react";
import type { Schedule } from "@/lib/types";
import * as api from "@/lib/api";
import { useAgents } from "@/hooks/use-agents";

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

export default function SchedulesPage() {
  const [schedules, setSchedules] = useState<Schedule[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [creating, setCreating] = useState(false);
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

  const handleDelete = async (id: string) => {
    await api.deleteSchedule(id);
    await refresh();
  };

  return (
    <div className="px-8 py-8 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Schedules</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Recurring tasks that run automatically on a schedule
          </p>
        </div>
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
    </div>
  );
}

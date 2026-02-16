"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { motion } from "framer-motion";
import {
  ArrowLeft,
  Cpu,
  Clock,
  DollarSign,
  MessageSquare,
  ListTodo,
  Eye,
  User,
  CheckCircle2,
  XCircle,
  Loader2,
  RotateCcw,
  Box,
  Zap,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Header } from "@/components/layout/header";
import { useAuthStore } from "@/lib/auth";
import * as api from "@/lib/api";
import type { AdminAgentStats } from "@/lib/api";

const stateColors: Record<string, string> = {
  running: "text-emerald-500",
  idle: "text-blue-500",
  working: "text-amber-500",
  stopped: "text-zinc-500",
  error: "text-red-500",
  created: "text-zinc-400",
};

const stateBg: Record<string, string> = {
  running: "bg-emerald-500/10",
  idle: "bg-blue-500/10",
  working: "bg-amber-500/10",
  stopped: "bg-zinc-500/10",
  error: "bg-red-500/10",
  created: "bg-zinc-400/10",
};

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  if (ms < 3_600_000) return `${(ms / 60_000).toFixed(1)}m`;
  return `${(ms / 3_600_000).toFixed(1)}h`;
}

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString();
}

export default function AdminAgentDetailPage() {
  const params = useParams();
  const router = useRouter();
  const user = useAuthStore((s) => s.user);
  const agentId = params.id as string;

  const [data, setData] = useState<AdminAgentStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (user && user.role !== "admin") {
      router.replace("/dashboard");
    }
  }, [user, router]);

  useEffect(() => {
    if (!agentId) return;
    setLoading(true);
    api
      .getAdminAgentStats(agentId)
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load"))
      .finally(() => setLoading(false));
  }, [agentId]);

  if (user?.role !== "admin") return null;

  return (
    <div>
      <Header
        title="Agent Statistics"
        subtitle={data?.agent.name || agentId}
        actions={
          <button
            onClick={() => router.push("/admin")}
            className="inline-flex items-center gap-2 rounded-xl px-4 py-2.5 text-sm font-medium text-muted-foreground hover:text-foreground hover:bg-accent/50 transition-all"
          >
            <ArrowLeft className="h-4 w-4" />
            Back to Admin
          </button>
        }
      />

      <motion.div
        className="px-8 py-6 space-y-6"
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
      >
        {loading ? (
          <div className="flex items-center justify-center py-20">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        ) : error ? (
          <div className="text-center py-20">
            <XCircle className="h-8 w-8 mx-auto mb-2 text-red-400" />
            <p className="text-sm text-red-400">{error}</p>
          </div>
        ) : data ? (
          <>
            {/* Agent Info Card */}
            <div className="rounded-xl border border-border/50 bg-card/50 p-6">
              <div className="flex items-start gap-4">
                <div className="relative shrink-0">
                  <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-foreground/[0.06]">
                    <Cpu className="h-7 w-7 text-muted-foreground" />
                  </div>
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-3 mb-1">
                    <h3 className="text-lg font-semibold">{data.agent.name}</h3>
                    <span
                      className={cn(
                        "px-2.5 py-1 rounded-lg text-[11px] font-semibold capitalize",
                        stateBg[data.agent.state] || "bg-zinc-500/10",
                        stateColors[data.agent.state] || "text-zinc-500"
                      )}
                    >
                      {data.agent.state}
                    </span>
                  </div>
                  <div className="flex flex-wrap items-center gap-4 text-sm text-muted-foreground">
                    <span className="flex items-center gap-1.5">
                      <Cpu className="h-3.5 w-3.5" />
                      {data.agent.model}
                    </span>
                    {data.agent.role && (
                      <span className="flex items-center gap-1.5">
                        <Zap className="h-3.5 w-3.5" />
                        {data.agent.role}
                      </span>
                    )}
                    {data.agent.container_id && (
                      <span className="flex items-center gap-1.5 font-mono text-xs">
                        <Box className="h-3.5 w-3.5" />
                        {data.agent.container_id.slice(0, 12)}
                      </span>
                    )}
                  </div>
                  <div className="flex flex-wrap gap-4 mt-2 text-xs text-muted-foreground/60">
                    <span>Created: {formatDate(data.agent.created_at)}</span>
                    <span>Updated: {formatDate(data.agent.updated_at)}</span>
                  </div>
                </div>
              </div>
            </div>

            {/* Stats Grid */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <StatCard
                icon={ListTodo}
                label="Total Tasks"
                value={data.stats.total_tasks}
                color="text-blue-500"
                bg="bg-blue-500/10"
              />
              <StatCard
                icon={CheckCircle2}
                label="Completed"
                value={data.stats.completed_tasks}
                color="text-emerald-500"
                bg="bg-emerald-500/10"
              />
              <StatCard
                icon={XCircle}
                label="Failed"
                value={data.stats.failed_tasks}
                color="text-red-500"
                bg="bg-red-500/10"
              />
              <StatCard
                icon={DollarSign}
                label="Total Cost"
                value={`$${data.stats.total_cost_usd.toFixed(4)}`}
                color="text-amber-500"
                bg="bg-amber-500/10"
              />
              <StatCard
                icon={Clock}
                label="Total Duration"
                value={formatDuration(data.stats.total_duration_ms)}
                color="text-purple-500"
                bg="bg-purple-500/10"
              />
              <StatCard
                icon={RotateCcw}
                label="Total Turns"
                value={data.stats.total_turns}
                color="text-cyan-500"
                bg="bg-cyan-500/10"
              />
              <StatCard
                icon={MessageSquare}
                label="Chat Sessions"
                value={data.stats.chat_sessions}
                color="text-indigo-500"
                bg="bg-indigo-500/10"
              />
              <StatCard
                icon={MessageSquare}
                label="Chat Messages"
                value={data.stats.chat_messages}
                color="text-pink-500"
                bg="bg-pink-500/10"
              />
            </div>

            {/* Visibility & Owner */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {/* Owner */}
              <div className="rounded-xl border border-border/50 bg-card/50 p-5">
                <h4 className="text-sm font-semibold mb-3 flex items-center gap-2">
                  <User className="h-4 w-4 text-muted-foreground" />
                  Owner
                </h4>
                {data.owner ? (
                  <div className="flex items-center gap-3">
                    <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10 text-primary text-sm font-bold">
                      {data.owner.name
                        .split(" ")
                        .map((n) => n[0])
                        .join("")
                        .slice(0, 2)
                        .toUpperCase()}
                    </div>
                    <div>
                      <p className="text-sm font-medium">{data.owner.name}</p>
                      <p className="text-xs text-muted-foreground">{data.owner.email}</p>
                    </div>
                    <span
                      className={cn(
                        "ml-auto px-2 py-0.5 rounded text-[10px] font-semibold",
                        data.owner.role === "admin"
                          ? "bg-amber-500/10 text-amber-500"
                          : "bg-blue-500/10 text-blue-500"
                      )}
                    >
                      {data.owner.role}
                    </span>
                  </div>
                ) : (
                  <p className="text-sm text-muted-foreground">No owner (legacy agent)</p>
                )}
              </div>

              {/* Visibility */}
              <div className="rounded-xl border border-border/50 bg-card/50 p-5">
                <h4 className="text-sm font-semibold mb-3 flex items-center gap-2">
                  <Eye className="h-4 w-4 text-muted-foreground" />
                  Visibility
                </h4>
                <div className="space-y-2">
                  {data.visibility.map((v, i) => (
                    <div
                      key={i}
                      className="flex items-center gap-2 text-sm"
                    >
                      <span className="px-2 py-0.5 rounded text-[10px] font-semibold bg-foreground/5 text-muted-foreground capitalize">
                        {v.scope}
                      </span>
                      {v.reason && (
                        <span className="text-muted-foreground">{v.reason}</span>
                      )}
                      {v.scope === "owner" && v.user && (
                        <span className="text-muted-foreground">
                          {(v.user as Record<string, string>).name}
                        </span>
                      )}
                      {v.scope === "admins" && v.count !== undefined && (
                        <span className="text-muted-foreground">
                          {v.count} admin{v.count !== 1 ? "s" : ""}
                        </span>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            </div>

            {/* Recent Tasks */}
            <div className="rounded-xl border border-border/50 bg-card/50 p-5">
              <h4 className="text-sm font-semibold mb-4 flex items-center gap-2">
                <ListTodo className="h-4 w-4 text-muted-foreground" />
                Recent Tasks
              </h4>
              {data.recent_tasks.length === 0 ? (
                <p className="text-sm text-muted-foreground text-center py-6">No tasks yet</p>
              ) : (
                <div className="space-y-2">
                  {data.recent_tasks.map((task) => (
                    <div
                      key={task.id}
                      className="flex items-center gap-3 px-3 py-2.5 rounded-lg bg-foreground/[0.02] hover:bg-foreground/[0.04] transition-colors"
                    >
                      <span
                        className={cn(
                          "shrink-0 h-2 w-2 rounded-full",
                          task.status === "completed"
                            ? "bg-emerald-500"
                            : task.status === "failed"
                            ? "bg-red-500"
                            : task.status === "running"
                            ? "bg-amber-500"
                            : "bg-zinc-400"
                        )}
                      />
                      <span className="flex-1 text-sm truncate">{task.title}</span>
                      <span className="shrink-0 text-[11px] text-muted-foreground capitalize">
                        {task.status}
                      </span>
                      {task.cost_usd !== null && (
                        <span className="shrink-0 text-[11px] text-muted-foreground">
                          ${task.cost_usd.toFixed(4)}
                        </span>
                      )}
                      {task.duration_ms !== null && (
                        <span className="shrink-0 text-[11px] text-muted-foreground">
                          {formatDuration(task.duration_ms)}
                        </span>
                      )}
                      {task.num_turns !== null && (
                        <span className="shrink-0 text-[11px] text-muted-foreground">
                          {task.num_turns} turns
                        </span>
                      )}
                      <span className="shrink-0 text-[11px] text-muted-foreground/50">
                        {formatDate(task.created_at)}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </>
        ) : null}
      </motion.div>
    </div>
  );
}

function StatCard({
  icon: Icon,
  label,
  value,
  color,
  bg,
}: {
  icon: typeof ListTodo;
  label: string;
  value: string | number;
  color: string;
  bg: string;
}) {
  return (
    <div className="rounded-xl border border-border/50 bg-card/50 p-4">
      <div className="flex items-center gap-3">
        <div className={cn("flex h-9 w-9 items-center justify-center rounded-lg", bg)}>
          <Icon className={cn("h-4.5 w-4.5", color)} />
        </div>
        <div>
          <p className="text-[11px] text-muted-foreground">{label}</p>
          <p className="text-lg font-semibold tracking-tight">{value}</p>
        </div>
      </div>
    </div>
  );
}

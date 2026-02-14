"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { motion } from "framer-motion";
import {
  ArrowLeft, CheckCircle2, XCircle, Clock, Loader2,
  Timer, Hash, Cpu, DollarSign, Wrench, Bot,
  AlertTriangle, Terminal, FileText, RotateCcw,
} from "lucide-react";
import { Header } from "@/components/layout/header";
import { cn } from "@/lib/utils";
import { formatDuration, formatCost, timeAgo } from "@/lib/utils";
import * as api from "@/lib/api";
import type { Task, LogEvent } from "@/lib/types";

const WS_URL = process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000";

const statusConfig: Record<string, { icon: typeof CheckCircle2; color: string; badge: string; label: string }> = {
  pending:   { icon: Clock,        color: "text-amber-400",   badge: "bg-amber-500/10 text-amber-400 border-amber-500/20",     label: "Pending" },
  queued:    { icon: Clock,        color: "text-blue-400",    badge: "bg-blue-500/10 text-blue-400 border-blue-500/20",         label: "Queued" },
  running:   { icon: Loader2,      color: "text-blue-400",    badge: "bg-blue-500/10 text-blue-400 border-blue-500/20",         label: "Running" },
  completed: { icon: CheckCircle2, color: "text-emerald-400", badge: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20", label: "Completed" },
  failed:    { icon: XCircle,      color: "text-red-400",     badge: "bg-red-500/10 text-red-400 border-red-500/20",             label: "Failed" },
  cancelled: { icon: XCircle,      color: "text-zinc-400",    badge: "bg-zinc-500/10 text-zinc-400 border-zinc-500/20",          label: "Cancelled" },
};

export default function TaskDetailPage() {
  const params = useParams();
  const taskId = params.id as string;
  const [task, setTask] = useState<Task | null>(null);
  const [logs, setLogs] = useState<LogEvent[]>([]);
  const [isConnected, setIsConnected] = useState(false);
  const [loading, setLoading] = useState(true);
  const wsRef = useRef<WebSocket | null>(null);
  const logContainerRef = useRef<HTMLDivElement>(null);
  const reconnectTimeout = useRef<ReturnType<typeof setTimeout>>();

  // Fetch task data
  const loadTask = useCallback(async () => {
    try {
      const data = await api.getTask(taskId);
      setTask(data);
      return data;
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
    return null;
  }, [taskId]);

  // Connect WebSocket for live logs
  const connectWs = useCallback((agentId: string) => {
    const ws = new WebSocket(`${WS_URL}/api/v1/ws/agents/${agentId}/logs`);
    wsRef.current = ws;

    ws.onopen = () => setIsConnected(true);

    ws.onclose = () => {
      setIsConnected(false);
      reconnectTimeout.current = setTimeout(() => connectWs(agentId), 3000);
    };

    ws.onerror = () => ws.close();

    ws.onmessage = (event) => {
      try {
        const data: LogEvent = JSON.parse(event.data);
        // Only show events for this task
        if (data.task_id === taskId) {
          setLogs((prev) => [...prev.slice(-1000), data]);
        }
      } catch {
        // Ignore non-JSON
      }
    };
  }, [taskId]);

  useEffect(() => {
    const init = async () => {
      const taskData = await loadTask();
      if (taskData?.agent_id) {
        connectWs(taskData.agent_id);
      }
    };
    init();

    // Poll task status for updates
    const interval = setInterval(loadTask, 3000);

    return () => {
      clearInterval(interval);
      wsRef.current?.close();
      clearTimeout(reconnectTimeout.current);
    };
  }, [loadTask, connectWs]);

  // Auto-scroll logs
  useEffect(() => {
    if (logContainerRef.current) {
      logContainerRef.current.scrollTop = logContainerRef.current.scrollHeight;
    }
  }, [logs]);

  if (loading || !task) {
    return (
      <div className="px-8 py-8 space-y-4">
        <div className="h-10 w-64 rounded-lg animate-shimmer bg-[length:200%_100%] bg-gradient-to-r from-foreground/[0.03] via-foreground/[0.06] to-foreground/[0.03]" />
        <div className="h-32 rounded-xl animate-shimmer bg-[length:200%_100%] bg-gradient-to-r from-foreground/[0.03] via-foreground/[0.06] to-foreground/[0.03]" />
        <div className="h-96 rounded-xl animate-shimmer bg-[length:200%_100%] bg-gradient-to-r from-foreground/[0.03] via-foreground/[0.06] to-foreground/[0.03]" />
      </div>
    );
  }

  const cfg = statusConfig[task.status] ?? statusConfig.pending;
  const StatusIcon = cfg.icon;
  const isActive = task.status === "running" || task.status === "queued";

  return (
    <div>
      <Header
        title={task.title}
        subtitle={`Task ${task.id.slice(0, 8)}`}
        actions={
          <div className="flex items-center gap-3">
            <Link
              href="/tasks"
              className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium text-muted-foreground hover:text-foreground hover:bg-foreground/[0.06] transition-colors"
            >
              <ArrowLeft className="h-3.5 w-3.5" />
              All Tasks
            </Link>
            <div className={cn(
              "inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs font-medium",
              cfg.badge
            )}>
              <StatusIcon className={cn("h-3.5 w-3.5", task.status === "running" && "animate-spin")} />
              {cfg.label}
            </div>
          </div>
        }
      />

      <motion.div
        className="px-8 py-8 space-y-5"
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
      >
        {/* Task info cards */}
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
          <MiniCard icon={Hash} label="Task ID" value={task.id.slice(0, 8)} />
          {task.agent_id && (
            <Link href={`/agents/${task.agent_id}`}>
              <MiniCard icon={Cpu} label="Agent" value={task.agent_id} clickable />
            </Link>
          )}
          {task.duration_ms ? (
            <MiniCard icon={Timer} label="Duration" value={formatDuration(task.duration_ms)} />
          ) : isActive ? (
            <MiniCard icon={Timer} label="Duration" value="In progress..." />
          ) : null}
          {task.cost_usd ? (
            <MiniCard icon={DollarSign} label="Cost" value={formatCost(task.cost_usd)} />
          ) : null}
          {task.num_turns ? (
            <MiniCard icon={RotateCcw} label="Turns" value={String(task.num_turns)} />
          ) : null}
        </div>

        {/* Prompt */}
        <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm overflow-hidden">
          <div className="flex items-center gap-2 border-b border-foreground/[0.06] px-5 py-3">
            <FileText className="h-3.5 w-3.5 text-muted-foreground" />
            <span className="text-xs font-medium text-muted-foreground">Prompt</span>
          </div>
          <div className="px-5 py-4 text-sm whitespace-pre-wrap leading-relaxed text-foreground/90">
            {task.prompt}
          </div>
        </div>

        {/* Error display */}
        {task.error && (
          <div className="rounded-xl bg-red-500/5 border border-red-500/10 px-5 py-4 flex items-start gap-3">
            <AlertTriangle className="h-4 w-4 text-red-400 shrink-0 mt-0.5" />
            <div>
              <p className="text-xs font-medium text-red-400/70 mb-1">Error</p>
              <p className="text-sm text-red-400">{task.error}</p>
            </div>
          </div>
        )}

        {/* Result display */}
        {task.result && task.status === "completed" && (
          <div className="rounded-xl bg-emerald-500/5 border border-emerald-500/10 px-5 py-4 flex items-start gap-3">
            <CheckCircle2 className="h-4 w-4 text-emerald-400 shrink-0 mt-0.5" />
            <div className="min-w-0">
              <p className="text-xs font-medium text-emerald-400/70 mb-1">Result</p>
              <p className="text-sm text-foreground/90 whitespace-pre-wrap break-words">{task.result}</p>
            </div>
          </div>
        )}

        {/* Live Output */}
        <div className="rounded-xl border border-foreground/[0.06] bg-black overflow-hidden">
          <div className="flex items-center justify-between border-b border-foreground/[0.06] px-5 py-3">
            <div className="flex items-center gap-2.5">
              <Terminal className="h-3.5 w-3.5 text-muted-foreground" />
              <span className="text-xs font-medium text-muted-foreground">Live Output</span>
              {isActive && (
                <span className="relative flex h-1.5 w-1.5 ml-1">
                  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-blue-400 opacity-75" />
                  <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-blue-500" />
                </span>
              )}
            </div>
            <div className="flex items-center gap-3">
              {task.agent_id && (
                <div className="flex items-center gap-1.5">
                  <div className={cn("h-1.5 w-1.5 rounded-full", isConnected ? "bg-emerald-500" : "bg-red-500")} />
                  <span className="text-[10px] text-muted-foreground/60">
                    {isConnected ? "Streaming" : "Disconnected"}
                  </span>
                </div>
              )}
              <button
                onClick={() => setLogs([])}
                className="text-[10px] text-muted-foreground/60 hover:text-muted-foreground transition-colors"
              >
                Clear
              </button>
            </div>
          </div>

          <div
            ref={logContainerRef}
            className="h-[500px] overflow-y-auto p-5 font-mono text-[12px] leading-relaxed space-y-1"
          >
            {logs.length === 0 && !isActive ? (
              <div className="flex flex-col items-center justify-center h-full text-muted-foreground/40">
                <Terminal className="h-8 w-8 mb-2" />
                <p className="text-xs">
                  {task.status === "completed" || task.status === "failed"
                    ? "Task has finished. Live logs are only available during execution."
                    : "Waiting for task to start..."}
                </p>
              </div>
            ) : logs.length === 0 && isActive ? (
              <div className="flex items-center gap-2 text-muted-foreground/60">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                <span>Waiting for output...</span>
              </div>
            ) : (
              logs.map((log, i) => <TaskLogLine key={i} event={log} />)
            )}
          </div>
        </div>

        {/* Timestamps */}
        <div className="flex items-center gap-6 text-[11px] text-muted-foreground/50">
          <span>Created: {new Date(task.created_at).toLocaleString()}</span>
          {task.started_at && <span>Started: {new Date(task.started_at).toLocaleString()}</span>}
          {task.completed_at && <span>Finished: {new Date(task.completed_at).toLocaleString()}</span>}
        </div>
      </motion.div>
    </div>
  );
}

function MiniCard({
  icon: Icon,
  label,
  value,
  clickable,
}: {
  icon: typeof Hash;
  label: string;
  value: string;
  clickable?: boolean;
}) {
  return (
    <div className={cn(
      "rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm px-4 py-3",
      clickable && "hover:border-primary/30 hover:bg-primary/5 transition-colors cursor-pointer"
    )}>
      <div className="flex items-center gap-1.5 mb-1">
        <Icon className="h-3 w-3 text-muted-foreground/60" />
        <span className="text-[10px] text-muted-foreground/60">{label}</span>
      </div>
      <p className="text-sm font-medium tabular-nums truncate">{value}</p>
    </div>
  );
}

function TaskLogLine({ event }: { event: LogEvent }) {
  const time = new Date(event.timestamp).toLocaleTimeString();
  const data = event.data as Record<string, unknown>;

  switch (event.type) {
    case "text":
      return (
        <div className="text-green-400 whitespace-pre-wrap break-words">
          <span className="text-muted-foreground/40 mr-2 select-none">{time}</span>
          {String(data.text || "")}
        </div>
      );
    case "tool_call":
      return (
        <div className="text-blue-400">
          <span className="text-muted-foreground/40 mr-2 select-none">{time}</span>
          <span className="inline-flex items-center gap-1">
            <Wrench className="h-3 w-3 text-yellow-400 inline" />
            <span className="text-yellow-400 font-medium">{String(data.tool || "")}</span>
          </span>
          <span className="text-muted-foreground/50 ml-2">
            {JSON.stringify(data.input || {}).slice(0, 300)}
          </span>
        </div>
      );
    case "tool_result":
      return (
        <div className="text-muted-foreground/60 ml-6 border-l border-foreground/[0.06] pl-3 max-h-32 overflow-hidden">
          {String(data.content || "").slice(0, 500)}
        </div>
      );
    case "error":
      return (
        <div className="text-red-400 flex items-start gap-1.5">
          <span className="text-muted-foreground/40 mr-0.5 select-none">{time}</span>
          <AlertTriangle className="h-3 w-3 mt-0.5 shrink-0" />
          {String(data.message || "")}
        </div>
      );
    case "system":
      return (
        <div className="text-violet-400/70 text-[11px]">
          <span className="text-muted-foreground/30 mr-2 select-none">{time}</span>
          {String(data.message || "")}
        </div>
      );
    case "result":
      return (
        <div className="text-emerald-400 border-t border-foreground/[0.08] mt-3 pt-3">
          <span className="text-muted-foreground/40 mr-2 select-none">{time}</span>
          Task completed — Cost: ${Number(data.cost_usd || 0).toFixed(4)} | Duration: {formatDuration(Number(data.duration_ms || 0))} | Turns: {Number(data.num_turns || 0)}
        </div>
      );
    default:
      return (
        <div className="text-muted-foreground/40 text-[11px]">
          {JSON.stringify(event.data)}
        </div>
      );
  }
}

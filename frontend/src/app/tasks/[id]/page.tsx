"use client";

import { useEffect, useRef, useState, useCallback, useMemo } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { motion } from "framer-motion";
import {
  ArrowLeft, ArrowRight, CheckCircle2, XCircle, Clock, Loader2,
  Timer, Hash, Cpu, DollarSign, Wrench, Bot,
  AlertTriangle, Terminal, FileText, RotateCcw, Send, MessageSquare,
  Download, Paperclip,
} from "lucide-react";
import { Header } from "@/components/layout/header";
import { cn } from "@/lib/utils";
import { formatDuration, formatCost, timeAgo, formatBytes } from "@/lib/utils";
import * as api from "@/lib/api";
import { useAuthStore } from "@/lib/auth";
import type { Task, LogEvent } from "@/lib/types";
import { useSimpleMode } from "@/hooks/use-simple-mode";
import { MarkdownContent } from "@/components/ui/markdown-content";

import { getWsUrl, getApiUrl } from "@/lib/config";

const statusConfig: Record<string, { icon: typeof CheckCircle2; color: string; badge: string; label: string }> = {
  pending:   { icon: Clock,        color: "text-amber-400",   badge: "bg-amber-500/10 text-amber-400 border-amber-500/20",     label: "Pending" },
  queued:    { icon: Clock,        color: "text-blue-400",    badge: "bg-blue-500/10 text-blue-400 border-blue-500/20",         label: "Queued" },
  running:   { icon: Loader2,      color: "text-blue-400",    badge: "bg-blue-500/10 text-blue-400 border-blue-500/20",         label: "Running" },
  completed: { icon: CheckCircle2, color: "text-emerald-400", badge: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20", label: "Completed" },
  failed:    { icon: XCircle,      color: "text-red-400",     badge: "bg-red-500/10 text-red-400 border-red-500/20",             label: "Failed" },
  cancelled: { icon: XCircle,      color: "text-zinc-400",    badge: "bg-zinc-500/10 text-zinc-400 border-zinc-500/20",          label: "Cancelled" },
};

// Merge consecutive streamed text events into one flowing block. The backend emits
// the answer as many small chunks; rendering each as its own line broke words
// mid-stream ("abgehackt"). Used for BOTH the live output and the step replay.
function mergeTextEvents(events: LogEvent[]): LogEvent[] {
  const out: LogEvent[] = [];
  for (const ev of events) {
    const last = out[out.length - 1];
    if (ev.type === "text" && last?.type === "text") {
      const prev = String((last.data as Record<string, unknown>)?.text || "");
      const add = String((ev.data as Record<string, unknown>)?.text || "");
      last.data = { ...(last.data as object), text: prev + add };
    } else {
      out.push({ ...ev, data: { ...(ev.data as object) } });
    }
  }
  return out;
}

export default function TaskDetailPage() {
  const params = useParams();
  const router = useRouter();
  const taskId = params.id as string;
  const [followUp, setFollowUp] = useState("");
  const [sendingFollowUp, setSendingFollowUp] = useState(false);
  const { simpleMode } = useSimpleMode();
  const [task, setTask] = useState<Task | null>(null);
  const [logs, setLogs] = useState<LogEvent[]>([]);
  // agent id → name, so delegated sub-tasks show WHO they went to (traceability).
  const [agentNames, setAgentNames] = useState<Record<string, string>>({});
  const [isConnected, setIsConnected] = useState(false);
  const [loading, setLoading] = useState(true);
  const [replaySteps, setReplaySteps] = useState<api.TaskStep[]>([]);
  const [artifacts, setArtifacts] = useState<api.TaskArtifact[]>([]);
  const [replayIndex, setReplayIndex] = useState(0);
  const [replayLoaded, setReplayLoaded] = useState(false);
  const [replayLoading, setReplayLoading] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const logContainerRef = useRef<HTMLDivElement>(null);
  const replayContainerRef = useRef<HTMLDivElement>(null);
  const reconnectTimeout = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  // Auto-scroll the replay log to the newest revealed step while dragging the slider.
  useEffect(() => {
    const el = replayContainerRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [replayIndex]);

  // Resolve agent id → name once, for readable "delegated to X" lines.
  useEffect(() => {
    api.getAgents("all").then((d) => {
      setAgentNames(Object.fromEntries(d.agents.map((a) => [a.id, a.name])));
    }).catch(() => {
      api.getAgents("own").then((d) =>
        setAgentNames(Object.fromEntries(d.agents.map((a) => [a.id, a.name])))
      ).catch(() => {});
    });
  }, []);

  // Merge consecutive streamed text deltas into one flowing block. The backend emits
  // the assistant's answer as many small chunks; rendering each as its own timestamped
  // line broke words mid-stream ("abgehackt"). Coalescing restores readable paragraphs.
  const mergedLogs = useMemo(() => mergeTextEvents(logs), [logs]);
  // Same coalescing for the step replay so it doesn't break words either.
  const mergedReplay = useMemo(
    () => mergeTextEvents(
      replaySteps.slice(0, replayIndex).map((s) => ({
        agent_id: "", task_id: taskId,
        type: s.type as LogEvent["type"], data: s.data, timestamp: s.timestamp || "",
      })),
    ),
    [replaySteps, replayIndex, taskId],
  );

  // Continue the session: send a follow-up instruction to the SAME agent as a new
  // task. The agent's workspace (with the produced files) persists, so it builds on
  // the previous result. Jump straight to the new task's live view.
  const sendFollowUp = useCallback(async () => {
    const text = followUp.trim();
    if (!text || !task) return;
    setSendingFollowUp(true);
    try {
      const t = await api.createTask({
        title: `Folge: ${task.title}`.slice(0, 120),
        prompt:
          `Folge-Anweisung zur vorherigen Aufgabe „${task.title}". Dein bisheriges Ergebnis und ` +
          `die dabei erzeugten Dateien liegen weiterhin in deinem Workspace (/workspace) — baue ` +
          `darauf auf, statt neu anzufangen:\n\n${text}`,
        agent_id: task.agent_id || undefined,
      });
      router.push(`/tasks/${t.id}`);
    } catch {
      setSendingFollowUp(false);
    }
  }, [followUp, task, router]);

  // Load the persisted step history for time-travel replay
  const loadReplay = useCallback(async () => {
    setReplayLoading(true);
    try {
      const res = await api.getTaskSteps(taskId);
      setReplaySteps(res.steps);
      setReplayIndex(res.steps.length);
      setReplayLoaded(true);
    } catch {
      setReplayLoaded(true);
    } finally {
      setReplayLoading(false);
    }
  }, [taskId]);

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
  const connectWs = useCallback(async (agentId: string) => {
    // Fetch one-time ticket for WebSocket auth
    let authParam = "";
    try {
      const resp = await fetch(`${getApiUrl()}/api/v1/ws/ticket`, {
        method: "POST",
        credentials: "include",
      });
      if (resp.ok) {
        const { ticket } = await resp.json();
        authParam = `?ticket=${ticket}`;
      }
    } catch {
      // Fallback to legacy token auth
      const token = useAuthStore.getState().wsToken;
      authParam = token ? `?token=${token}` : "";
    }

    const ws = new WebSocket(`${getWsUrl()}/api/v1/ws/agents/${agentId}/logs${authParam}`);
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
    const interval = setInterval(loadTask, 15000);

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

  // Load the deliverables the agent produced for this task (files in its
  // /workspace/transfer within the task's run window) once it is no longer active,
  // so the user can open them right here instead of digging through the explorer.
  const taskDone = task && task.status !== "running" && task.status !== "queued";
  useEffect(() => {
    if (!taskDone) return;
    let cancelled = false;
    api.getTaskArtifacts(taskId)
      .then((r) => { if (!cancelled) setArtifacts(r.artifacts || []); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [taskDone, taskId]);

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
          {!simpleMode && task.duration_ms ? (
            <MiniCard icon={Timer} label="Duration" value={formatDuration(task.duration_ms)} />
          ) : !simpleMode && isActive ? (
            <MiniCard icon={Timer} label="Duration" value="In progress..." />
          ) : null}
          {!simpleMode && task.cost_usd ? (
            <MiniCard icon={DollarSign} label="Cost" value={formatCost(task.cost_usd)} />
          ) : null}
          {!simpleMode && task.num_turns ? (
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

        {/* Deliverables the agent produced for this task — clickable, no explorer digging */}
        {taskDone && task.agent_id && artifacts.length > 0 && (
          <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm overflow-hidden">
            <div className="flex items-center gap-2 border-b border-foreground/[0.06] px-5 py-3">
              <Paperclip className="h-3.5 w-3.5 text-primary" />
              <span className="text-xs font-medium text-muted-foreground">
                Ergebnisse ({artifacts.length})
              </span>
            </div>
            <div className="p-2">
              {artifacts.map((a) => (
                <button
                  key={a.path}
                  onClick={() => window.open(api.getFileDownloadUrl(task.agent_id!, a.path), "_blank")}
                  className="group flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left hover:bg-foreground/[0.04] transition-colors"
                  title={a.path}
                >
                  <FileText className="h-4 w-4 shrink-0 text-muted-foreground group-hover:text-primary transition-colors" />
                  <span className="min-w-0 flex-1 truncate text-sm text-foreground/90">{a.name}</span>
                  <span className="shrink-0 text-[11px] text-muted-foreground/50 tabular-nums">
                    {formatBytes(a.size)}
                  </span>
                  <Download className="h-3.5 w-3.5 shrink-0 text-muted-foreground/40 group-hover:text-primary transition-colors" />
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Follow-up: react to the result and steer the agent further */}
        {!isActive && task.agent_id && (
          <div className="mb-4 rounded-xl border border-primary/20 bg-card/80 backdrop-blur-sm overflow-hidden">
            <div className="flex items-center gap-2 border-b border-foreground/[0.06] px-5 py-3">
              <MessageSquare className="h-3.5 w-3.5 text-primary" />
              <span className="text-xs font-medium text-muted-foreground">Weitere Anweisung geben</span>
            </div>
            <div className="p-4 space-y-2.5">
              <textarea
                value={followUp}
                onChange={(e) => setFollowUp(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) sendFollowUp(); }}
                placeholder="Auf dem Ergebnis aufbauen … z. B. „mach eine interaktive Version mit Charts und Dark Mode"
                rows={3}
                className="w-full rounded-lg border border-foreground/[0.1] bg-background/80 px-4 py-2.5 text-sm outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-all resize-none placeholder:text-muted-foreground/40"
              />
              <div className="flex items-center justify-between">
                <span className="text-[11px] text-muted-foreground/50">Der Agent arbeitet mit seinem bisherigen Ergebnis weiter (⌘/Strg+Enter)</span>
                <button
                  onClick={sendFollowUp}
                  disabled={sendingFollowUp || !followUp.trim()}
                  className="inline-flex items-center gap-2 rounded-lg bg-primary px-5 py-2.5 text-sm font-medium text-primary-foreground shadow-lg shadow-primary/20 hover:bg-primary/90 disabled:opacity-50 disabled:shadow-none transition-all"
                >
                  {sendingFollowUp ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
                  Anweisung senden
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Live Output (hidden in simple mode) */}
        {!simpleMode && <div className="rounded-xl border border-foreground/[0.06] bg-black overflow-hidden">
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
              mergedLogs.map((log, i) => <TaskLogLine key={i} event={log} agentNames={agentNames} />)
            )}
          </div>
        </div>}

        {/* Step-Replay (time-travel) — for finished tasks */}
        {!simpleMode && (task.status === "completed" || task.status === "failed") && (
          <div className="rounded-xl border border-foreground/[0.06] bg-card/80 overflow-hidden">
            <div className="flex items-center justify-between border-b border-foreground/[0.06] px-5 py-3">
              <div className="flex items-center gap-2.5">
                <RotateCcw className="h-3.5 w-3.5 text-muted-foreground" />
                <span className="text-xs font-medium text-muted-foreground">Schritt-Replay</span>
              </div>
              {!replayLoaded && (
                <button
                  onClick={loadReplay}
                  disabled={replayLoading}
                  className="flex items-center gap-1.5 rounded-lg bg-foreground/[0.06] px-3 py-1.5 text-[11px] font-medium text-foreground hover:bg-foreground/[0.1] disabled:opacity-50 transition-colors"
                >
                  {replayLoading ? <Loader2 className="h-3 w-3 animate-spin" /> : <RotateCcw className="h-3 w-3" />}
                  Replay laden
                </button>
              )}
            </div>
            {replayLoaded && (
              <div className="p-5 space-y-3">
                {replaySteps.length === 0 ? (
                  <p className="text-xs text-muted-foreground/50">
                    Keine aufgezeichneten Schritte für diese Task (vor Einführung des Step-Trackings ausgeführt).
                  </p>
                ) : (
                  <>
                    <div className="flex items-center gap-3">
                      <input
                        type="range"
                        min={0}
                        max={replaySteps.length}
                        value={replayIndex}
                        onChange={(e) => setReplayIndex(parseInt(e.target.value, 10))}
                        className="flex-1 accent-blue-500"
                      />
                      <span className="text-[11px] tabular-nums text-muted-foreground/70 w-20 text-right">
                        Schritt {replayIndex}/{replaySteps.length}
                      </span>
                    </div>
                    <div ref={replayContainerRef} className="h-[400px] overflow-y-auto rounded-lg bg-black p-4 font-mono text-[12px] leading-relaxed space-y-1">
                      {mergedReplay.map((ev, i) => (
                        <TaskLogLine key={i} event={ev} agentNames={agentNames} />
                      ))}
                      {replayIndex === 0 && (
                        <p className="text-muted-foreground/40">Schieberegler bewegen, um die Ausführung Schritt für Schritt abzuspielen.</p>
                      )}
                    </div>
                  </>
                )}
              </div>
            )}
          </div>
        )}

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

function TaskLogLine({ event, agentNames = {} }: { event: LogEvent; agentNames?: Record<string, string> }) {
  const time = new Date(event.timestamp).toLocaleTimeString();
  const data = event.data as Record<string, unknown>;

  switch (event.type) {
    case "text":
      // Render the (merged) assistant text as Markdown so lists, code and headings
      // read as clean prose instead of raw tokens — the terminal look was what made
      // the replay feel "gebrochen". Timestamp stays as a subtle inline prefix.
      return (
        <div className="flex gap-2">
          <span className="text-muted-foreground/40 select-none shrink-0 leading-relaxed">{time}</span>
          <MarkdownContent content={String(data.text || "")} className="flex-1 !text-foreground/90" />
        </div>
      );
    case "tool_call": {
      const tool = String(data.tool || "");
      const input = (data.input || {}) as Record<string, unknown>;
      // Delegation: the lead spinning up a sub-task for a teammate — show WHO + WHAT
      // so the team split is traceable, not just a raw create_task blob.
      if (tool === "create_task") {
        const target = String(input.agent_id || "");
        const who = agentNames[target] || (target ? target.slice(0, 8) : "Auto-Zuweisung");
        const what = String(input.title || input.prompt || "");
        return (
          <div className="text-amber-300 flex items-start gap-1.5">
            <span className="text-muted-foreground/40 mr-0.5 select-none">{time}</span>
            <ArrowRight className="h-3 w-3 mt-0.5 shrink-0 text-amber-400" />
            <span>
              <span className="font-medium">Delegiert an {who}</span>
              {what ? `: ${what.slice(0, 140)}` : ""}
            </span>
          </div>
        );
      }
      return (
        <div className="text-blue-400">
          <span className="text-muted-foreground/40 mr-2 select-none">{time}</span>
          <span className="inline-flex items-center gap-1">
            <Wrench className="h-3 w-3 text-yellow-400 inline" />
            <span className="text-yellow-400 font-medium">{tool}</span>
          </span>
          <span className="text-muted-foreground/50 ml-2">
            {JSON.stringify(input).slice(0, 300)}
          </span>
        </div>
      );
    }
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

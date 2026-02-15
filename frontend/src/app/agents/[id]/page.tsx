"use client";

import { useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";
import { motion } from "framer-motion";
import Link from "next/link";
import {
  Cpu, MemoryStick, MessageSquare, Terminal, History,
  CheckCircle2, XCircle, Clock, Loader2, RotateCcw,
  Timer, Hash, DollarSign, Activity, RefreshCw,
  Brain, Save, Edit3, FolderOpen, File, Folder,
  Download, Upload, ChevronRight, ArrowLeft, Plug, ArrowUpCircle,
} from "lucide-react";
import { Header } from "@/components/layout/header";
import { LiveTerminal } from "@/components/terminal/live-terminal";
import { AgentChat } from "@/components/agents/chat";
import { IntegrationSelector } from "@/components/agents/integration-selector";
import { MemoryTab } from "@/components/agents/memory-tab";
import { useTasks } from "@/hooks/use-tasks";
import { cn } from "@/lib/utils";
import { formatDuration, formatCost, timeAgo } from "@/lib/utils";
import * as api from "@/lib/api";
import type { Agent, FileEntry } from "@/lib/types";

const statusConfig: Record<string, { icon: typeof CheckCircle2; color: string; badge: string }> = {
  pending: { icon: Clock, color: "text-amber-400", badge: "bg-amber-500/10 text-amber-400 border-amber-500/20" },
  queued: { icon: Clock, color: "text-blue-400", badge: "bg-blue-500/10 text-blue-400 border-blue-500/20" },
  running: { icon: Loader2, color: "text-blue-400", badge: "bg-blue-500/10 text-blue-400 border-blue-500/20" },
  completed: { icon: CheckCircle2, color: "text-emerald-400", badge: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20" },
  failed: { icon: XCircle, color: "text-red-400", badge: "bg-red-500/10 text-red-400 border-red-500/20" },
  cancelled: { icon: XCircle, color: "text-zinc-400", badge: "bg-zinc-500/10 text-zinc-400 border-zinc-500/20" },
};

const agentStateConfig: Record<string, { dot: string; label: string; badge: string }> = {
  running: { dot: "bg-emerald-500", label: "Online", badge: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20" },
  working: { dot: "bg-blue-500", label: "Working", badge: "bg-blue-500/10 text-blue-400 border-blue-500/20" },
  idle: { dot: "bg-amber-500", label: "Idle", badge: "bg-amber-500/10 text-amber-400 border-amber-500/20" },
  stopped: { dot: "bg-zinc-500", label: "Stopped", badge: "bg-zinc-500/10 text-zinc-400 border-zinc-500/20" },
  error: { dot: "bg-red-500", label: "Error", badge: "bg-red-500/10 text-red-400 border-red-500/20" },
  created: { dot: "bg-violet-500", label: "Starting", badge: "bg-violet-500/10 text-violet-400 border-violet-500/20" },
};

const tabs = [
  { key: "chat", label: "Chat", icon: MessageSquare },
  { key: "terminal", label: "Live Output", icon: Terminal },
  { key: "files", label: "Files", icon: FolderOpen },
  { key: "history", label: "Task History", icon: History },
  { key: "knowledge", label: "Knowledge", icon: Brain },
  { key: "memory", label: "Memory", icon: MemoryStick },
  { key: "integrations", label: "Integrations", icon: Plug },
] as const;

type TabKey = (typeof tabs)[number]["key"];

export default function AgentDetailPage() {
  const params = useParams();
  const agentId = params.id as string;
  const [agent, setAgent] = useState<Agent | null>(null);
  const { tasks } = useTasks(agentId);
  const [activeTab, setActiveTab] = useState<TabKey>("chat");

  useEffect(() => {
    const load = async () => {
      try {
        const data = await api.getAgent(agentId);
        setAgent(data as Agent);
      } catch {
        // ignore
      }
    };
    load();
    const interval = setInterval(load, 5000);
    return () => clearInterval(interval);
  }, [agentId]);

  if (!agent) {
    return (
      <div className="px-8 py-8">
        <div className="space-y-4">
          <div className="h-10 w-48 rounded-lg animate-shimmer bg-[length:200%_100%] bg-gradient-to-r from-foreground/[0.03] via-foreground/[0.06] to-foreground/[0.03]" />
          <div className="grid grid-cols-4 gap-3">
            {[1, 2, 3, 4].map((i) => (
              <div key={i} className="h-20 rounded-xl animate-shimmer bg-[length:200%_100%] bg-gradient-to-r from-foreground/[0.03] via-foreground/[0.06] to-foreground/[0.03]" />
            ))}
          </div>
          <div className="h-96 rounded-xl animate-shimmer bg-[length:200%_100%] bg-gradient-to-r from-foreground/[0.03] via-foreground/[0.06] to-foreground/[0.03]" />
        </div>
      </div>
    );
  }

  const stateConfig = agentStateConfig[agent.state] ?? agentStateConfig.stopped;
  const isActive = agent.state === "running" || agent.state === "working";
  const cpuPercent = agent.cpu_percent ?? 0;
  const memMb = agent.memory_usage_mb ?? 0;

  return (
    <div className="flex flex-col h-[calc(100vh-4rem)]">
      <Header
        title={agent.name}
        subtitle={`Agent ${agent.id.slice(0, 8)}`}
        actions={
          <div className={cn(
            "inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs font-medium",
            stateConfig.badge
          )}>
            <span className="relative flex h-1.5 w-1.5">
              {isActive && (
                <span className={cn("absolute inline-flex h-full w-full animate-ping rounded-full opacity-75", stateConfig.dot)} />
              )}
              <span className={cn("relative inline-flex h-1.5 w-1.5 rounded-full", stateConfig.dot)} />
            </span>
            {stateConfig.label}
          </div>
        }
      />

      <motion.div
        className="px-8 py-4 space-y-4 flex-1 flex flex-col min-h-0"
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
      >
        {/* Info cards */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <InfoCard
            icon={Activity}
            label="State"
            value={stateConfig.label}
            color="text-blue-400"
            iconBg="bg-blue-500/10"
          />
          <InfoCard
            icon={Cpu}
            label="CPU"
            value={`${cpuPercent.toFixed(1)}%`}
            color="text-cyan-400"
            iconBg="bg-cyan-500/10"
            bar={{ value: cpuPercent, max: 100, gradient: "from-blue-500 to-cyan-400" }}
          />
          <InfoCard
            icon={MemoryStick}
            label="Memory"
            value={`${memMb.toFixed(0)} MB`}
            color="text-emerald-400"
            iconBg="bg-emerald-500/10"
            bar={{ value: memMb, max: 2048, gradient: "from-emerald-500 to-teal-400" }}
          />
          <InfoCard
            icon={Hash}
            label="Model"
            value={agent.model.split("-").slice(0, 2).join("-")}
            color="text-violet-400"
            iconBg="bg-violet-500/10"
          />
        </div>

        {/* Update available banner */}
        {agent.update_available && (
          <UpdateBanner agentId={agentId} onUpdated={(a) => setAgent(a)} />
        )}

        {/* Current task banner */}
        {agent.current_task && (
          <motion.div
            initial={{ opacity: 0, y: -4 }}
            animate={{ opacity: 1, y: 0 }}
            className="rounded-xl bg-blue-500/5 border border-blue-500/10 px-5 py-3 flex items-center gap-3"
          >
            <Loader2 className="h-4 w-4 text-blue-400 animate-spin shrink-0" />
            <div>
              <p className="text-xs font-medium text-blue-400/70">Currently working on</p>
              <p className="text-sm font-medium text-blue-400 truncate">{agent.current_task}</p>
            </div>
          </motion.div>
        )}

        {/* Pill tab switcher */}
        <div className="flex gap-1 p-1 rounded-xl bg-foreground/[0.03] border border-foreground/[0.06] w-fit">
          {tabs.map((tab) => {
            const Icon = tab.icon;
            return (
              <button
                key={tab.key}
                onClick={() => setActiveTab(tab.key)}
                className={cn(
                  "inline-flex items-center gap-2 rounded-lg px-4 py-2 text-xs font-medium transition-all duration-150",
                  activeTab === tab.key
                    ? "bg-foreground/[0.08] text-foreground shadow-sm"
                    : "text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04]"
                )}
              >
                <Icon className="h-3.5 w-3.5" />
                {tab.label}
              </button>
            );
          })}
        </div>

        {/* Tab content */}
        <div className="flex-1 min-h-0">
          {activeTab === "chat" && <AgentChat agentId={agentId} />}
          {activeTab === "terminal" && <LiveTerminal agentId={agentId} />}
          {activeTab === "files" && <FileBrowser agentId={agentId} />}
          {activeTab === "history" && <TaskHistory tasks={tasks} />}
          {activeTab === "knowledge" && <KnowledgePanel agentId={agentId} />}
          {activeTab === "memory" && <MemoryTab agentId={agentId} />}
          {activeTab === "integrations" && <IntegrationSelector agentId={agentId} />}
        </div>
      </motion.div>
    </div>
  );
}

function UpdateBanner({ agentId, onUpdated }: { agentId: string; onUpdated: (agent: Agent) => void }) {
  const [updating, setUpdating] = useState(false);

  const handleUpdate = async () => {
    if (!confirm("Update this agent to the latest version? The container will be recreated but all data (knowledge, files, sessions) will be preserved.")) return;
    setUpdating(true);
    try {
      const updated = await api.updateAgent(agentId);
      onUpdated(updated as Agent);
    } catch {
      // ignore
    } finally {
      setUpdating(false);
    }
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: -4 }}
      animate={{ opacity: 1, y: 0 }}
      className="rounded-xl bg-amber-500/5 border border-amber-500/20 px-5 py-3 flex items-center justify-between"
    >
      <div className="flex items-center gap-3">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-amber-500/10">
          <ArrowUpCircle className="h-4 w-4 text-amber-400" />
        </div>
        <div>
          <p className="text-sm font-medium text-amber-400">Update available</p>
          <p className="text-xs text-muted-foreground">A new agent image version is available. Your data will be preserved.</p>
        </div>
      </div>
      <button
        onClick={handleUpdate}
        disabled={updating}
        className="inline-flex items-center gap-1.5 rounded-lg px-4 py-2 text-xs font-medium bg-amber-500 text-black hover:bg-amber-400 transition-all disabled:opacity-50"
      >
        {updating ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
        ) : (
          <ArrowUpCircle className="h-3.5 w-3.5" />
        )}
        {updating ? "Updating..." : "Update Now"}
      </button>
    </motion.div>
  );
}

function InfoCard({
  icon: Icon,
  label,
  value,
  color,
  iconBg,
  bar,
}: {
  icon: typeof Cpu;
  label: string;
  value: string;
  color: string;
  iconBg: string;
  bar?: { value: number; max: number; gradient: string };
}) {
  return (
    <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-4">
      <div className="flex items-center gap-2.5 mb-2">
        <div className={cn("flex h-7 w-7 items-center justify-center rounded-lg", iconBg)}>
          <Icon className={cn("h-3.5 w-3.5", color)} />
        </div>
        <span className="text-[11px] font-medium text-muted-foreground">{label}</span>
      </div>
      <p className={cn("text-lg font-bold tabular-nums tracking-tight", color)}>{value}</p>
      {bar && (
        <div className="mt-2 h-1.5 rounded-full bg-foreground/[0.06]">
          <div
            className={cn("h-1.5 rounded-full bg-gradient-to-r transition-all duration-700 ease-out", bar.gradient)}
            style={{ width: `${Math.min((bar.value / bar.max) * 100, 100)}%` }}
          />
        </div>
      )}
    </div>
  );
}

function TaskHistory({ tasks }: { tasks: ReturnType<typeof useTasks>["tasks"] }) {
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

  if (tasks.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-foreground/[0.1] bg-card/30 p-12 text-center">
        <History className="h-8 w-8 text-muted-foreground/50 mx-auto mb-3" />
        <p className="text-sm text-muted-foreground">No tasks executed by this agent yet.</p>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {tasks.map((task, i) => {
        const cfg = statusConfig[task.status] ?? statusConfig.pending;
        const Icon = cfg.icon;
        return (
          <Link key={task.id} href={`/tasks/${task.id}`}>
          <motion.div
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: i * 0.03, duration: 0.25 }}
            className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-4 hover:border-foreground/[0.1] hover:bg-card/90 cursor-pointer transition-all duration-200"
          >
            <div className="flex items-center gap-3">
              <div className={cn("shrink-0", cfg.color)}>
                <Icon className={cn("h-4 w-4", task.status === "running" && "animate-spin")} />
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2.5">
                  <h4 className="font-medium text-sm truncate">{task.title}</h4>
                  <span className={cn(
                    "shrink-0 inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-medium",
                    cfg.badge
                  )}>
                    {task.status}
                  </span>
                </div>
              </div>
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
            <div className="mt-2 ml-7 flex items-center gap-4 text-[11px] text-muted-foreground/60">
              {task.duration_ms && (
                <span className="flex items-center gap-1 tabular-nums">
                  <Timer className="h-3 w-3" />{formatDuration(task.duration_ms)}
                </span>
              )}
              {task.num_turns && <span className="tabular-nums">{task.num_turns} turns</span>}
              {task.cost_usd ? (
                <span className="flex items-center gap-1 tabular-nums">
                  <DollarSign className="h-3 w-3" />{formatCost(task.cost_usd)}
                </span>
              ) : null}
              <span>{timeAgo(task.created_at)}</span>
            </div>
            {task.error && (
              <div className="mt-2 ml-7 rounded-lg bg-red-500/5 border border-red-500/10 px-3 py-2 text-[11px] text-red-400/80">
                {task.error}
              </div>
            )}
          </motion.div>
          </Link>
        );
      })}
    </div>
  );
}

function KnowledgePanel({ agentId }: { agentId: string }) {
  const [knowledge, setKnowledge] = useState("");
  const [metrics, setMetrics] = useState<Record<string, number>>({});
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [editContent, setEditContent] = useState("");

  useEffect(() => {
    loadKnowledge();
  }, [agentId]);

  const loadKnowledge = async () => {
    setLoading(true);
    try {
      const data = await api.getAgentKnowledge(agentId);
      setKnowledge(data.knowledge);
      setMetrics(data.metrics);
    } catch {
      setKnowledge("");
    } finally {
      setLoading(false);
    }
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await api.updateAgentKnowledge(agentId, editContent);
      setKnowledge(editContent);
      setEditing(false);
    } catch {
      // ignore
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="rounded-xl border border-foreground/[0.06] bg-card/80 p-8 flex items-center justify-center">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Metrics bar */}
      {Object.keys(metrics).length > 0 && (
        <div className="flex items-center gap-4 rounded-xl border border-foreground/[0.06] bg-card/80 p-4">
          <Brain className="h-4 w-4 text-violet-400 shrink-0" />
          <div className="flex items-center gap-5 text-xs">
            {metrics.total !== undefined && (
              <span className="tabular-nums">
                <span className="text-muted-foreground">Tasks:</span>{" "}
                <span className="font-medium">{metrics.total}</span>
              </span>
            )}
            {metrics.success !== undefined && (
              <span className="tabular-nums">
                <span className="text-muted-foreground">Success:</span>{" "}
                <span className="font-medium text-emerald-400">{metrics.success}</span>
              </span>
            )}
            {metrics.fail !== undefined && metrics.fail > 0 && (
              <span className="tabular-nums">
                <span className="text-muted-foreground">Failed:</span>{" "}
                <span className="font-medium text-red-400">{metrics.fail}</span>
              </span>
            )}
            {metrics.success_rate !== undefined && (
              <span className="tabular-nums">
                <span className="text-muted-foreground">Rate:</span>{" "}
                <span className={cn(
                  "font-medium",
                  metrics.success_rate >= 0.8 ? "text-emerald-400" : metrics.success_rate >= 0.5 ? "text-amber-400" : "text-red-400"
                )}>
                  {(metrics.success_rate * 100).toFixed(0)}%
                </span>
              </span>
            )}
          </div>
        </div>
      )}

      {/* Knowledge editor */}
      <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm overflow-hidden">
        <div className="flex items-center justify-between border-b border-foreground/[0.06] px-5 py-3">
          <div className="flex items-center gap-2">
            <Brain className="h-4 w-4 text-violet-400" />
            <span className="text-sm font-medium">knowledge.md</span>
            <span className="text-[10px] text-muted-foreground/60">Agent Knowledge Base</span>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={loadKnowledge}
              className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-[11px] font-medium text-muted-foreground hover:text-foreground hover:bg-foreground/[0.06] transition-colors"
            >
              <RefreshCw className="h-3 w-3" />
              Refresh
            </button>
            {editing ? (
              <>
                <button
                  onClick={handleSave}
                  disabled={saving}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-[11px] font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors"
                >
                  {saving ? <Loader2 className="h-3 w-3 animate-spin" /> : <Save className="h-3 w-3" />}
                  Save
                </button>
                <button
                  onClick={() => setEditing(false)}
                  className="rounded-lg px-3 py-1.5 text-[11px] font-medium text-muted-foreground hover:text-foreground hover:bg-foreground/[0.06] transition-colors"
                >
                  Cancel
                </button>
              </>
            ) : (
              <button
                onClick={() => { setEditContent(knowledge); setEditing(true); }}
                className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-[11px] font-medium text-muted-foreground hover:text-foreground hover:bg-foreground/[0.06] transition-colors"
              >
                <Edit3 className="h-3 w-3" />
                Edit
              </button>
            )}
          </div>
        </div>

        {editing ? (
          <textarea
            value={editContent}
            onChange={(e) => setEditContent(e.target.value)}
            className="w-full h-[500px] p-5 text-[12px] font-mono leading-relaxed bg-transparent outline-none resize-none"
          />
        ) : knowledge ? (
          <pre className="p-5 text-[12px] font-mono leading-relaxed whitespace-pre-wrap text-foreground/90 max-h-[500px] overflow-auto">
            {knowledge}
          </pre>
        ) : (
          <div className="flex flex-col items-center justify-center h-48 text-muted-foreground/50">
            <Brain className="h-8 w-8 mb-2" />
            <p className="text-sm">No knowledge base yet</p>
            <p className="text-xs text-muted-foreground/40 mt-1">Run a task to start building knowledge</p>
          </div>
        )}
      </div>
    </div>
  );
}

function formatFileSize(bytes: number): string {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${(bytes / Math.pow(k, i)).toFixed(i > 0 ? 1 : 0)} ${sizes[i]}`;
}

function FileBrowser({ agentId }: { agentId: string }) {
  const [currentPath, setCurrentPath] = useState("/workspace");
  const [entries, setEntries] = useState<FileEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const loadFiles = async (path: string) => {
    setLoading(true);
    try {
      const data = await api.getFiles(agentId, path);
      setEntries(data.entries);
      setCurrentPath(path);
    } catch {
      setEntries([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadFiles(currentPath);
  }, [agentId]);

  const navigateTo = (path: string) => {
    loadFiles(path);
  };

  const goUp = () => {
    const parent = currentPath.split("/").slice(0, -1).join("/") || "/";
    loadFiles(parent);
  };

  const handleUpload = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    setUploading(true);
    try {
      await api.uploadFiles(agentId, currentPath, files);
      await loadFiles(currentPath);
    } catch (e) {
      alert(e instanceof Error ? e.message : "Upload failed");
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const handleDownload = (entry: FileEntry) => {
    const url = api.getFileDownloadUrl(agentId, entry.path);
    window.open(url, "_blank");
  };

  // Breadcrumb segments
  const pathParts = currentPath.split("/").filter(Boolean);

  // Sort: directories first, then files, alphabetically
  const sorted = [...entries].sort((a, b) => {
    if (a.type !== b.type) return a.type === "directory" ? -1 : 1;
    return a.name.localeCompare(b.name);
  });

  return (
    <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm overflow-hidden">
      {/* Toolbar */}
      <div className="flex items-center justify-between border-b border-foreground/[0.06] px-5 py-3">
        <div className="flex items-center gap-2 min-w-0">
          {currentPath !== "/" && currentPath !== "/workspace" && (
            <button
              onClick={goUp}
              className="flex h-7 w-7 items-center justify-center rounded-lg text-muted-foreground hover:text-foreground hover:bg-foreground/[0.06] transition-colors shrink-0"
              title="Go up"
            >
              <ArrowLeft className="h-3.5 w-3.5" />
            </button>
          )}
          <div className="flex items-center gap-1 text-xs min-w-0 overflow-hidden">
            <button
              onClick={() => navigateTo("/workspace")}
              className="text-muted-foreground hover:text-foreground transition-colors shrink-0"
            >
              workspace
            </button>
            {pathParts.slice(1).map((part, i) => {
              const fullPath = "/" + pathParts.slice(0, i + 2).join("/");
              return (
                <span key={fullPath} className="flex items-center gap-1 min-w-0">
                  <ChevronRight className="h-3 w-3 text-muted-foreground/40 shrink-0" />
                  <button
                    onClick={() => navigateTo(fullPath)}
                    className="text-muted-foreground hover:text-foreground transition-colors truncate"
                  >
                    {part}
                  </button>
                </span>
              );
            })}
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <button
            onClick={() => loadFiles(currentPath)}
            className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-[11px] font-medium text-muted-foreground hover:text-foreground hover:bg-foreground/[0.06] transition-colors"
          >
            <RefreshCw className="h-3 w-3" />
            Refresh
          </button>
          <input
            ref={fileInputRef}
            type="file"
            multiple
            className="hidden"
            onChange={(e) => handleUpload(e.target.files)}
          />
          <button
            onClick={() => fileInputRef.current?.click()}
            disabled={uploading}
            className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-[11px] font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors"
          >
            {uploading ? <Loader2 className="h-3 w-3 animate-spin" /> : <Upload className="h-3 w-3" />}
            Upload
          </button>
        </div>
      </div>

      {/* File list */}
      {loading ? (
        <div className="flex items-center justify-center h-48">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      ) : sorted.length === 0 ? (
        <div className="flex flex-col items-center justify-center h-48 text-muted-foreground/50">
          <FolderOpen className="h-8 w-8 mb-2" />
          <p className="text-sm">Empty directory</p>
        </div>
      ) : (
        <div className="divide-y divide-foreground/[0.04] max-h-[500px] overflow-auto">
          {sorted.map((entry) => (
            <div
              key={entry.path}
              className="flex items-center gap-3 px-5 py-2.5 hover:bg-foreground/[0.03] transition-colors cursor-pointer group"
              onClick={() => {
                if (entry.type === "directory") {
                  navigateTo(entry.path);
                }
              }}
            >
              <div className={cn(
                "flex h-8 w-8 items-center justify-center rounded-lg shrink-0",
                entry.type === "directory" ? "bg-amber-500/10" : "bg-blue-500/10"
              )}>
                {entry.type === "directory" ? (
                  <Folder className="h-4 w-4 text-amber-400" />
                ) : (
                  <File className="h-4 w-4 text-blue-400" />
                )}
              </div>
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium truncate">{entry.name}</p>
                <p className="text-[11px] text-muted-foreground/60">
                  {entry.type === "directory"
                    ? "Directory"
                    : formatFileSize(entry.size)}
                  {entry.modified > 0 && (
                    <span className="ml-2">{new Date(entry.modified * 1000).toLocaleDateString()}</span>
                  )}
                </p>
              </div>
              {entry.type === "file" && (
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    handleDownload(entry);
                  }}
                  className="flex h-8 w-8 items-center justify-center rounded-lg text-muted-foreground hover:text-foreground hover:bg-foreground/[0.06] opacity-0 group-hover:opacity-100 transition-all shrink-0"
                  title="Download"
                >
                  <Download className="h-3.5 w-3.5" />
                </button>
              )}
              {entry.type === "directory" && (
                <ChevronRight className="h-4 w-4 text-muted-foreground/40 shrink-0" />
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

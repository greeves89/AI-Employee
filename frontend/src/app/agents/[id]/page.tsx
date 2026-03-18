"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "next/navigation";
import { motion } from "framer-motion";
import Link from "next/link";
import {
  Cpu, MemoryStick, MessageSquare, History,
  CheckCircle2, XCircle, Clock, Loader2, RotateCcw,
  Timer, Hash, DollarSign, Activity, RefreshCw,
  Brain, Save, Edit3, FolderOpen, File, Folder,
  Download, Upload, ChevronRight, ArrowLeft, Plug, ArrowUpCircle,
  Settings, Package, ShieldOff, Check, ListTodo,
  Eye, EyeOff, Search, X, ArrowUpDown, Code, FileText,
  Image as ImageIcon, Container, Send, Copy, RefreshCcw, Trash2, Key, Sparkles,
} from "lucide-react";
import { Header } from "@/components/layout/header";
import {
  FilePreview, FilePreviewEmpty,
  getFileColor, formatModified, formatModifiedFull,
  formatFileSize as formatFileSizeShared,
} from "@/components/files/file-preview";
import { LiveTerminal } from "@/components/terminal/live-terminal";
import { AgentChat } from "@/components/agents/chat";
import { IntegrationSelector } from "@/components/agents/integration-selector";
import { MemoryTab } from "@/components/agents/memory-tab";
import { TodoTab } from "@/components/agents/todo-tab";
import { McpInfo } from "@/components/agents/mcp-info";
import { ProactiveToggle } from "@/components/agents/proactive-toggle";
import { DockerAppsTab } from "@/components/agents/docker-apps-tab";
import { SkillsTab } from "@/components/agents/skills-tab";
import { useTasks } from "@/hooks/use-tasks";
import { cn } from "@/lib/utils";
import { formatDuration, formatCost, timeAgo } from "@/lib/utils";
import * as api from "@/lib/api";
import type { Agent, FileEntry, PermissionPackage } from "@/lib/types";
import { useSimpleMode } from "@/hooks/use-simple-mode";

const statusConfig: Record<string, { icon: typeof CheckCircle2; color: string; badge: string }> = {
  pending: { icon: Clock, color: "text-amber-400", badge: "bg-amber-500/10 text-amber-400 border-amber-500/20" },
  queued: { icon: Clock, color: "text-blue-400", badge: "bg-blue-500/10 text-blue-400 border-blue-500/20" },
  running: { icon: Loader2, color: "text-blue-400", badge: "bg-blue-500/10 text-blue-400 border-blue-500/20" },
  completed: { icon: CheckCircle2, color: "text-emerald-400", badge: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20" },
  failed: { icon: XCircle, color: "text-red-400", badge: "bg-red-500/10 text-red-400 border-red-500/20" },
  cancelled: { icon: XCircle, color: "text-zinc-400", badge: "bg-zinc-500/10 text-zinc-400 border-zinc-500/20" },
};

const agentStateConfig: Record<string, { online: boolean; label: string; badge: string }> = {
  running: { online: true, label: "Idle", badge: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20" },
  idle: { online: true, label: "Idle", badge: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20" },
  working: { online: true, label: "Working", badge: "bg-blue-500/10 text-blue-400 border-blue-500/20" },
  stopped: { online: false, label: "Stopped", badge: "bg-zinc-500/10 text-zinc-400 border-zinc-500/20" },
  error: { online: false, label: "Error", badge: "bg-red-500/10 text-red-400 border-red-500/20" },
  created: { online: false, label: "Starting", badge: "bg-violet-500/10 text-violet-400 border-violet-500/20" },
};

const tabs = [
  { key: "chat", label: "Chat", icon: MessageSquare, simpleVisible: true },
  { key: "terminal", label: "Activity", icon: Activity, simpleVisible: false },
  { key: "todos", label: "Todos", icon: ListTodo, simpleVisible: true },
  { key: "files", label: "Files", icon: FolderOpen, simpleVisible: true },
  { key: "apps", label: "Apps", icon: Container, simpleVisible: false },
  { key: "history", label: "Task History", icon: History, simpleVisible: true },
  { key: "knowledge", label: "Knowledge", icon: Brain, simpleVisible: false },
  { key: "memory", label: "Memory", icon: MemoryStick, simpleVisible: false },
  { key: "integrations", label: "Integrations", icon: Plug, simpleVisible: false },
  { key: "skills", label: "Skills", icon: Sparkles, simpleVisible: false },
  { key: "settings", label: "Settings", icon: Settings, simpleVisible: false },
] as const;

type TabKey = (typeof tabs)[number]["key"];

export default function AgentDetailPage() {
  const params = useParams();
  const agentId = params.id as string;
  const [agent, setAgent] = useState<Agent | null>(null);
  const { tasks } = useTasks(agentId);
  const [activeTab, setActiveTab] = useState<TabKey>("chat");
  const [restarting, setRestarting] = useState(false);
  const { simpleMode } = useSimpleMode();

  const visibleTabs = simpleMode
    ? tabs.filter((t) => t.simpleVisible)
    : tabs;

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
    const interval = setInterval(load, 15000);
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
  const isActive = stateConfig.online;
  const cpuPercent = agent.cpu_percent ?? 0;
  const memMb = agent.memory_usage_mb ?? 0;

  return (
    <div className="flex flex-col h-[calc(100vh-4rem)]">
      <Header
        title={agent.name}
        subtitle={`Agent ${agent.id.slice(0, 8)}`}
        actions={
          <div className="flex items-center gap-3">
            {/* Inline status metrics */}
            {!simpleMode && (
              <div className="hidden lg:flex items-center gap-3 mr-2">
                <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  <Cpu className="h-3 w-3 text-cyan-400" />
                  <span className="text-cyan-400 font-medium">{cpuPercent.toFixed(1)}%</span>
                </div>
                <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  <MemoryStick className="h-3 w-3 text-emerald-400" />
                  <span className="text-emerald-400 font-medium">{memMb.toFixed(0)} MB</span>
                </div>
                <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  {agent.mode === "custom_llm" ? <Plug className="h-3 w-3 text-violet-400" /> : <Hash className="h-3 w-3 text-violet-400" />}
                  <span className="text-violet-400 font-medium">
                    {agent.mode === "custom_llm" && agent.llm_config
                      ? `${agent.llm_config.provider_type === "openai" ? "OpenAI" : agent.llm_config.provider_type === "google" ? "Google" : "Anthropic"} / ${agent.llm_config.model_name}`
                      : agent.model.split("-").slice(0, 2).join("-")}
                  </span>
                </div>
              </div>
            )}
            <button
              onClick={async () => {
                setRestarting(true);
                try {
                  const updated = await api.restartAgent(agentId);
                  setAgent(updated as Agent);
                } catch {
                  // ignore
                } finally {
                  setRestarting(false);
                }
              }}
              disabled={restarting}
              className="inline-flex items-center gap-1.5 rounded-full border border-foreground/[0.1] px-3 py-1.5 text-xs font-medium text-muted-foreground hover:text-foreground hover:border-foreground/[0.2] hover:bg-foreground/[0.04] transition-all disabled:opacity-50"
              title="Restart agent (picks up new MCP servers, integrations)"
            >
              <RefreshCw className={cn("h-3 w-3", restarting && "animate-spin")} />
              {restarting ? "Restarting..." : "Restart"}
            </button>
            {agent.mode === "custom_llm" && (
              <div className="inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs font-medium bg-violet-500/10 text-violet-400 border-violet-500/20">
                <Plug className="h-3 w-3" />
                Custom LLM
              </div>
            )}
            <div className={cn(
              "inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs font-medium",
              stateConfig.badge
            )}>
              <span className="relative flex h-1.5 w-1.5">
                {stateConfig.online && (
                  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-500 opacity-75" />
                )}
                <span className={cn(
                  "relative inline-flex h-1.5 w-1.5 rounded-full",
                  stateConfig.online ? "bg-emerald-500" : "bg-red-500"
                )} />
              </span>
              {stateConfig.label}
            </div>
          </div>
        }
      />

      <motion.div
        className="px-8 py-4 space-y-4 flex-1 flex flex-col min-h-0"
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
      >

        {/* Budget progress */}
        {agent.budget_usd != null && agent.budget_usd > 0 && (
          <BudgetBar spent={agent.total_cost_usd ?? 0} budget={agent.budget_usd} />
        )}

        {/* Update available banner */}
        {agent.update_available && (
          <UpdateBanner agentId={agentId} onUpdated={(a) => setAgent(a)} />
        )}

        {/* Pill tab switcher + current task indicator */}
        <div className="flex items-center gap-3">
          <div className="flex gap-1 p-1 rounded-xl bg-foreground/[0.03] border border-foreground/[0.06] w-fit overflow-x-auto">
          {visibleTabs.map((tab) => {
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

          {/* Current task indicator (inline) */}
          {agent.current_task && (
            <motion.div
              initial={{ opacity: 0, x: -8 }}
              animate={{ opacity: 1, x: 0 }}
              className="flex items-center gap-2 rounded-lg bg-blue-500/5 border border-blue-500/10 px-3 py-1.5 max-w-xs"
            >
              <Loader2 className="h-3 w-3 text-blue-400 animate-spin shrink-0" />
              <p className="text-[11px] font-medium text-blue-400 truncate">{agent.current_task}</p>
            </motion.div>
          )}
        </div>

        {/* Tab content */}
        <div className="flex-1 min-h-0">
          {activeTab === "chat" && <AgentChat agentId={agentId} />}
          {activeTab === "terminal" && <LiveTerminal agentId={agentId} />}
          {activeTab === "todos" && <TodoTab agentId={agentId} />}
          {activeTab === "files" && <FileBrowser agentId={agentId} />}
          {activeTab === "apps" && <DockerAppsTab agentId={agentId} />}
          {activeTab === "history" && <TaskHistory tasks={tasks} />}
          {activeTab === "knowledge" && <KnowledgePanel agentId={agentId} />}
          {activeTab === "memory" && <MemoryTab agentId={agentId} />}
          {activeTab === "integrations" && <IntegrationSelector agentId={agentId} />}
          {activeTab === "skills" && <SkillsTab agentId={agentId} />}
          {activeTab === "settings" && <AgentSettings agent={agent} onUpdated={(a) => setAgent(a)} />}
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

function BudgetBar({ spent, budget }: { spent: number; budget: number }) {
  const pct = budget > 0 ? Math.min((spent / budget) * 100, 100) : 0;
  const color =
    pct >= 100
      ? "from-red-500 to-red-400"
      : pct >= 80
        ? "from-amber-500 to-amber-400"
        : "from-emerald-500 to-teal-400";
  const labelColor =
    pct >= 100 ? "text-red-400" : pct >= 80 ? "text-amber-400" : "text-emerald-400";

  return (
    <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-4">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-amber-500/10">
            <DollarSign className="h-3.5 w-3.5 text-amber-400" />
          </div>
          <span className="text-[11px] font-medium text-muted-foreground">Budget</span>
        </div>
        <span className={cn("text-sm font-bold tabular-nums", labelColor)}>
          ${spent.toFixed(2)} / ${budget.toFixed(2)}
        </span>
      </div>
      <div className="h-2 rounded-full bg-foreground/[0.06]">
        <div
          className={cn("h-2 rounded-full bg-gradient-to-r transition-all duration-700 ease-out", color)}
          style={{ width: `${pct}%` }}
        />
      </div>
      <p className="text-[10px] text-muted-foreground/50 mt-1.5 text-right">
        {pct.toFixed(0)}% used
      </p>
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
    <div className="flex flex-col gap-4 h-full">
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
      <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm overflow-hidden flex-1 min-h-0 flex flex-col">
        <div className="flex items-center justify-between border-b border-foreground/[0.06] px-5 py-3 shrink-0">
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
            className="w-full flex-1 min-h-0 p-5 pb-8 text-[12px] font-mono leading-relaxed bg-transparent outline-none resize-none"
          />
        ) : knowledge ? (
          <pre className="p-5 pb-8 text-[12px] font-mono leading-relaxed whitespace-pre-wrap text-foreground/90 flex-1 min-h-0 overflow-auto">
            {knowledge}
          </pre>
        ) : (
          <div className="flex flex-col items-center justify-center flex-1 min-h-[12rem] text-muted-foreground/50">
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

const PERM_ICON_MAP: Record<string, React.ElementType> = {
  package: Package,
  settings: Settings,
  "shield-off": ShieldOff,
};

function TelegramAgentSection({ agentId }: { agentId: string }) {
  const [loading, setLoading] = useState(true);
  const [hasToken, setHasToken] = useState(false);
  const [authKey, setAuthKey] = useState("");
  const [botRunning, setBotRunning] = useState(false);
  const [botToken, setBotToken] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const load = async () => {
    try {
      const data = await api.getAgentTelegram(agentId);
      setHasToken(data.has_token);
      setAuthKey(data.auth_key);
      setBotRunning(data.bot_running);
    } catch {
      // Telegram not configured yet
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, [agentId]);

  const handleSave = async () => {
    if (!botToken.trim()) return;
    setSaving(true);
    setError(null);
    try {
      const data = await api.setAgentTelegram(agentId, botToken.trim());
      setHasToken(true);
      setAuthKey(data.auth_key);
      setBotRunning(data.bot_running);
      setBotToken("");
      if (data.error) setError(data.error);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Fehler beim Speichern");
    } finally {
      setSaving(false);
    }
  };

  const handleRemove = async () => {
    setSaving(true);
    try {
      await api.removeAgentTelegram(agentId);
      setHasToken(false);
      setAuthKey("");
      setBotRunning(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Fehler");
    } finally {
      setSaving(false);
    }
  };

  const handleRegenerateKey = async () => {
    try {
      const data = await api.regenerateTelegramKey(agentId);
      setAuthKey(data.auth_key);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Fehler");
    }
  };

  const copyKey = () => {
    navigator.clipboard.writeText(authKey);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  if (loading) return null;

  return (
    <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm overflow-hidden">
      <div className="flex items-center justify-between border-b border-foreground/[0.06] px-5 py-3">
        <div className="flex items-center gap-2">
          <Send className="h-4 w-4 text-sky-400" />
          <span className="text-sm font-medium">Telegram Bot</span>
          {hasToken && (
            <span className={cn(
              "text-[10px] px-2 py-0.5 rounded-full border font-medium",
              botRunning
                ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/20"
                : "bg-amber-500/10 text-amber-400 border-amber-500/20"
            )}>
              {botRunning ? "Verbunden" : "Nicht aktiv"}
            </span>
          )}
        </div>
        {hasToken && (
          <button
            onClick={handleRemove}
            disabled={saving}
            className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-[11px] font-medium text-red-400 hover:bg-red-500/10 transition-colors"
          >
            <Trash2 className="h-3 w-3" />
            Entfernen
          </button>
        )}
      </div>

      <div className="p-5 space-y-4">
        {!hasToken ? (
          <>
            <p className="text-xs text-muted-foreground/70">
              Erstelle einen Bot bei{" "}
              <span className="text-sky-400 font-medium">@BotFather</span> auf Telegram
              und gib hier den Token ein. Nutzer muessen sich mit einem Auth-Key autorisieren.
            </p>
            <div className="flex gap-2">
              <input
                type="password"
                value={botToken}
                onChange={(e) => setBotToken(e.target.value)}
                placeholder="Bot Token von @BotFather..."
                className="flex-1 rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3.5 py-2.5 text-sm font-mono outline-none focus:border-sky-500/50 focus:ring-1 focus:ring-sky-500/20 transition-all"
              />
              <button
                onClick={handleSave}
                disabled={saving || !botToken.trim()}
                className="rounded-xl bg-sky-600 px-4 py-2.5 text-sm font-medium text-white shadow-lg shadow-sky-600/20 hover:bg-sky-500 disabled:opacity-40 transition-all"
              >
                {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : "Verbinden"}
              </button>
            </div>
          </>
        ) : (
          <>
            {/* Auth Key display */}
            <div>
              <label className="block text-[11px] font-medium text-muted-foreground/70 mb-1.5">
                Auth-Key (zum Teilen mit Nutzern)
              </label>
              <div className="flex items-center gap-2">
                <div className="flex-1 flex items-center gap-2 rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3.5 py-2.5">
                  <Key className="h-3.5 w-3.5 text-muted-foreground/50 shrink-0" />
                  <code className="text-sm font-mono tracking-wider text-foreground select-all">{authKey}</code>
                </div>
                <button
                  onClick={copyKey}
                  className={cn(
                    "rounded-lg p-2.5 transition-all border",
                    copied
                      ? "bg-emerald-500/10 border-emerald-500/20 text-emerald-400"
                      : "border-foreground/[0.08] text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04]"
                  )}
                  title="Key kopieren"
                >
                  {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
                </button>
                <button
                  onClick={handleRegenerateKey}
                  className="rounded-lg p-2.5 border border-foreground/[0.08] text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04] transition-all"
                  title="Key neu generieren (alle Sessions ungueltig)"
                >
                  <RefreshCcw className="h-4 w-4" />
                </button>
              </div>
              <p className="text-[10px] text-muted-foreground/40 mt-1.5">
                Nutzer senden <code className="text-sky-400/80">/auth {authKey}</code> im Telegram-Chat um sich zu autorisieren.
              </p>
            </div>

            {/* Change token */}
            <div>
              <label className="block text-[11px] font-medium text-muted-foreground/70 mb-1.5">
                Bot Token aendern
              </label>
              <div className="flex gap-2">
                <input
                  type="password"
                  value={botToken}
                  onChange={(e) => setBotToken(e.target.value)}
                  placeholder="Neuer Bot Token..."
                  className="flex-1 rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3.5 py-2.5 text-sm font-mono outline-none focus:border-sky-500/50 focus:ring-1 focus:ring-sky-500/20 transition-all"
                />
                <button
                  onClick={handleSave}
                  disabled={saving || !botToken.trim()}
                  className="rounded-lg bg-sky-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-sky-500 disabled:opacity-40 transition-all"
                >
                  {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : "Aktualisieren"}
                </button>
              </div>
            </div>
          </>
        )}

        {error && (
          <div className="rounded-lg border border-red-500/20 bg-red-500/10 px-4 py-2.5 text-sm text-red-400">
            {error}
          </div>
        )}
      </div>
    </div>
  );
}


function AgentSettings({
  agent,
  onUpdated,
}: {
  agent: Agent;
  onUpdated: (agent: Agent) => void;
}) {
  const agentId = agent.id;
  const currentPermissions = agent.permissions ?? [];

  const [packages, setPackages] = useState<PermissionPackage[]>([]);
  const [selected, setSelected] = useState<string[]>(currentPermissions);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ type: "success" | "warning" | "error"; text: string } | null>(null);

  // LLM Config state (for custom_llm agents)
  const [llmEditing, setLlmEditing] = useState(false);
  const [llmSaving, setLlmSaving] = useState(false);
  const [llmEndpoint, setLlmEndpoint] = useState(agent.llm_config?.api_endpoint ?? "");
  const [llmModel, setLlmModel] = useState(agent.llm_config?.model_name ?? "");
  const [llmTemp, setLlmTemp] = useState(String(agent.llm_config?.temperature ?? 0.7));
  const [llmMaxTokens, setLlmMaxTokens] = useState(String(agent.llm_config?.max_tokens ?? 4096));
  const [llmSystemPrompt, setLlmSystemPrompt] = useState(agent.llm_config?.system_prompt ?? "");
  const [llmToolsEnabled, setLlmToolsEnabled] = useState(agent.llm_config?.tools_enabled ?? true);
  const [llmNewApiKey, setLlmNewApiKey] = useState("");
  const [showApiKey, setShowApiKey] = useState(false);

  useEffect(() => {
    api.getPermissionPackages().then((data) => {
      setPackages(data.packages);
    }).catch(() => setPackages([]));
  }, []);

  useEffect(() => {
    setSelected(currentPermissions);
  }, [currentPermissions]);

  // Sync LLM config when agent updates
  useEffect(() => {
    if (agent.llm_config) {
      setLlmEndpoint(agent.llm_config.api_endpoint);
      setLlmModel(agent.llm_config.model_name);
      setLlmTemp(String(agent.llm_config.temperature));
      setLlmMaxTokens(String(agent.llm_config.max_tokens));
      setLlmSystemPrompt(agent.llm_config.system_prompt);
      setLlmToolsEnabled(agent.llm_config.tools_enabled);
    }
  }, [agent.llm_config]);

  const togglePermission = (id: string) => {
    setSelected((prev) => {
      if (id === "full-access") {
        return prev.includes(id) ? [] : ["full-access"];
      }
      const without = prev.filter((p) => p !== "full-access" && p !== id);
      if (prev.includes(id)) return without;
      return [...without, id];
    });
  };

  const hasChanges = JSON.stringify([...selected].sort()) !== JSON.stringify([...currentPermissions].sort());

  const handleSave = async () => {
    setSaving(true);
    setMessage(null);
    try {
      const result = await api.updateAgentPermissions(agentId, selected);
      if (result.warning) {
        setMessage({ type: "warning", text: result.warning });
      } else {
        setMessage({ type: "success", text: "Berechtigungen aktualisiert." });
      }
      const updated = await api.getAgent(agentId);
      onUpdated(updated as Agent);
    } catch (e) {
      setMessage({ type: "error", text: e instanceof Error ? e.message : "Fehler beim Speichern" });
    } finally {
      setSaving(false);
    }
  };

  const handleLlmSave = async () => {
    setLlmSaving(true);
    setMessage(null);
    try {
      const update: Record<string, unknown> = {
        api_endpoint: llmEndpoint.trim(),
        model_name: llmModel.trim(),
        temperature: parseFloat(llmTemp) || 0.7,
        max_tokens: parseInt(llmMaxTokens) || 4096,
        system_prompt: llmSystemPrompt.trim(),
        tools_enabled: llmToolsEnabled,
      };
      if (llmNewApiKey.trim()) {
        update.api_key = llmNewApiKey.trim();
      }
      await api.updateLLMConfig(agentId, update as Parameters<typeof api.updateLLMConfig>[1]);
      setLlmNewApiKey("");
      setLlmEditing(false);
      setMessage({ type: "success", text: "LLM-Konfiguration aktualisiert. Restart empfohlen." });
      const updated = await api.getAgent(agentId);
      onUpdated(updated as Agent);
    } catch (e) {
      setMessage({ type: "error", text: e instanceof Error ? e.message : "Fehler beim Speichern" });
    } finally {
      setLlmSaving(false);
    }
  };

  const providerLabel = agent.llm_config?.provider_type === "openai" ? "OpenAI" : agent.llm_config?.provider_type === "google" ? "Google" : agent.llm_config?.provider_type === "anthropic" ? "Anthropic" : agent.llm_config?.provider_type ?? "";

  return (
    <div className="space-y-6 overflow-auto max-h-[calc(100vh-22rem)] pb-4">
      {/* Proactive Mode */}
      <ProactiveToggle agentId={agentId} />

      {/* LLM Configuration (custom_llm only) */}
      {agent.mode === "custom_llm" && agent.llm_config && (
        <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm overflow-hidden">
          <div className="flex items-center justify-between border-b border-foreground/[0.06] px-5 py-3">
            <div className="flex items-center gap-2">
              <Plug className="h-4 w-4 text-violet-400" />
              <span className="text-sm font-medium">LLM-Konfiguration</span>
              <span className="text-[10px] px-2 py-0.5 rounded-full bg-violet-500/10 text-violet-400 border border-violet-500/20 font-medium">
                {providerLabel}
              </span>
            </div>
            {llmEditing ? (
              <div className="flex items-center gap-2">
                <button
                  onClick={handleLlmSave}
                  disabled={llmSaving}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-violet-600 px-4 py-1.5 text-[11px] font-medium text-white hover:bg-violet-500 disabled:opacity-40 transition-all"
                >
                  {llmSaving ? <Loader2 className="h-3 w-3 animate-spin" /> : <Save className="h-3 w-3" />}
                  Speichern
                </button>
                <button
                  onClick={() => {
                    setLlmEditing(false);
                    setLlmNewApiKey("");
                    // Reset to current values
                    if (agent.llm_config) {
                      setLlmEndpoint(agent.llm_config.api_endpoint);
                      setLlmModel(agent.llm_config.model_name);
                      setLlmTemp(String(agent.llm_config.temperature));
                      setLlmMaxTokens(String(agent.llm_config.max_tokens));
                      setLlmSystemPrompt(agent.llm_config.system_prompt);
                      setLlmToolsEnabled(agent.llm_config.tools_enabled);
                    }
                  }}
                  className="rounded-lg px-3 py-1.5 text-[11px] font-medium text-muted-foreground hover:text-foreground hover:bg-foreground/[0.06] transition-colors"
                >
                  Abbrechen
                </button>
              </div>
            ) : (
              <button
                onClick={() => setLlmEditing(true)}
                className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-[11px] font-medium text-muted-foreground hover:text-foreground hover:bg-foreground/[0.06] transition-colors"
              >
                <Edit3 className="h-3 w-3" />
                Bearbeiten
              </button>
            )}
          </div>

          <div className="p-5 space-y-4">
            {llmEditing ? (
              <>
                {/* Editable fields */}
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="block text-[11px] font-medium text-muted-foreground/70 mb-1.5">API Endpoint</label>
                    <input
                      type="text"
                      value={llmEndpoint}
                      onChange={(e) => setLlmEndpoint(e.target.value)}
                      className="w-full rounded-lg border border-foreground/[0.1] bg-background/80 px-3.5 py-2 text-sm font-mono outline-none focus:border-violet-500/50 focus:ring-1 focus:ring-violet-500/20 transition-all"
                    />
                  </div>
                  <div>
                    <label className="block text-[11px] font-medium text-muted-foreground/70 mb-1.5">Model</label>
                    <input
                      type="text"
                      value={llmModel}
                      onChange={(e) => setLlmModel(e.target.value)}
                      className="w-full rounded-lg border border-foreground/[0.1] bg-background/80 px-3.5 py-2 text-sm outline-none focus:border-violet-500/50 focus:ring-1 focus:ring-violet-500/20 transition-all"
                    />
                  </div>
                </div>

                <div>
                  <label className="block text-[11px] font-medium text-muted-foreground/70 mb-1.5">Temperature</label>
                  <input
                    type="number"
                    step="0.1"
                    min="0"
                    max="2"
                    value={llmTemp}
                    onChange={(e) => setLlmTemp(e.target.value)}
                    className="w-full max-w-[200px] rounded-lg border border-foreground/[0.1] bg-background/80 px-3.5 py-2 text-sm outline-none focus:border-violet-500/50 focus:ring-1 focus:ring-violet-500/20 transition-all tabular-nums"
                  />
                  <p className="text-[10px] text-muted-foreground/40 mt-1">0 = praezise, 1 = kreativ</p>
                </div>

                {/* API Key (change) */}
                <div>
                  <label className="block text-[11px] font-medium text-muted-foreground/70 mb-1.5">
                    API Key{" "}
                    <span className="text-muted-foreground/40">(leer lassen = beibehalten)</span>
                  </label>
                  <div className="relative">
                    <input
                      type={showApiKey ? "text" : "password"}
                      value={llmNewApiKey}
                      onChange={(e) => setLlmNewApiKey(e.target.value)}
                      placeholder="Neuen API Key eingeben..."
                      className="w-full rounded-lg border border-foreground/[0.1] bg-background/80 px-3.5 py-2 pr-10 text-sm font-mono outline-none focus:border-violet-500/50 focus:ring-1 focus:ring-violet-500/20 transition-all"
                    />
                    <button
                      type="button"
                      onClick={() => setShowApiKey(!showApiKey)}
                      className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted-foreground/50 hover:text-muted-foreground transition-colors"
                    >
                      {showApiKey ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
                    </button>
                  </div>
                </div>

                {/* System Prompt */}
                <div>
                  <label className="block text-[11px] font-medium text-muted-foreground/70 mb-1.5">System Prompt</label>
                  <textarea
                    value={llmSystemPrompt}
                    onChange={(e) => setLlmSystemPrompt(e.target.value)}
                    rows={4}
                    className="w-full rounded-lg border border-foreground/[0.1] bg-background/80 px-3.5 py-2 text-sm outline-none focus:border-violet-500/50 focus:ring-1 focus:ring-violet-500/20 transition-all resize-none"
                  />
                </div>

                {/* Tools toggle */}
                <div className="flex items-center justify-between rounded-lg border border-foreground/[0.06] bg-foreground/[0.02] px-4 py-3">
                  <div>
                    <p className="text-sm font-medium">Tool-Nutzung</p>
                    <p className="text-[11px] text-muted-foreground/70 mt-0.5">
                      Shell, Dateien, Memory, TODOs, Notifications, Team-Koordination
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => setLlmToolsEnabled(!llmToolsEnabled)}
                    className={cn(
                      "relative h-6 w-11 shrink-0 rounded-full transition-colors",
                      llmToolsEnabled ? "bg-violet-500" : "bg-foreground/20"
                    )}
                  >
                    <span
                      className={cn(
                        "absolute left-0.5 top-0.5 h-5 w-5 rounded-full bg-white shadow transition-transform",
                        llmToolsEnabled && "translate-x-5"
                      )}
                    />
                  </button>
                </div>
              </>
            ) : (
              <>
                {/* Read-only view */}
                <div className="grid grid-cols-2 gap-x-6 gap-y-3">
                  <div>
                    <span className="text-[11px] font-medium text-muted-foreground/60">Provider</span>
                    <p className="text-sm font-medium mt-0.5">{providerLabel}</p>
                  </div>
                  <div>
                    <span className="text-[11px] font-medium text-muted-foreground/60">Model</span>
                    <p className="text-sm font-medium mt-0.5 font-mono">{agent.llm_config.model_name}</p>
                  </div>
                  <div>
                    <span className="text-[11px] font-medium text-muted-foreground/60">API Endpoint</span>
                    <p className="text-sm font-mono text-muted-foreground mt-0.5 truncate">{agent.llm_config.api_endpoint}</p>
                  </div>
                  <div>
                    <span className="text-[11px] font-medium text-muted-foreground/60">API Key</span>
                    <p className="text-sm text-muted-foreground mt-0.5 font-mono">********</p>
                  </div>
                  <div>
                    <span className="text-[11px] font-medium text-muted-foreground/60">Temperature</span>
                    <p className="text-sm tabular-nums mt-0.5">{agent.llm_config.temperature}</p>
                  </div>
                  <div>
                    <span className="text-[11px] font-medium text-muted-foreground/60">Tools</span>
                    <p className="text-sm mt-0.5">
                      {agent.llm_config.tools_enabled ? (
                        <span className="inline-flex items-center gap-1 text-emerald-400">
                          <Check className="h-3 w-3" /> Aktiviert
                        </span>
                      ) : (
                        <span className="text-muted-foreground">Deaktiviert</span>
                      )}
                    </p>
                  </div>
                </div>
                {agent.llm_config.system_prompt && (
                  <div className="mt-2 pt-3 border-t border-foreground/[0.06]">
                    <span className="text-[11px] font-medium text-muted-foreground/60">System Prompt</span>
                    <pre className="mt-1.5 text-xs font-mono text-muted-foreground/80 whitespace-pre-wrap bg-foreground/[0.02] rounded-lg p-3 max-h-32 overflow-auto">
                      {agent.llm_config.system_prompt}
                    </pre>
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      )}

      {/* Telegram Bot */}
      <TelegramAgentSection agentId={agentId} />

      {/* Permissions */}
      <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm overflow-hidden">
        <div className="flex items-center justify-between border-b border-foreground/[0.06] px-5 py-3">
          <div className="flex items-center gap-2">
            <Package className="h-4 w-4 text-primary" />
            <span className="text-sm font-medium">Berechtigungen (Sudo-Pakete)</span>
          </div>
          <button
            onClick={handleSave}
            disabled={saving || !hasChanges}
            className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-4 py-1.5 text-[11px] font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-40 transition-all"
          >
            {saving ? <Loader2 className="h-3 w-3 animate-spin" /> : <Save className="h-3 w-3" />}
            Speichern
          </button>
        </div>

        <div className="p-5 space-y-2">
          {packages.map((pkg) => {
            const Icon = PERM_ICON_MAP[pkg.icon] || Package;
            const isSelected = selected.includes(pkg.id);
            const isFullAccess = pkg.id === "full-access";

            return (
              <button
                key={pkg.id}
                type="button"
                onClick={() => togglePermission(pkg.id)}
                className={cn(
                  "w-full flex items-start gap-3 rounded-xl border p-3.5 text-left transition-all duration-200",
                  isSelected
                    ? isFullAccess
                      ? "border-amber-500/40 bg-amber-500/[0.08]"
                      : "border-primary/40 bg-primary/[0.08]"
                    : "border-foreground/[0.06] bg-foreground/[0.02] hover:bg-foreground/[0.04]"
                )}
              >
                <div
                  className={cn(
                    "flex h-9 w-9 shrink-0 items-center justify-center rounded-lg transition-colors",
                    isSelected
                      ? isFullAccess
                        ? "bg-amber-500/20 text-amber-400"
                        : "bg-primary/20 text-primary"
                      : "bg-foreground/[0.06] text-muted-foreground"
                  )}
                >
                  <Icon className="h-4 w-4" />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span
                      className={cn(
                        "text-sm font-medium",
                        isSelected ? "text-foreground" : "text-muted-foreground"
                      )}
                    >
                      {pkg.label}
                    </span>
                    {pkg.default && !isFullAccess && (
                      <span className="text-[10px] font-medium uppercase tracking-wider text-primary/60 bg-primary/10 px-1.5 py-0.5 rounded">
                        Default
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-muted-foreground/70 mt-0.5">
                    {pkg.description}
                  </p>
                </div>
                <div
                  className={cn(
                    "flex h-5 w-5 shrink-0 items-center justify-center rounded-md border transition-all mt-0.5",
                    isSelected
                      ? isFullAccess
                        ? "border-amber-500 bg-amber-500 text-white"
                        : "border-primary bg-primary text-white"
                      : "border-foreground/20"
                  )}
                >
                  {isSelected && <Check className="h-3 w-3" />}
                </div>
              </button>
            );
          })}

          {packages.length === 0 && (
            <div className="flex items-center justify-center py-6">
              <Loader2 className="h-4 w-4 animate-spin text-muted-foreground mr-2" />
              <span className="text-xs text-muted-foreground/50">Berechtigungspakete werden geladen...</span>
            </div>
          )}
        </div>

        <div className="px-5 pb-4">
          <p className="text-[11px] text-muted-foreground/50">
            Ohne Auswahl: nur pip/npm install (kein sudo). Basis-Tools (git, curl, node) sind immer verfuegbar.
          </p>
        </div>
      </div>

      {/* Status messages */}
      {message && (
        <div className={cn(
          "rounded-lg border px-4 py-2.5 text-sm",
          message.type === "success" && "border-emerald-500/20 bg-emerald-500/10 text-emerald-400",
          message.type === "warning" && "border-amber-500/20 bg-amber-500/10 text-amber-400",
          message.type === "error" && "border-red-500/20 bg-red-500/10 text-red-400",
        )}>
          {message.text}
        </div>
      )}
    </div>
  );
}

type FileSortMode = "name" | "date" | "size";

function FileBrowser({ agentId }: { agentId: string }) {
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [treeData, setTreeData] = useState<Record<string, FileEntry[]>>({});
  const [expanded, setExpanded] = useState<Set<string>>(new Set(["/workspace"]));
  const [selectedFile, setSelectedFile] = useState<FileEntry | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [sortMode, setSortMode] = useState<FileSortMode>("name");
  const fileInputRef = useRef<HTMLInputElement>(null);

  const loadDir = async (path: string) => {
    try {
      const data = await api.getFiles(agentId, path);
      setTreeData((prev) => ({ ...prev, [path]: data.entries }));
    } catch {
      setTreeData((prev) => ({ ...prev, [path]: [] }));
    }
  };

  useEffect(() => {
    setLoading(true);
    loadDir("/workspace").finally(() => setLoading(false));
  }, [agentId]);

  const toggleDir = async (path: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(path)) {
        Array.from(next).forEach((p) => {
          if (p.startsWith(path) && p !== "/workspace") next.delete(p);
        });
      } else {
        next.add(path);
      }
      return next;
    });
    if (!treeData[path]) {
      await loadDir(path);
    }
  };

  const refreshAll = async () => {
    setLoading(true);
    const paths = Array.from(new Set(["/workspace"].concat(Array.from(expanded))));
    await Promise.all(paths.map(loadDir));
    setLoading(false);
  };

  const handleUpload = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    setUploading(true);
    try {
      await api.uploadFiles(agentId, "/workspace", files);
      await refreshAll();
    } catch (e) {
      alert(e instanceof Error ? e.message : "Upload failed");
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const handleDownload = (path: string) => {
    const url = api.getFileDownloadUrl(agentId, path);
    window.open(url, "_blank");
  };

  const sortEntries = (entries: FileEntry[]) =>
    [...entries].sort((a, b) => {
      if (a.type !== b.type) return a.type === "directory" ? -1 : 1;
      if (sortMode === "date") return (b.modified || 0) - (a.modified || 0);
      if (sortMode === "size") return (b.size || 0) - (a.size || 0);
      return a.name.localeCompare(b.name);
    });

  // Search across all loaded files
  const searchResults = useMemo(() => {
    if (!searchQuery.trim()) return [];
    const query = searchQuery.toLowerCase();
    const results: FileEntry[] = [];
    for (const entries of Object.values(treeData)) {
      for (const entry of entries) {
        if (entry.type === "file" && entry.name.toLowerCase().includes(query)) {
          results.push(entry);
        }
      }
    }
    return sortEntries(results);
  }, [searchQuery, treeData, sortMode]);

  const renderTree = (path: string, depth: number): React.ReactNode => {
    const entries = treeData[path];
    if (!entries) return null;

    return sortEntries(entries).map((entry) => {
      const isDir = entry.type === "directory";
      const isExpanded = expanded.has(entry.path);
      const isSelected = selectedFile?.path === entry.path;

      return (
        <div key={entry.path}>
          <div
            className={cn(
              "flex items-center gap-2 py-1 px-3 hover:bg-foreground/[0.04] transition-colors cursor-pointer group",
              isSelected && "bg-primary/10 border-r-2 border-primary"
            )}
            style={{ paddingLeft: `${depth * 18 + 12}px` }}
            onClick={() => isDir ? toggleDir(entry.path) : setSelectedFile(entry)}
          >
            {isDir ? (
              <ChevronRight className={cn(
                "h-3 w-3 text-muted-foreground/50 transition-transform duration-150 shrink-0",
                isExpanded && "rotate-90"
              )} />
            ) : (
              <span className="w-3 shrink-0" />
            )}
            {isDir ? (
              isExpanded ? (
                <FolderOpen className="h-3.5 w-3.5 text-amber-400/70 shrink-0" />
              ) : (
                <Folder className="h-3.5 w-3.5 text-amber-400/70 shrink-0" />
              )
            ) : (
              <File className={cn("h-3.5 w-3.5 shrink-0", getFileColor(entry.name))} />
            )}
            <span className="text-[12px] truncate flex-1 min-w-0">{entry.name}</span>
            {!isDir && entry.modified > 0 && (
              <span className="text-[10px] text-muted-foreground/30 tabular-nums shrink-0" title={formatModifiedFull(entry.modified)}>
                {formatModified(entry.modified)}
              </span>
            )}
            {!isDir && (
              <span className="text-[10px] text-muted-foreground/40 tabular-nums shrink-0 w-12 text-right">
                {formatFileSize(entry.size)}
              </span>
            )}
            {!isDir && (
              <button
                onClick={(e) => { e.stopPropagation(); handleDownload(entry.path); }}
                className="flex h-5 w-5 items-center justify-center rounded text-muted-foreground/30 hover:text-foreground opacity-0 group-hover:opacity-100 transition-all shrink-0"
                title="Download"
              >
                <Download className="h-2.5 w-2.5" />
              </button>
            )}
            {isDir && isExpanded && !treeData[entry.path] && (
              <Loader2 className="h-3 w-3 animate-spin text-muted-foreground/40 shrink-0" />
            )}
          </div>
          {isDir && isExpanded && treeData[entry.path] && renderTree(entry.path, depth + 1)}
        </div>
      );
    });
  };

  return (
    <div className="flex gap-4 h-full">
      {/* Tree panel */}
      <div className="w-[380px] shrink-0 rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm flex flex-col overflow-hidden">
        {/* Search + Sort + Toolbar */}
        <div className="border-b border-foreground/[0.06] px-3 py-2 space-y-2">
          <div className="flex items-center gap-2">
            <div className="relative flex-1">
              <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground/40" />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Dateien suchen..."
                className="w-full rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] pl-8 pr-8 py-1.5 text-[12px] placeholder:text-muted-foreground/30 focus:outline-none focus:border-primary/30 transition-colors"
              />
              {searchQuery && (
                <button
                  onClick={() => setSearchQuery("")}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground/40 hover:text-foreground"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              )}
            </div>
            <button
              onClick={refreshAll}
              className="flex h-7 w-7 items-center justify-center rounded-lg text-muted-foreground/40 hover:text-foreground hover:bg-foreground/[0.06] transition-colors shrink-0"
              title="Refresh"
            >
              <RefreshCw className="h-3 w-3" />
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
              className="flex h-7 items-center gap-1 rounded-lg bg-primary px-2.5 text-[11px] font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors shrink-0"
            >
              {uploading ? <Loader2 className="h-3 w-3 animate-spin" /> : <Upload className="h-3 w-3" />}
              Upload
            </button>
          </div>
          <div className="flex items-center gap-1">
            {(["name", "date", "size"] as FileSortMode[]).map((mode) => (
              <button
                key={mode}
                onClick={() => setSortMode(mode)}
                className={cn(
                  "inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-[10px] font-medium transition-colors",
                  sortMode === mode
                    ? "bg-primary/10 text-primary"
                    : "text-muted-foreground/50 hover:text-muted-foreground hover:bg-foreground/[0.04]"
                )}
              >
                {mode === "name" && <><Hash className="h-2.5 w-2.5" /> Name</>}
                {mode === "date" && <><Clock className="h-2.5 w-2.5" /> Datum</>}
                {mode === "size" && <><ArrowUpDown className="h-2.5 w-2.5" /> Groesse</>}
              </button>
            ))}
          </div>
        </div>

        {/* Tree content */}
        <div className="flex-1 overflow-y-auto py-1 font-mono">
          {loading ? (
            <div className="flex items-center justify-center h-full">
              <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
            </div>
          ) : searchQuery.trim() ? (
            searchResults.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-32 text-muted-foreground/30">
                <Search className="h-5 w-5 mb-2" />
                <span className="text-[11px]">Keine Treffer</span>
              </div>
            ) : (
              <div className="py-1">
                <div className="px-3 py-1 text-[10px] text-muted-foreground/40 font-sans">
                  {searchResults.length} Treffer
                </div>
                {searchResults.map((entry) => {
                  const isSelected = selectedFile?.path === entry.path;
                  return (
                    <div
                      key={entry.path}
                      className={cn(
                        "flex items-center gap-2 py-1.5 px-3 hover:bg-foreground/[0.04] transition-colors cursor-pointer",
                        isSelected && "bg-primary/10 border-r-2 border-primary"
                      )}
                      onClick={() => setSelectedFile(entry)}
                    >
                      <File className={cn("h-3.5 w-3.5 shrink-0", getFileColor(entry.name))} />
                      <div className="flex flex-col flex-1 min-w-0">
                        <span className="text-[12px] truncate">{entry.name}</span>
                        <span className="text-[10px] text-muted-foreground/30 truncate font-sans">{entry.path}</span>
                      </div>
                      {entry.modified > 0 && (
                        <span className="text-[10px] text-muted-foreground/30 tabular-nums shrink-0">
                          {formatModified(entry.modified)}
                        </span>
                      )}
                      <span className="text-[10px] text-muted-foreground/40 tabular-nums shrink-0 w-12 text-right">
                        {formatFileSize(entry.size)}
                      </span>
                    </div>
                  );
                })}
              </div>
            )
          ) : !treeData["/workspace"] || treeData["/workspace"].length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full text-muted-foreground/50 font-sans">
              <FolderOpen className="h-8 w-8 mb-2" />
              <p className="text-sm">Leerer Workspace</p>
            </div>
          ) : (
            renderTree("/workspace", 0)
          )}
        </div>
      </div>

      {/* File preview panel */}
      <div className="flex-1 rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm flex flex-col overflow-hidden">
        {selectedFile ? (
          <FilePreview
            key={selectedFile.path}
            fileUrl={api.getFileDownloadUrl(agentId, selectedFile.path)}
            filePath={selectedFile.path}
            fileSize={selectedFile.size}
            fileModified={selectedFile.modified}
            onDownload={() => handleDownload(selectedFile.path)}
          />
        ) : (
          <FilePreviewEmpty />
        )}
      </div>
    </div>
  );
}

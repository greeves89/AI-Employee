"use client";

import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import { Plus, Play, Square, Trash2, Loader2, Bot, LayoutGrid, Network, StopCircle, Sparkles, ArrowUpCircle } from "lucide-react";
import { useAgents } from "@/hooks/use-agents";
import { Header } from "@/components/layout/header";
import { AgentCard } from "@/components/dashboard/agent-card";
import { CreateAgentModal } from "@/components/agents/create-agent-modal";
import { AgentNetworkView } from "@/components/agents/agent-network-view";
import { cn } from "@/lib/utils";
import * as api from "@/lib/api";
import type { AgentTemplate } from "@/lib/types";

type ViewMode = "grid" | "network";

const TEMPLATE_CATEGORY_COLORS: Record<string, string> = {
  dev: "from-blue-500/10 to-blue-500/5 border-blue-500/20",
  data: "from-emerald-500/10 to-emerald-500/5 border-emerald-500/20",
  writing: "from-purple-500/10 to-purple-500/5 border-purple-500/20",
  ops: "from-amber-500/10 to-amber-500/5 border-amber-500/20",
  creative: "from-pink-500/10 to-pink-500/5 border-pink-500/20",
  general: "from-foreground/[0.06] to-foreground/[0.03] border-foreground/[0.08]",
  marketing: "from-orange-500/10 to-orange-500/5 border-orange-500/20",
  support: "from-cyan-500/10 to-cyan-500/5 border-cyan-500/20",
  sales: "from-rose-500/10 to-rose-500/5 border-rose-500/20",
  management: "from-indigo-500/10 to-indigo-500/5 border-indigo-500/20",
  security: "from-red-500/10 to-red-500/5 border-red-500/20",
};

export default function AgentsPage() {
  const { agents, loading, refresh } = useAgents();
  const [showCreate, setShowCreate] = useState(false);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<ViewMode>("grid");
  const [templates, setTemplates] = useState<AgentTemplate[]>([]);
  const [startingTemplate, setStartingTemplate] = useState<number | null>(null);

  useEffect(() => {
    api.getTemplates().then(d => setTemplates(d.templates.filter(t => t.is_published))).catch(() => {});
  }, []);

  const handleStartTemplate = async (template: AgentTemplate) => {
    setStartingTemplate(template.id);
    try {
      await api.createAgentFromTemplate(template.id);
      await refresh();
    } catch (e) {
      alert(`Fehler: ${e}`);
    } finally {
      setStartingTemplate(null);
    }
  };

  const [stoppingAll, setStoppingAll] = useState(false);
  const [updatingAll, setUpdatingAll] = useState(false);
  const [updatingAgent, setUpdatingAgent] = useState<string | null>(null);

  const agentsNeedingUpdate = agents.filter((a) => a.update_available);

  const handleUpdateAll = async () => {
    if (!confirm(`${agentsNeedingUpdate.length} Agent(s) auf die neueste Version aktualisieren?`)) return;
    setUpdatingAll(true);
    try {
      await Promise.all(agentsNeedingUpdate.map((a) => api.updateAgent(a.id)));
      await refresh();
    } finally {
      setUpdatingAll(false);
    }
  };

  const handleUpdateAgent = async (id: string) => {
    setUpdatingAgent(id);
    try {
      await api.updateAgent(id);
      await refresh();
    } finally {
      setUpdatingAgent(null);
    }
  };

  const handleStopAll = async () => {
    if (!confirm("Alle Agents stoppen?")) return;
    setStoppingAll(true);
    try {
      const running = agents.filter((a) => ["running", "idle", "working"].includes(a.state));
      await Promise.all(running.map((a) => api.stopAgent(a.id)));
      await refresh();
    } finally {
      setStoppingAll(false);
    }
  };

  const handleStop = async (id: string) => {
    setActionLoading(id);
    try {
      await api.stopAgent(id);
      await refresh();
    } finally {
      setActionLoading(null);
    }
  };

  const handleStart = async (id: string) => {
    setActionLoading(id);
    try {
      await api.startAgent(id);
      await refresh();
    } finally {
      setActionLoading(null);
    }
  };

  const handleRemove = async (id: string) => {
    if (!confirm("Remove this agent? This will stop and remove the container.")) return;
    setActionLoading(id);
    try {
      await api.removeAgent(id);
      await refresh();
    } finally {
      setActionLoading(null);
    }
  };

  return (
    <div>
      <Header
        title="Agents"
        subtitle="Manage your Claude Code agent containers"
        actions={
          <div className="flex items-center gap-2">
            {/* View mode toggle */}
            <div className="flex items-center rounded-lg border border-foreground/[0.06] bg-card/50 p-0.5">
              <button
                onClick={() => setViewMode("grid")}
                className={cn(
                  "rounded-lg px-2.5 py-2 text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04] transition-all duration-200",
                  viewMode === "grid" && "bg-foreground/[0.08] text-foreground"
                )}
                title="Grid View"
              >
                <LayoutGrid className="h-4 w-4" />
              </button>
              <button
                onClick={() => setViewMode("network")}
                className={cn(
                  "rounded-lg px-2.5 py-2 text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04] transition-all duration-200",
                  viewMode === "network" && "bg-foreground/[0.08] text-foreground"
                )}
                title="Network View"
              >
                <Network className="h-4 w-4" />
              </button>
            </div>

            {/* Update All — only visible when at least one agent has an update */}
            {agentsNeedingUpdate.length > 0 && (
              <button
                onClick={handleUpdateAll}
                disabled={updatingAll}
                className="inline-flex items-center gap-2 rounded-xl bg-amber-500/10 border border-amber-500/20 px-4 py-2.5 text-sm font-medium text-amber-400 hover:bg-amber-500/20 disabled:opacity-50 transition-all duration-200"
              >
                {updatingAll ? <Loader2 className="h-4 w-4 animate-spin" /> : <ArrowUpCircle className="h-4 w-4" />}
                Update All ({agentsNeedingUpdate.length})
              </button>
            )}

            {/* Stop All */}
            {agents.some((a) => ["running", "idle", "working"].includes(a.state)) && (
              <button
                onClick={handleStopAll}
                disabled={stoppingAll}
                className="inline-flex items-center gap-2 rounded-xl bg-red-500/10 border border-red-500/20 px-4 py-2.5 text-sm font-medium text-red-400 hover:bg-red-500/20 disabled:opacity-50 transition-all duration-200"
              >
                {stoppingAll ? <Loader2 className="h-4 w-4 animate-spin" /> : <StopCircle className="h-4 w-4" />}
                Stop All
              </button>
            )}

            <button
              onClick={() => setShowCreate(true)}
              className="inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground shadow-lg shadow-primary/20 hover:bg-primary/90 transition-all duration-200"
            >
              <Plus className="h-4 w-4" />
              New Agent
            </button>
          </div>
        }
      />

      {/* Agent Creation Modal */}
      <CreateAgentModal
        open={showCreate}
        onOpenChange={setShowCreate}
        onCreated={refresh}
      />

      {/* Published templates — shown when templates are available */}
      {templates.length > 0 && (
        <motion.div
          className="px-8 pt-6"
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3 }}
        >
          <div className="flex items-center gap-2 mb-3">
            <Sparkles className="h-4 w-4 text-primary/70" />
            <h2 className="text-sm font-medium text-muted-foreground">Verfügbare Vorlagen</h2>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-3">
            {templates.map(t => (
              <div
                key={t.id}
                className={cn(
                  "rounded-xl border bg-gradient-to-br p-4 flex items-start gap-3",
                  TEMPLATE_CATEGORY_COLORS[t.category] || TEMPLATE_CATEGORY_COLORS.general,
                )}
              >
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium truncate">{t.display_name}</p>
                  {t.description && (
                    <p className="text-xs text-muted-foreground/60 mt-0.5 line-clamp-2">{t.description}</p>
                  )}
                  <p className="text-[10px] text-muted-foreground/40 mt-1">{t.model}</p>
                </div>
                <button
                  onClick={() => handleStartTemplate(t)}
                  disabled={startingTemplate === t.id}
                  className="shrink-0 flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground shadow-sm shadow-primary/20 hover:bg-primary/90 disabled:opacity-50 transition-all"
                >
                  {startingTemplate === t.id
                    ? <Loader2 className="h-3 w-3 animate-spin" />
                    : <Play className="h-3 w-3" />}
                  Starten
                </button>
              </div>
            ))}
          </div>
        </motion.div>
      )}

      <motion.div
        className="px-8 py-8"
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
      >
        {loading && agents.length === 0 ? (
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            {[1, 2, 3].map((i) => (
              <div
                key={i}
                className="rounded-xl border border-foreground/[0.06] bg-card/50 p-5 h-48 animate-shimmer bg-[length:200%_100%] bg-gradient-to-r from-foreground/[0.03] via-foreground/[0.06] to-foreground/[0.03]"
              />
            ))}
          </div>
        ) : viewMode === "network" ? (
          <AgentNetworkView agents={agents} />
        ) : agents.length === 0 ? (
          <div className="rounded-xl border border-dashed border-foreground/[0.1] bg-card/30 p-16 text-center">
            <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-foreground/[0.06] mb-4">
              <Bot className="h-7 w-7 text-muted-foreground" />
            </div>
            <h3 className="text-lg font-semibold mb-1.5">No agents yet</h3>
            <p className="text-sm text-muted-foreground mb-5">
              Create your first agent to start running autonomous tasks.
            </p>
            <button
              onClick={() => setShowCreate(true)}
              className="inline-flex items-center gap-2 rounded-xl bg-primary px-5 py-2.5 text-sm font-medium text-primary-foreground shadow-lg shadow-primary/20 hover:bg-primary/90 transition-all"
            >
              <Plus className="h-4 w-4" />
              Create Agent
            </button>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            {agents.map((agent, i) => (
              <motion.div
                key={agent.id}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: i * 0.06, duration: 0.25 }}
                className="relative group h-full"
              >
                <AgentCard agent={agent} />

                {/* Floating action buttons */}
                <div className="absolute top-3 right-3 flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity duration-200 z-10">
                  {actionLoading === agent.id || updatingAgent === agent.id ? (
                    <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-card/90 backdrop-blur-md shadow-sm">
                      <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />
                    </div>
                  ) : (
                    <>
                      {agent.update_available && (
                        <button
                          onClick={(e) => { e.preventDefault(); e.stopPropagation(); handleUpdateAgent(agent.id); }}
                          className="flex h-7 w-7 items-center justify-center rounded-lg bg-card/90 backdrop-blur-md shadow-sm text-amber-400 hover:bg-amber-500/15 transition-colors"
                          title="Update agent"
                        >
                          <ArrowUpCircle className="h-3.5 w-3.5" />
                        </button>
                      )}
                      {agent.state === "stopped" ? (
                        <button
                          onClick={(e) => { e.preventDefault(); e.stopPropagation(); handleStart(agent.id); }}
                          className="flex h-7 w-7 items-center justify-center rounded-lg bg-card/90 backdrop-blur-md shadow-sm text-muted-foreground hover:text-emerald-400 hover:bg-emerald-500/15 transition-colors"
                          title="Start"
                        >
                          <Play className="h-3.5 w-3.5" />
                        </button>
                      ) : (
                        <button
                          onClick={(e) => { e.preventDefault(); e.stopPropagation(); handleStop(agent.id); }}
                          className="flex h-7 w-7 items-center justify-center rounded-lg bg-card/90 backdrop-blur-md shadow-sm text-muted-foreground hover:text-amber-400 hover:bg-amber-500/15 transition-colors"
                          title="Stop"
                        >
                          <Square className="h-3.5 w-3.5" />
                        </button>
                      )}
                      <button
                        onClick={(e) => { e.preventDefault(); e.stopPropagation(); handleRemove(agent.id); }}
                        className="flex h-7 w-7 items-center justify-center rounded-lg bg-card/90 backdrop-blur-md shadow-sm text-muted-foreground hover:text-red-400 hover:bg-red-500/15 transition-colors"
                        title="Remove"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </>
                  )}
                </div>
              </motion.div>
            ))}
          </div>
        )}
      </motion.div>
    </div>
  );
}

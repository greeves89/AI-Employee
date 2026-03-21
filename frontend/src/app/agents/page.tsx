"use client";

import { useState } from "react";
import { motion } from "framer-motion";
import { Plus, Play, Square, Trash2, Loader2, Bot, LayoutGrid, Network, StopCircle } from "lucide-react";
import { useAgents } from "@/hooks/use-agents";
import { Header } from "@/components/layout/header";
import { AgentCard } from "@/components/dashboard/agent-card";
import { CreateAgentModal } from "@/components/agents/create-agent-modal";
import { AgentNetworkView } from "@/components/agents/agent-network-view";
import { cn } from "@/lib/utils";
import * as api from "@/lib/api";

type ViewMode = "grid" | "network";

export default function AgentsPage() {
  const { agents, loading, refresh } = useAgents();
  const [showCreate, setShowCreate] = useState(false);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<ViewMode>("grid");

  const [stoppingAll, setStoppingAll] = useState(false);

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
                  {actionLoading === agent.id ? (
                    <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-card/90 backdrop-blur-md shadow-sm">
                      <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />
                    </div>
                  ) : (
                    <>
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

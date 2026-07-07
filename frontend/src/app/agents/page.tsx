"use client";

import { useState, useEffect, useMemo } from "react";
import dynamic from "next/dynamic";
import { motion } from "framer-motion";
import { Plus, Play, Square, Trash2, Loader2, Bot, LayoutGrid, Network, Users, StopCircle, ArrowUpCircle, Crown } from "lucide-react";
import { useAgents } from "@/hooks/use-agents";
import { Header } from "@/components/layout/header";
import { AgentCard } from "@/components/dashboard/agent-card";
import { cn } from "@/lib/utils";
import * as api from "@/lib/api";
import type { AgentTeam } from "@/lib/api";
import { useConfirm } from "@/components/ui/dialog-provider";
type ViewMode = "grid" | "network" | "teams";

const CreateAgentModal = dynamic(
  () => import("@/components/agents/create-agent-modal").then((m) => m.CreateAgentModal),
  { ssr: false },
);

const TeamsSection = dynamic(
  () => import("@/components/agents/teams-section").then((m) => m.TeamsSection),
  { ssr: false },
);

const AgentNetworkView = dynamic(
  () => import("@/components/agents/agent-network-view").then((m) => m.AgentNetworkView),
  {
    ssr: false,
    loading: () => (
      <div className="rounded-xl border border-foreground/[0.06] bg-card/50 p-8 text-sm text-muted-foreground">
        Loading network view...
      </div>
    ),
  },
);

export default function AgentsPage() {
  const { agents, loading, refresh } = useAgents();
  const confirm = useConfirm();
  const [showCreate, setShowCreate] = useState(false);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<ViewMode>("grid");
  const [stoppingAll, setStoppingAll] = useState(false);
  const [startingAll, setStartingAll] = useState(false);
  const [updatingAll, setUpdatingAll] = useState(false);
  // Agents currently being updated — a Set so "Update All" can spin every card
  // independently (each clears the moment its own update finishes).
  const [updatingAgents, setUpdatingAgents] = useState<Set<string>>(new Set());

  const markUpdating = (id: string, on: boolean) =>
    setUpdatingAgents((prev) => {
      const next = new Set(prev);
      if (on) next.add(id); else next.delete(id);
      return next;
    });

  // Update one agent: spin its card, then refresh so its "Update" badge clears.
  const updateOne = async (id: string) => {
    markUpdating(id, true);
    try {
      await api.updateAgent(id);
      await refresh();
    } finally {
      markUpdating(id, false);
    }
  };

  const agentsNeedingUpdate = agents.filter((a) => a.update_available);

  const [teams, setTeams] = useState<AgentTeam[]>([]);
  useEffect(() => {
    api.getTeams().then((d) => setTeams(d.teams || [])).catch(() => {});
  }, []);

  // Group agents by team for the grid view (teams first, "Ohne Team" last)
  const agentGroups = useMemo(() => {
    const teamOf: Record<string, AgentTeam> = {};
    for (const t of teams) for (const m of t.member_agent_ids) if (!teamOf[m]) teamOf[m] = t;
    const byKey: Record<string, { key: string; name: string; isTeam: boolean; leadName: string | null; agents: typeof agents }> = {};
    for (const a of agents) {
      const t = teamOf[a.id];
      const key = t ? t.id : "__none__";
      if (!byKey[key]) {
        const leadName = t?.lead_agent_id ? (agents.find((x) => x.id === t.lead_agent_id)?.name ?? null) : null;
        byKey[key] = { key, name: t ? t.name : "Ohne Team", isTeam: !!t, leadName, agents: [] };
      }
      byKey[key].agents.push(a);
    }
    return Object.values(byKey).sort((a, b) => (a.isTeam === b.isTeam ? 0 : a.isTeam ? -1 : 1));
  }, [agents, teams]);

  const handleUpdateAll = async () => {
    const ok = await confirm({
      title: `${agentsNeedingUpdate.length} Agent(s) aktualisieren?`,
      message: "Alle markierten Agents werden auf die neueste Version aktualisiert. Daten bleiben erhalten.",
      variant: "warning",
      confirmLabel: "Update",
    });
    if (!ok) return;
    setUpdatingAll(true);
    try {
      // Each card spins on its own and clears as soon as ITS update completes.
      await Promise.all(agentsNeedingUpdate.map((a) => updateOne(a.id)));
    } finally {
      setUpdatingAll(false);
    }
  };

  const handleUpdateAgent = async (id: string) => {
    await updateOne(id);
  };

  const handleStopAll = async () => {
    const ok = await confirm({
      title: "Alle Agents stoppen?",
      message: "Alle aktuell laufenden Agents werden gestoppt.",
      variant: "warning",
      confirmLabel: "Alle stoppen",
    });
    if (!ok) return;
    setStoppingAll(true);
    try {
      const running = agents.filter((a) => ["running", "idle", "working"].includes(a.state));
      await Promise.all(running.map((a) => api.stopAgent(a.id)));
      await refresh();
    } finally {
      setStoppingAll(false);
    }
  };

  const handleStartAll = async () => {
    const stopped = agents.filter((a) => ["stopped", "created", "error"].includes(a.state));
    if (stopped.length === 0) return;
    const ok = await confirm({
      title: `${stopped.length} Agent(s) starten?`,
      message: "Alle gestoppten Agents werden gestartet.",
      variant: "default",
      confirmLabel: "Alle starten",
    });
    if (!ok) return;
    setStartingAll(true);
    try {
      await Promise.all(stopped.map((a) => api.startAgent(a.id)));
      await refresh();
    } finally {
      setStartingAll(false);
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
    const ok = await confirm({
      title: "Remove this agent?",
      message: "The container will be stopped and removed. This action cannot be undone.",
      variant: "destructive",
      confirmLabel: "Remove",
    });
    if (!ok) return;
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
              <button
                onClick={() => setViewMode("teams")}
                className={cn(
                  "rounded-lg px-2.5 py-2 text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04] transition-all duration-200",
                  viewMode === "teams" && "bg-foreground/[0.08] text-foreground"
                )}
                title="Teams View"
              >
                <Users className="h-4 w-4" />
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

            {/* Start All */}
            {agents.some((a) => ["stopped", "created", "error"].includes(a.state)) && (
              <button
                onClick={handleStartAll}
                disabled={startingAll}
                className="inline-flex items-center gap-2 rounded-xl bg-emerald-500/10 border border-emerald-500/20 px-4 py-2.5 text-sm font-medium text-emerald-400 hover:bg-emerald-500/20 disabled:opacity-50 transition-all duration-200"
              >
                {startingAll ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
                Start All
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


      <motion.div
        className="px-8 py-8"
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
      >
        {viewMode === "teams" ? (
          <TeamsSection agents={agents} />
        ) : loading && agents.length === 0 ? (
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
          <div className="space-y-7">
            {agentGroups.map((g) => (
            <section key={g.key}>
              {(agentGroups.length > 1 || g.isTeam) && (
                <div className="flex items-center gap-2 mb-3">
                  {g.isTeam ? <Users className="h-4 w-4 text-violet-400" /> : <Bot className="h-4 w-4 text-muted-foreground" />}
                  <h3 className="text-sm font-semibold">{g.name}</h3>
                  {g.leadName && <span className="flex items-center gap-1 text-[11px] text-amber-400"><Crown className="h-3 w-3" /> {g.leadName} · Lead</span>}
                  <span className="text-[11px] text-muted-foreground">· {g.agents.length} Agent{g.agents.length !== 1 ? "s" : ""}</span>
                </div>
              )}
              <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
                {g.agents.map((agent, i) => (
              <motion.div
                key={agent.id}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: i * 0.06, duration: 0.25 }}
                className="relative group h-full"
              >
                <AgentCard agent={agent} updating={updatingAgents.has(agent.id)} />

                {/* Floating action buttons */}
                <div className="absolute top-3 right-3 flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity duration-200 z-10">
                  {actionLoading === agent.id || updatingAgents.has(agent.id) ? (
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
            </section>
            ))}
          </div>
        )}
      </motion.div>
    </div>
  );
}

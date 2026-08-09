"use client";

import { useState, useEffect, useMemo } from "react";
import dynamic from "next/dynamic";
import { motion } from "framer-motion";
import { Plus, Play, Square, Trash2, Loader2, Bot, LayoutGrid, Network, Users, StopCircle, ArrowUpCircle, Crown, RotateCw } from "lucide-react";
import { useAgents } from "@/hooks/use-agents";
import { Header } from "@/components/layout/header";
import { AgentCard } from "@/components/dashboard/agent-card";
import { cn } from "@/lib/utils";
import * as api from "@/lib/api";
import type { AgentTeam } from "@/lib/api";
import { useConfirm } from "@/components/ui/dialog-provider";
import { getAgentTag } from "@/components/agents/agent-avatar";
import { AgentFilterBar, type GroupBy, type SortBy } from "@/components/agents/agent-filter-bar";
type ViewMode = "grid" | "network" | "teams";

/** Die Begründung aus einer 409-Antwort herausholen.
 *
 *  Der Fehlertext ist „API Error 409: {json}". Ohne das stünde im Dialog eine
 *  JSON-Zeile — und der Grund, weshalb blockiert wurde, wäre unlesbar. */
function extractGateMessage(raw: string): string {
  const start = raw.indexOf("{");
  if (start < 0) return raw;
  try {
    const parsed = JSON.parse(raw.slice(start));
    return String(parsed?.detail?.message ?? parsed?.detail ?? raw);
  } catch {
    return raw;
  }
}

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
  const [restartingAll, setRestartingAll] = useState(false);
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
  //
  // Blockiert das Golden-Test-Gatter (#391), kommt ein 409 zurueck. Das muss man
  // sehen UND ueberstimmen koennen: ein Gatter ohne Notausgang wird beim ersten
  // dringenden Fall umgangen, und dann dauerhaft abgeschaltet.
  const updateOne = async (id: string) => {
    markUpdating(id, true);
    try {
      await api.updateAgent(id);
      await refresh();
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      if (!message.includes("409")) throw e;
      const ok = await confirm({
        title: "Golden-Tests schlagen Alarm",
        message:
          `${extractGateMessage(message)}\n\n` +
          "Trotzdem aktualisieren? Der Rückschritt wäre danach draußen.",
        variant: "warning",
        confirmLabel: "Trotzdem aktualisieren",
      });
      if (ok) {
        await api.updateAgent(id, true);
        await refresh();
      }
    } finally {
      markUpdating(id, false);
    }
  };

  const agentsNeedingUpdate = agents.filter((a) => a.update_available);

  const [teams, setTeams] = useState<AgentTeam[]>([]);
  useEffect(() => {
    api.getTeams().then((d) => setTeams(d.teams || [])).catch(() => {});
  }, []);

  // Suchen, filtern, sortieren (#524) — die Liste kommt vollständig vom Server,
  // also passiert das hier. Abfrageparameter auf /agents lohnen erst bei einer
  // Menge, auf die diese Oberfläche nicht ausgelegt ist.
  const [query, setQuery] = useState("");
  const [tagFilter, setTagFilter] = useState<string | null>(null);
  const [groupBy, setGroupBy] = useState<GroupBy>("team");
  const [sortBy, setSortBy] = useState<SortBy>("name");

  const allTags = useMemo(() => {
    const seen = new Set<string>();
    for (const a of agents) {
      const t = getAgentTag(a.config as Record<string, unknown> | null);
      if (t) seen.add(t);
    }
    return [...seen].sort((a, b) => a.localeCompare(b, "de"));
  }, [agents]);

  const visibleAgents = useMemo(() => {
    const q = query.trim().toLowerCase();
    return agents.filter((a) => {
      const tag = getAgentTag(a.config as Record<string, unknown> | null);
      if (tagFilter && tag !== tagFilter) return false;
      if (!q) return true;
      // Name, Rolle und Schlagwort — die drei Dinge, nach denen man einen Agenten
      // im Kopf sucht.
      return (
        a.name.toLowerCase().includes(q) ||
        (a.role || "").toLowerCase().includes(q) ||
        tag.toLowerCase().includes(q)
      );
    });
  }, [agents, query, tagFilter]);

  // Gruppierung: nach Team (Verhalten) ODER nach Schlagwort (Organisation).
  const agentGroups = useMemo(() => {
    const byKey: Record<string, { key: string; name: string; isTeam: boolean; leadName: string | null; agents: typeof agents }> = {};

    if (groupBy === "tag") {
      for (const a of visibleAgents) {
        const tag = getAgentTag(a.config as Record<string, unknown> | null);
        const key = tag || "__none__";
        if (!byKey[key]) {
          byKey[key] = { key, name: tag || "Ohne Schlagwort", isTeam: false, leadName: null, agents: [] };
        }
        byKey[key].agents.push(a);
      }
    } else {
      const teamOf: Record<string, AgentTeam> = {};
      for (const t of teams) for (const m of t.member_agent_ids) if (!teamOf[m]) teamOf[m] = t;
      for (const a of visibleAgents) {
        const t = teamOf[a.id];
        const key = t ? t.id : "__none__";
        if (!byKey[key]) {
          const leadName = t?.lead_agent_id ? (agents.find((x) => x.id === t.lead_agent_id)?.name ?? null) : null;
          byKey[key] = { key, name: t ? t.name : "Ohne Team", isTeam: !!t, leadName, agents: [] };
        }
        byKey[key].agents.push(a);
      }
    }

    const groups = Object.values(byKey);
    for (const g of groups) {
      g.agents = [...g.agents].sort((a, b) => {
        if (sortBy === "state") {
          const rank = (s: string) => (["running", "idle", "working"].includes(s) ? 0 : s === "error" ? 1 : 2);
          const d = rank(a.state) - rank(b.state);
          if (d) return d;
        } else if (sortBy === "tag") {
          const ta = getAgentTag(a.config as Record<string, unknown> | null);
          const tb = getAgentTag(b.config as Record<string, unknown> | null);
          // Ohne Schlagwort ans Ende — sonst stehen die Unsortierten vorn.
          if (!!ta !== !!tb) return ta ? -1 : 1;
          const d = ta.localeCompare(tb, "de");
          if (d) return d;
        }
        return a.name.localeCompare(b.name, "de");
      });
    }
    // Benannte Gruppen zuerst, „ohne …" ans Ende.
    return groups.sort((a, b) => {
      const na = a.key === "__none__" ? 1 : 0;
      const nb = b.key === "__none__" ? 1 : 0;
      if (na !== nb) return na - nb;
      if (groupBy === "team" && a.isTeam !== b.isTeam) return a.isTeam ? -1 : 1;
      return a.name.localeCompare(b.name, "de");
    });
  }, [agents, visibleAgents, teams, groupBy, sortBy]);

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

  const handleRestartAll = async () => {
    const active = agents.filter((a) => ["running", "idle", "working"].includes(a.state));
    if (active.length === 0) return;
    const ok = await confirm({
      title: `${active.length} Agent(s) neu starten?`,
      message:
        "Alle aktiven Agents werden mit frischen Umgebungsvariablen (MCP-Server, Integrationen) neu erstellt. Alle aktuell laufenden Aufgaben werden dabei abgebrochen. Daten (Volumes, Wissen, Konfiguration) bleiben erhalten.",
      variant: "warning",
      confirmLabel: "Alle neu starten",
    });
    if (!ok) return;
    setRestartingAll(true);
    try {
      await Promise.all(active.map((a) => api.restartAgent(a.id)));
      await refresh();
    } finally {
      setRestartingAll(false);
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

            {/* Restart All */}
            {agents.some((a) => ["running", "idle", "working"].includes(a.state)) && (
              <button
                onClick={handleRestartAll}
                disabled={restartingAll}
                className="inline-flex items-center gap-2 rounded-xl bg-amber-500/10 border border-amber-500/20 px-4 py-2.5 text-sm font-medium text-amber-400 hover:bg-amber-500/20 disabled:opacity-50 transition-all duration-200"
              >
                {restartingAll ? <Loader2 className="h-4 w-4 animate-spin" /> : <RotateCw className="h-4 w-4" />}
                Restart All
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
            {/* Erst ab einer Menge, die man nicht mehr überblickt — darunter ist die
                Leiste nur Ballast über einer Handvoll Karten. */}
            {(agents.length > 5 || allTags.length > 0) && (
              <AgentFilterBar
                query={query}
                onQuery={setQuery}
                tags={allTags}
                tagFilter={tagFilter}
                onTagFilter={setTagFilter}
                groupBy={groupBy}
                onGroupBy={setGroupBy}
                sortBy={sortBy}
                onSortBy={setSortBy}
                shown={visibleAgents.length}
                total={agents.length}
              />
            )}

            {visibleAgents.length === 0 && (
              <div className="rounded-xl border border-dashed border-foreground/[0.1] bg-card/30 p-10 text-center text-sm text-muted-foreground">
                Kein Agent passt zu dieser Suche.
                <button
                  onClick={() => { setQuery(""); setTagFilter(null); }}
                  className="ml-2 text-primary hover:underline"
                >
                  Filter zurücksetzen
                </button>
              </div>
            )}

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

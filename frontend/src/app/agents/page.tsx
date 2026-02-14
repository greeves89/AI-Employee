"use client";

import { useState } from "react";
import { motion } from "framer-motion";
import { Plus, Cpu, Play, Square, Trash2, Loader2, Bot } from "lucide-react";
import { useAgents } from "@/hooks/use-agents";
import { Header } from "@/components/layout/header";
import { AgentCard } from "@/components/dashboard/agent-card";
import { cn } from "@/lib/utils";
import * as api from "@/lib/api";

export default function AgentsPage() {
  const { agents, loading, refresh } = useAgents();
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [newRole, setNewRole] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  const handleCreate = async () => {
    if (!newName.trim()) return;
    setCreating(true);
    try {
      await api.createAgent(newName.trim(), undefined, newRole.trim() || undefined);
      setNewName("");
      setNewRole("");
      setShowCreate(false);
      await refresh();
    } catch (e) {
      alert(e instanceof Error ? e.message : "Failed to create agent");
    } finally {
      setCreating(false);
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
          <button
            onClick={() => setShowCreate(true)}
            className="inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground shadow-lg shadow-primary/20 hover:bg-primary/90 transition-all duration-200"
          >
            <Plus className="h-4 w-4" />
            New Agent
          </button>
        }
      />

      <motion.div
        className="px-8 py-8"
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
      >
        {/* Create agent inline form */}
        {showCreate && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            className="mb-6 rounded-xl border border-primary/20 bg-primary/5 backdrop-blur-sm p-5"
          >
            <div className="flex items-end gap-3">
              <div className="flex-1">
                <label className="block text-xs font-medium text-muted-foreground mb-1.5">
                  Agent Name
                </label>
                <input
                  type="text"
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && handleCreate()}
                  placeholder="e.g. worker-1, research-bot..."
                  autoFocus
                  className="w-full rounded-lg border border-foreground/[0.1] bg-background/80 px-4 py-2.5 text-sm outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-all"
                />
              </div>
              <div className="flex-1">
                <label className="block text-xs font-medium text-muted-foreground mb-1.5">
                  Role <span className="text-muted-foreground/40">(optional)</span>
                </label>
                <input
                  type="text"
                  value={newRole}
                  onChange={(e) => setNewRole(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && handleCreate()}
                  placeholder="e.g. Developer, Researcher, Writer..."
                  className="w-full rounded-lg border border-foreground/[0.1] bg-background/80 px-4 py-2.5 text-sm outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-all"
                />
              </div>
              <button
                onClick={handleCreate}
                disabled={creating || !newName.trim()}
                className="inline-flex items-center gap-2 rounded-lg bg-primary px-5 py-2.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-all"
              >
                {creating ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Plus className="h-4 w-4" />
                )}
                {creating ? "Creating..." : "Create"}
              </button>
              <button
                onClick={() => { setShowCreate(false); setNewName(""); setNewRole(""); }}
                className="rounded-lg px-4 py-2.5 text-sm font-medium text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04] transition-all"
              >
                Cancel
              </button>
            </div>
          </motion.div>
        )}

        {loading && agents.length === 0 ? (
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            {[1, 2, 3].map((i) => (
              <div
                key={i}
                className="rounded-xl border border-foreground/[0.06] bg-card/50 p-5 h-48 animate-shimmer bg-[length:200%_100%] bg-gradient-to-r from-foreground/[0.03] via-foreground/[0.06] to-foreground/[0.03]"
              />
            ))}
          </div>
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
                className="relative group"
              >
                <AgentCard agent={agent} />

                {/* Floating action buttons */}
                <div className="absolute top-3 right-3 flex items-center gap-1.5 opacity-0 group-hover:opacity-100 transition-opacity duration-200 z-10">
                  {actionLoading === agent.id ? (
                    <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-foreground/[0.08] backdrop-blur-sm">
                      <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />
                    </div>
                  ) : (
                    <>
                      {agent.state === "stopped" ? (
                        <button
                          onClick={(e) => { e.preventDefault(); e.stopPropagation(); handleStart(agent.id); }}
                          className="flex h-8 w-8 items-center justify-center rounded-lg bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 hover:bg-emerald-500/20 backdrop-blur-sm transition-colors"
                          title="Start"
                        >
                          <Play className="h-3.5 w-3.5" />
                        </button>
                      ) : (
                        <button
                          onClick={(e) => { e.preventDefault(); e.stopPropagation(); handleStop(agent.id); }}
                          className="flex h-8 w-8 items-center justify-center rounded-lg bg-amber-500/10 border border-amber-500/20 text-amber-400 hover:bg-amber-500/20 backdrop-blur-sm transition-colors"
                          title="Stop"
                        >
                          <Square className="h-3.5 w-3.5" />
                        </button>
                      )}
                      <button
                        onClick={(e) => { e.preventDefault(); e.stopPropagation(); handleRemove(agent.id); }}
                        className="flex h-8 w-8 items-center justify-center rounded-lg bg-red-500/10 border border-red-500/20 text-red-400 hover:bg-red-500/20 backdrop-blur-sm transition-colors"
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

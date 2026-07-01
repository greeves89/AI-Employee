"use client";

import { useState, useEffect, useCallback } from "react";
import { motion } from "framer-motion";
import { Plus, Users, Crown, Pencil, Send, Trash2, Loader2 } from "lucide-react";
import * as api from "@/lib/api";
import type { Team } from "@/lib/api";
import type { Agent } from "@/lib/types";
import { cn } from "@/lib/utils";
import { AgentAvatar } from "@/components/agents/agent-avatar";
import { CreateTeamModal } from "@/components/agents/create-team-modal";
import { DelegateToTeamModal } from "@/components/agents/delegate-to-team-modal";
import { useConfirm, useToast } from "@/components/ui/dialog-provider";

export function TeamsSection({ agents }: { agents: Agent[] }) {
  const confirm = useConfirm();
  const toast = useToast();

  const [teams, setTeams] = useState<Team[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [editTeam, setEditTeam] = useState<Team | null>(null);
  const [delegateTeam, setDelegateTeam] = useState<Team | null>(null);
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  const agentById = useCallback(
    (id: string) => agents.find((a) => a.id === id) ?? null,
    [agents],
  );

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.listTeams();
      setTeams(data.teams);
    } catch {
      setTeams([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const handleDelete = async (team: Team) => {
    const ok = await confirm({
      title: `Team "${team.name}" loeschen?`,
      message: "Das Team wird entfernt. Die zugehoerigen Agents bleiben erhalten.",
      variant: "destructive",
      confirmLabel: "Loeschen",
    });
    if (!ok) return;
    setActionLoading(team.id);
    try {
      await api.deleteTeam(team.id);
      toast.success("Team geloescht", team.name);
      await refresh();
    } catch (e) {
      toast.error("Loeschen fehlgeschlagen", e instanceof Error ? e.message : undefined);
    } finally {
      setActionLoading(null);
    }
  };

  return (
    <div>
      {/* Section header */}
      <div className="flex items-center justify-between mb-5">
        <div className="flex items-center gap-2.5">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary/10 text-primary">
            <Users className="h-5 w-5" />
          </div>
          <div>
            <h2 className="text-base font-semibold leading-tight">Teams</h2>
            <p className="text-xs text-muted-foreground">Agents buendeln, Lead festlegen, Tasks delegieren</p>
          </div>
        </div>
        <button
          onClick={() => setShowCreate(true)}
          className="inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground shadow-lg shadow-primary/20 hover:bg-primary/90 transition-all duration-200"
        >
          <Plus className="h-4 w-4" />
          New Team
        </button>
      </div>

      {/* Create / Edit / Delegate modals */}
      <CreateTeamModal
        open={showCreate}
        onOpenChange={setShowCreate}
        onSaved={refresh}
        agents={agents}
      />
      <CreateTeamModal
        open={editTeam !== null}
        onOpenChange={(o) => !o && setEditTeam(null)}
        onSaved={refresh}
        agents={agents}
        team={editTeam}
      />
      <DelegateToTeamModal
        open={delegateTeam !== null}
        onOpenChange={(o) => !o && setDelegateTeam(null)}
        team={delegateTeam}
      />

      {/* Content */}
      {loading && teams.length === 0 ? (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {[1, 2, 3].map((i) => (
            <div
              key={i}
              className="rounded-xl border border-foreground/[0.06] bg-card/50 p-5 h-40 animate-shimmer bg-[length:200%_100%] bg-gradient-to-r from-foreground/[0.03] via-foreground/[0.06] to-foreground/[0.03]"
            />
          ))}
        </div>
      ) : teams.length === 0 ? (
        <div className="rounded-xl border border-dashed border-foreground/[0.1] bg-card/30 p-16 text-center">
          <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-foreground/[0.06] mb-4">
            <Users className="h-7 w-7 text-muted-foreground" />
          </div>
          <h3 className="text-lg font-semibold mb-1.5">Noch keine Teams</h3>
          <p className="text-sm text-muted-foreground mb-5">
            Buendle deine Agents zu einem Team und delegiere Tasks an den Lead.
          </p>
          <button
            onClick={() => setShowCreate(true)}
            className="inline-flex items-center gap-2 rounded-xl bg-primary px-5 py-2.5 text-sm font-medium text-primary-foreground shadow-lg shadow-primary/20 hover:bg-primary/90 transition-all"
          >
            <Plus className="h-4 w-4" />
            Team erstellen
          </button>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {teams.map((team, i) => {
            const lead = team.lead_agent_id ? agentById(team.lead_agent_id) : null;
            return (
              <motion.div
                key={team.id}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: i * 0.06, duration: 0.25 }}
                className="group relative flex flex-col rounded-xl border border-foreground/[0.06] bg-card/50 p-5 transition-colors hover:border-foreground/[0.12]"
              >
                {/* Title row */}
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <h3 className="text-sm font-semibold truncate">{team.name}</h3>
                      <span
                        className={cn(
                          "inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wider",
                          team.is_active
                            ? "bg-emerald-500/10 text-emerald-400"
                            : "bg-foreground/[0.06] text-muted-foreground"
                        )}
                      >
                        <span className={cn("h-1.5 w-1.5 rounded-full", team.is_active ? "bg-emerald-400" : "bg-muted-foreground")} />
                        {team.is_active ? "aktiv" : "inaktiv"}
                      </span>
                    </div>
                    {team.description && (
                      <p className="mt-1 text-xs text-muted-foreground/80 line-clamp-2">{team.description}</p>
                    )}
                  </div>

                  {/* Card actions */}
                  <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity duration-200">
                    {actionLoading === team.id ? (
                      <div className="flex h-7 w-7 items-center justify-center">
                        <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />
                      </div>
                    ) : (
                      <>
                        <button
                          onClick={() => setEditTeam(team)}
                          className="flex h-7 w-7 items-center justify-center rounded-lg text-muted-foreground hover:text-foreground hover:bg-foreground/[0.06] transition-colors"
                          title="Mitglieder & Lead bearbeiten"
                        >
                          <Pencil className="h-3.5 w-3.5" />
                        </button>
                        <button
                          onClick={() => setDelegateTeam(team)}
                          className="flex h-7 w-7 items-center justify-center rounded-lg text-muted-foreground hover:text-primary hover:bg-primary/10 transition-colors"
                          title="Task delegieren"
                        >
                          <Send className="h-3.5 w-3.5" />
                        </button>
                        <button
                          onClick={() => handleDelete(team)}
                          className="flex h-7 w-7 items-center justify-center rounded-lg text-muted-foreground hover:text-red-400 hover:bg-red-500/15 transition-colors"
                          title="Team loeschen"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </>
                    )}
                  </div>
                </div>

                {/* Members */}
                <div className="mt-4 flex items-center gap-3">
                  {team.member_agent_ids.length > 0 ? (
                    <div className="flex -space-x-2">
                      {team.member_agent_ids.slice(0, 6).map((id) => {
                        const agent = agentById(id);
                        return (
                          <div key={id} className="rounded-xl ring-2 ring-card" title={agent?.name ?? id}>
                            <AgentAvatar config={agent?.config} size="sm" />
                          </div>
                        );
                      })}
                      {team.member_agent_ids.length > 6 && (
                        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-foreground/[0.06] text-[10px] font-medium text-muted-foreground ring-2 ring-card">
                          +{team.member_agent_ids.length - 6}
                        </div>
                      )}
                    </div>
                  ) : (
                    <p className="text-xs text-muted-foreground/60">Keine Mitglieder</p>
                  )}
                  <span className="text-xs text-muted-foreground/70">
                    {team.member_agent_ids.length} {team.member_agent_ids.length === 1 ? "Mitglied" : "Mitglieder"}
                  </span>
                </div>

                {/* Lead badge */}
                <div className="mt-4 border-t border-foreground/[0.06] pt-3">
                  {lead ? (
                    <span className="inline-flex items-center gap-1.5 rounded-lg bg-amber-500/10 px-2.5 py-1 text-xs font-medium text-amber-400">
                      <Crown className="h-3.5 w-3.5" />
                      Lead: {lead.name}
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1.5 rounded-lg bg-foreground/[0.04] px-2.5 py-1 text-xs text-muted-foreground/70">
                      <Crown className="h-3.5 w-3.5" />
                      Kein Lead
                    </span>
                  )}
                </div>
              </motion.div>
            );
          })}
        </div>
      )}
    </div>
  );
}

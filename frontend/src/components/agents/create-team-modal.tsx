"use client";

import { useState, useEffect } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { motion, AnimatePresence } from "framer-motion";
import { X, Plus, Loader2, Check, Crown, Users } from "lucide-react";
import * as api from "@/lib/api";
import type { Agent } from "@/lib/types";
import type { Team } from "@/lib/api";
import { cn } from "@/lib/utils";
import { AgentAvatar } from "@/components/agents/agent-avatar";
import { useToast } from "@/components/ui/dialog-provider";

interface CreateTeamModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSaved: () => void;
  agents: Agent[];
  /** When provided, the modal edits this team instead of creating a new one. */
  team?: Team | null;
}

export function CreateTeamModal({
  open,
  onOpenChange,
  onSaved,
  agents,
  team,
}: CreateTeamModalProps) {
  const toast = useToast();
  const isEdit = Boolean(team);

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [memberIds, setMemberIds] = useState<string[]>([]);
  const [leadAgentId, setLeadAgentId] = useState<string>("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Reset / prefill on open
  useEffect(() => {
    if (open) {
      setName(team?.name ?? "");
      setDescription(team?.description ?? "");
      setMemberIds(team?.member_agent_ids ?? []);
      setLeadAgentId(team?.lead_agent_id ?? "");
      setError(null);
    }
  }, [open, team]);

  const toggleMember = (id: string) => {
    setMemberIds((prev) => {
      if (prev.includes(id)) {
        // Removing the current lead clears the lead selection
        if (leadAgentId === id) setLeadAgentId("");
        return prev.filter((m) => m !== id);
      }
      return [...prev, id];
    });
  };

  const handleSave = async () => {
    if (!name.trim()) {
      setError("Name ist erforderlich");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const body = {
        name: name.trim(),
        description: description.trim() || undefined,
        member_agent_ids: memberIds,
        lead_agent_id: leadAgentId || null,
      };
      if (isEdit && team) {
        await api.updateTeam(team.id, body);
        toast.success("Team aktualisiert", name.trim());
      } else {
        await api.createTeam(body);
        toast.success("Team erstellt", name.trim());
      }
      onOpenChange(false);
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Team konnte nicht gespeichert werden");
    } finally {
      setSaving(false);
    }
  };

  const memberAgents = agents.filter((a) => memberIds.includes(a.id));

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <AnimatePresence>
        {open && (
          <Dialog.Portal forceMount>
            <Dialog.Overlay asChild>
              <motion.div
                className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.15 }}
              />
            </Dialog.Overlay>

            <Dialog.Content asChild>
              <motion.div
                className="fixed inset-0 z-50 flex items-center justify-center p-4"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.2, ease: "easeOut" }}
              >
                <motion.div
                  className="w-full max-w-2xl rounded-2xl border border-foreground/[0.08] bg-card shadow-2xl shadow-black/40 overflow-hidden max-h-[90vh] overflow-y-auto"
                  initial={{ scale: 0.95, y: 10 }}
                  animate={{ scale: 1, y: 0 }}
                  exit={{ scale: 0.95, y: 10 }}
                  transition={{ duration: 0.2, ease: "easeOut" }}
                >
                  {/* Header */}
                  <div className="flex items-center justify-between border-b border-foreground/[0.06] px-6 py-4">
                    <Dialog.Title className="text-lg font-semibold">
                      {isEdit ? "Team bearbeiten" : "Team erstellen"}
                    </Dialog.Title>
                    <Dialog.Close className="rounded-lg p-1.5 text-muted-foreground hover:text-foreground hover:bg-foreground/[0.06] transition-colors">
                      <X className="h-4 w-4" />
                    </Dialog.Close>
                  </div>

                  {/* Body */}
                  <div className="px-6 py-5 space-y-5">
                    {/* Name */}
                    <div>
                      <label className="block text-xs font-medium text-muted-foreground mb-1.5">
                        Name <span className="text-red-400">*</span>
                      </label>
                      <input
                        type="text"
                        value={name}
                        onChange={(e) => setName(e.target.value)}
                        onKeyDown={(e) => e.key === "Enter" && !saving && handleSave()}
                        placeholder="z.B. Research-Team, Delivery-Squad..."
                        autoFocus
                        className="w-full rounded-lg border border-foreground/[0.1] bg-background/80 px-4 py-2.5 text-sm outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-all"
                      />
                    </div>

                    {/* Description */}
                    <div>
                      <label className="block text-xs font-medium text-muted-foreground mb-1.5">
                        Beschreibung <span className="text-muted-foreground/40">(optional)</span>
                      </label>
                      <textarea
                        value={description}
                        onChange={(e) => setDescription(e.target.value)}
                        placeholder="Wofuer ist dieses Team zustaendig?"
                        rows={2}
                        className="w-full rounded-lg border border-foreground/[0.1] bg-background/80 px-4 py-2.5 text-sm outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-all resize-none"
                      />
                    </div>

                    {/* Members */}
                    <div>
                      <label className="block text-xs font-medium text-muted-foreground mb-2.5">
                        Mitglieder <span className="text-muted-foreground/40">({memberIds.length})</span>
                      </label>
                      <div className="space-y-2">
                        {agents.map((agent) => {
                          const isSelected = memberIds.includes(agent.id);
                          return (
                            <button
                              key={agent.id}
                              type="button"
                              onClick={() => toggleMember(agent.id)}
                              className={cn(
                                "w-full flex items-center gap-3 rounded-xl border p-3 text-left transition-all duration-200",
                                isSelected
                                  ? "border-primary/40 bg-primary/[0.08]"
                                  : "border-foreground/[0.06] bg-foreground/[0.02] hover:bg-foreground/[0.04]"
                              )}
                            >
                              <AgentAvatar config={agent.config} size="sm" />
                              <div className="flex-1 min-w-0">
                                <p className={cn("text-sm font-medium truncate", isSelected ? "text-foreground" : "text-muted-foreground")}>
                                  {agent.name}
                                </p>
                                {agent.role && (
                                  <p className="text-xs text-muted-foreground/70 truncate">{agent.role}</p>
                                )}
                              </div>
                              <div
                                className={cn(
                                  "flex h-5 w-5 shrink-0 items-center justify-center rounded-md border transition-all",
                                  isSelected ? "border-primary bg-primary text-white" : "border-foreground/20"
                                )}
                              >
                                {isSelected && <Check className="h-3 w-3" />}
                              </div>
                            </button>
                          );
                        })}

                        {agents.length === 0 && (
                          <p className="text-xs text-muted-foreground/50 text-center py-3">
                            Keine Agents vorhanden — lege zuerst einen Agent an.
                          </p>
                        )}
                      </div>
                    </div>

                    {/* Lead */}
                    <div>
                      <label className="block text-xs font-medium text-muted-foreground mb-1.5">
                        <span className="inline-flex items-center gap-1.5">
                          <Crown className="h-3.5 w-3.5 text-amber-400" /> Lead
                        </span>{" "}
                        <span className="text-muted-foreground/40">(optional)</span>
                      </label>
                      <select
                        value={leadAgentId}
                        onChange={(e) => setLeadAgentId(e.target.value)}
                        disabled={memberAgents.length === 0}
                        className="w-full rounded-lg border border-foreground/[0.1] bg-background/80 px-4 py-2.5 text-sm outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-all disabled:opacity-50"
                      >
                        <option value="">Kein Lead</option>
                        {memberAgents.map((agent) => (
                          <option key={agent.id} value={agent.id}>
                            {agent.name}
                          </option>
                        ))}
                      </select>
                      <p className="text-[11px] text-muted-foreground/50 mt-1">
                        Der Lead muss Mitglied des Teams sein und erhaelt delegierte Tasks.
                      </p>
                    </div>

                    {/* Error */}
                    {error && (
                      <div className="rounded-lg border border-red-500/20 bg-red-500/10 px-4 py-2.5 text-sm text-red-400">
                        {error}
                      </div>
                    )}
                  </div>

                  {/* Footer */}
                  <div className="flex items-center justify-end gap-3 border-t border-foreground/[0.06] px-6 py-4 bg-foreground/[0.02]">
                    <Dialog.Close className="rounded-lg px-4 py-2.5 text-sm font-medium text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04] transition-all">
                      Abbrechen
                    </Dialog.Close>
                    <button
                      onClick={handleSave}
                      disabled={saving || !name.trim()}
                      className="inline-flex items-center gap-2 rounded-lg bg-primary px-5 py-2.5 text-sm font-medium text-primary-foreground disabled:opacity-50 transition-all shadow-lg shadow-primary/20 hover:bg-primary/90"
                    >
                      {saving ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : isEdit ? (
                        <Users className="h-4 w-4" />
                      ) : (
                        <Plus className="h-4 w-4" />
                      )}
                      {saving ? "Speichere..." : isEdit ? "Speichern" : "Team erstellen"}
                    </button>
                  </div>
                </motion.div>
              </motion.div>
            </Dialog.Content>
          </Dialog.Portal>
        )}
      </AnimatePresence>
    </Dialog.Root>
  );
}

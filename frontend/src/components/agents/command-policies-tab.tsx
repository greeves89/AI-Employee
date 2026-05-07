"use client";

import { useEffect, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  ShieldAlert, ShieldCheck, AlertCircle, AlertTriangle,
  Plus, Trash2, Loader2, Shield, Lock,
} from "lucide-react";
import { cn } from "@/lib/utils";
import {
  getCommandPoliciesForAgent,
  getCommandPolicies,
  createCommandPolicy,
  updateCommandPolicy,
  deleteCommandPolicy,
} from "@/lib/api";
import type { CommandPolicy } from "@/lib/api";

const effectConfig: Record<string, { icon: typeof ShieldAlert; color: string; bg: string; label: string }> = {
  blocked: { icon: ShieldAlert, color: "text-red-400", bg: "bg-red-500/10", label: "Blocked" },
  high: { icon: AlertCircle, color: "text-orange-400", bg: "bg-orange-500/10", label: "High" },
  medium: { icon: AlertTriangle, color: "text-amber-400", bg: "bg-amber-500/10", label: "Medium" },
  allow: { icon: ShieldCheck, color: "text-emerald-400", bg: "bg-emerald-500/10", label: "Allow" },
};

interface Props {
  agentId: string;
}

export function CommandPoliciesTab({ agentId }: Props) {
  const [globalPolicies, setGlobalPolicies] = useState<CommandPolicy[]>([]);
  const [agentPolicies, setAgentPolicies] = useState<CommandPolicy[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [draft, setDraft] = useState({ name: "", pattern: "", effect: "allow", description: "" });

  const load = async () => {
    setLoading(true);
    try {
      const allData = await getCommandPolicies();
      const all = allData.policies;
      setGlobalPolicies(all.filter((p) => p.scope === "global"));
      setAgentPolicies(all.filter((p) => p.scope === "agent" && p.agent_id === agentId));
    } catch (error) {
      console.error("Failed to load policies:", error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, [agentId]);

  const handleSave = async () => {
    if (!draft.name.trim() || !draft.pattern.trim()) return;
    try {
      await createCommandPolicy({
        name: draft.name.trim(),
        pattern: draft.pattern.trim(),
        effect: draft.effect,
        scope: "agent",
        agent_id: agentId,
        description: draft.description.trim(),
      });
      setDraft({ name: "", pattern: "", effect: "allow", description: "" });
      setShowForm(false);
      await load();
    } catch (error) {
      console.error("Failed to save policy:", error);
    }
  };

  const handleToggle = async (policy: CommandPolicy) => {
    try {
      await updateCommandPolicy(policy.id, { is_active: !policy.is_active });
      await load();
    } catch (error) {
      console.error("Failed to toggle policy:", error);
    }
  };

  const handleDelete = async (id: number) => {
    try {
      await deleteCommandPolicy(id);
      await load();
    } catch (error) {
      console.error("Failed to delete policy:", error);
    }
  };

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center py-20 text-muted-foreground/50">
        <Loader2 className="h-6 w-6 animate-spin mb-3" />
        <span className="text-sm">Loading policies...</span>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Global Rules (inherited, read-only) */}
      <div>
        <div className="flex items-center gap-2 mb-3">
          <Lock className="h-4 w-4 text-muted-foreground/40" />
          <h3 className="text-sm font-medium text-muted-foreground/60">Global Rules — inherited</h3>
          <span className="text-[10px] text-muted-foreground/30 bg-foreground/[0.03] px-1.5 py-0.5 rounded">
            {globalPolicies.length} rules
          </span>
        </div>
        {globalPolicies.length === 0 ? (
          <p className="text-[11px] text-muted-foreground/30 pl-6">No global policies configured</p>
        ) : (
          <div className="space-y-1.5">
            {globalPolicies.map((policy) => {
              const ec = effectConfig[policy.effect] || effectConfig.blocked;
              const EffectIcon = ec.icon;
              return (
                <div
                  key={policy.id}
                  className={cn(
                    "rounded-lg border border-foreground/[0.04] bg-card/30 px-3 py-2 flex items-center gap-3",
                    !policy.is_active && "opacity-40"
                  )}
                >
                  <div className={cn("flex h-6 w-6 shrink-0 items-center justify-center rounded-md", ec.bg)}>
                    <EffectIcon className={cn("h-3 w-3", ec.color)} />
                  </div>
                  <span className="text-xs font-medium truncate flex-1">{policy.name}</span>
                  <code className="text-[10px] text-muted-foreground/40 font-mono truncate max-w-[200px]">
                    {policy.pattern}
                  </code>
                  <span className={cn("text-[10px] px-1.5 py-0.5 rounded", ec.bg, ec.color)}>
                    {ec.label}
                  </span>
                  <span className="text-[10px] text-muted-foreground/30 italic">inherited</span>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Agent-specific overrides */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <Shield className="h-4 w-4 text-primary/60" />
            <h3 className="text-sm font-medium">Agent Overrides</h3>
            <span className="text-[10px] text-muted-foreground/30 bg-foreground/[0.03] px-1.5 py-0.5 rounded">
              {agentPolicies.length} rules
            </span>
          </div>
          <button
            onClick={() => setShowForm(!showForm)}
            className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-2.5 py-1.5 text-xs font-medium text-primary-foreground hover:bg-primary/90 transition-all"
          >
            <Plus className="h-3 w-3" />
            Add override
          </button>
        </div>

        {showForm && (
          <div className="rounded-xl border border-foreground/[0.08] bg-card/50 p-3 mb-3 space-y-2">
            <div className="grid grid-cols-2 gap-2">
              <input
                value={draft.name}
                onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                placeholder="Name"
                className="rounded-lg border border-foreground/[0.08] bg-background px-3 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-primary/40"
              />
              <input
                value={draft.pattern}
                onChange={(e) => setDraft({ ...draft, pattern: e.target.value })}
                placeholder="Regex pattern"
                className="rounded-lg border border-foreground/[0.08] bg-background px-3 py-1.5 text-xs font-mono focus:outline-none focus:ring-1 focus:ring-primary/40"
              />
            </div>
            <div className="grid grid-cols-2 gap-2">
              <select
                value={draft.effect}
                onChange={(e) => setDraft({ ...draft, effect: e.target.value })}
                className="rounded-lg border border-foreground/[0.08] bg-background px-3 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-primary/40"
              >
                <option value="allow">Allow — explicitly permit</option>
                <option value="medium">Medium — approval recommended</option>
                <option value="high">High — requires approval</option>
                <option value="blocked">Blocked — always deny</option>
              </select>
              <input
                value={draft.description}
                onChange={(e) => setDraft({ ...draft, description: e.target.value })}
                placeholder="Description"
                className="rounded-lg border border-foreground/[0.08] bg-background px-3 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-primary/40"
              />
            </div>
            <div className="flex gap-2 justify-end">
              <button
                onClick={() => { setShowForm(false); setDraft({ name: "", pattern: "", effect: "allow", description: "" }); }}
                className="rounded-lg px-2.5 py-1 text-[11px] text-muted-foreground hover:text-foreground transition-all"
              >
                Cancel
              </button>
              <button
                onClick={handleSave}
                disabled={!draft.name.trim() || !draft.pattern.trim()}
                className="rounded-lg bg-primary px-2.5 py-1 text-[11px] font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-all"
              >
                Save
              </button>
            </div>
          </div>
        )}

        {agentPolicies.length === 0 && !showForm ? (
          <div className="rounded-lg border border-dashed border-foreground/[0.06] bg-card/20 p-8 text-center">
            <Shield className="h-8 w-8 mx-auto mb-2 text-muted-foreground/20" />
            <p className="text-[11px] text-muted-foreground/40">
              No agent-specific overrides. This agent uses only global policies.
            </p>
          </div>
        ) : (
          <div className="space-y-1.5">
            {agentPolicies.map((policy) => {
              const ec = effectConfig[policy.effect] || effectConfig.blocked;
              const EffectIcon = ec.icon;
              return (
                <div
                  key={policy.id}
                  className={cn(
                    "rounded-lg border border-foreground/[0.08] bg-card/50 px-3 py-2 flex items-center gap-3",
                    !policy.is_active && "opacity-40"
                  )}
                >
                  <div className={cn("flex h-6 w-6 shrink-0 items-center justify-center rounded-md", ec.bg)}>
                    <EffectIcon className={cn("h-3 w-3", ec.color)} />
                  </div>
                  <div className="min-w-0 flex-1">
                    <span className="text-xs font-medium truncate block">{policy.name}</span>
                    <code className="text-[10px] text-muted-foreground/40 font-mono truncate block">
                      {policy.pattern}
                    </code>
                  </div>
                  <span className={cn("text-[10px] px-1.5 py-0.5 rounded shrink-0", ec.bg, ec.color)}>
                    {ec.label}
                  </span>
                  <button
                    onClick={() => handleToggle(policy)}
                    className={cn(
                      "relative inline-flex h-4 w-8 items-center rounded-full transition-colors shrink-0",
                      policy.is_active ? "bg-primary" : "bg-foreground/10"
                    )}
                  >
                    <span
                      className={cn(
                        "inline-block h-3 w-3 transform rounded-full bg-white transition-transform",
                        policy.is_active ? "translate-x-4" : "translate-x-0.5"
                      )}
                    />
                  </button>
                  <button
                    onClick={() => handleDelete(policy.id)}
                    className="rounded-md p-1 text-muted-foreground/40 hover:text-red-400 hover:bg-red-500/10 transition-all shrink-0"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

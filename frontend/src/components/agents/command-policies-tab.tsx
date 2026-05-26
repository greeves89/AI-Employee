"use client";

import { useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  AlertTriangle,
  Loader2,
  Lock,
  Plus,
  Shield,
  ShieldAlert,
  ShieldCheck,
  Trash2,
  X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import {
  createCommandPolicy,
  deleteCommandPolicy,
  getCommandPolicies,
  updateCommandPolicy,
} from "@/lib/api";
import type { CommandPolicy, CommandPolicyEffect, CommandPolicyScope } from "@/lib/api";

const effectConfig: Record<CommandPolicyEffect, { icon: typeof ShieldAlert; color: string; bg: string; label: string }> = {
  blocked: { icon: ShieldAlert, color: "text-red-400", bg: "bg-red-500/10", label: "Block" },
  high: { icon: AlertCircle, color: "text-orange-400", bg: "bg-orange-500/10", label: "High Approval" },
  medium: { icon: AlertTriangle, color: "text-amber-400", bg: "bg-amber-500/10", label: "Medium Approval" },
  allow: { icon: ShieldCheck, color: "text-emerald-400", bg: "bg-emerald-500/10", label: "Allow" },
};

type Draft = {
  name: string;
  pattern: string;
  effect: CommandPolicyEffect;
  description: string;
  sort_order: string;
};

interface Props {
  agentId?: string;
}

const emptyDraft: Draft = {
  name: "",
  pattern: "",
  effect: "allow",
  description: "",
  sort_order: "100",
};

function PolicyRow({
  policy,
  readOnly,
  onToggle,
  onDelete,
}: {
  policy: CommandPolicy;
  readOnly?: boolean;
  onToggle: (policy: CommandPolicy) => void;
  onDelete: (id: number) => void;
}) {
  const config = effectConfig[policy.effect] ?? effectConfig.blocked;
  const Icon = config.icon;

  return (
    <div className={cn(
      "rounded-xl border border-foreground/[0.08] bg-card/50 px-3 py-2.5 flex items-center gap-3",
      !policy.is_active && "opacity-45",
    )}>
      <div className={cn("flex h-8 w-8 shrink-0 items-center justify-center rounded-lg", config.bg)}>
        <Icon className={cn("h-4 w-4", config.color)} />
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-medium truncate">{policy.name}</span>
          <span className={cn("rounded-md px-1.5 py-0.5 text-[10px] font-medium", config.bg, config.color)}>
            {config.label}
          </span>
          {readOnly && (
            <span className="rounded-md bg-foreground/[0.04] px-1.5 py-0.5 text-[10px] text-muted-foreground">
              inherited
            </span>
          )}
        </div>
        <code className="mt-1 block truncate text-[11px] text-muted-foreground/60">{policy.pattern}</code>
        {policy.description && (
          <p className="mt-1 truncate text-[11px] text-muted-foreground/50">{policy.description}</p>
        )}
      </div>
      <span className="shrink-0 text-[10px] text-muted-foreground/40">#{policy.sort_order}</span>
      {!readOnly && (
        <>
          <button
            onClick={() => onToggle(policy)}
            className={cn(
              "relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors",
              policy.is_active ? "bg-primary" : "bg-foreground/15",
            )}
            title={policy.is_active ? "Disable policy" : "Enable policy"}
          >
            <span className={cn(
              "inline-block h-4 w-4 transform rounded-full bg-white transition-transform",
              policy.is_active ? "translate-x-4" : "translate-x-0.5",
            )} />
          </button>
          <button
            onClick={() => onDelete(policy.id)}
            className="rounded-lg p-2 text-muted-foreground/50 transition-all hover:bg-red-500/10 hover:text-red-400"
            title="Delete policy"
          >
            <Trash2 className="h-4 w-4" />
          </button>
        </>
      )}
    </div>
  );
}

export function CommandPoliciesTab({ agentId }: Props) {
  const [policies, setPolicies] = useState<CommandPolicy[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [saving, setSaving] = useState(false);
  const [draft, setDraft] = useState<Draft>(emptyDraft);

  const mode: CommandPolicyScope = agentId ? "agent" : "global";

  const load = async () => {
    setLoading(true);
    try {
      const data = await getCommandPolicies();
      setPolicies(data.policies);
    } catch (error) {
      console.error("Failed to load command policies:", error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, [agentId]);

  const globalPolicies = useMemo(
    () => policies.filter((policy) => policy.scope === "global"),
    [policies],
  );
  const ownPolicies = useMemo(
    () => policies.filter((policy) => policy.scope === mode && (mode === "global" || policy.agent_id === agentId)),
    [agentId, mode, policies],
  );

  const save = async () => {
    if (!draft.name.trim() || !draft.pattern.trim()) return;
    setSaving(true);
    try {
      await createCommandPolicy({
        name: draft.name.trim(),
        pattern: draft.pattern.trim(),
        effect: draft.effect,
        scope: mode,
        agent_id: mode === "agent" ? agentId : null,
        description: draft.description.trim(),
        sort_order: Number.parseInt(draft.sort_order, 10) || 100,
      });
      setDraft(emptyDraft);
      setShowForm(false);
      await load();
    } finally {
      setSaving(false);
    }
  };

  const toggle = async (policy: CommandPolicy) => {
    await updateCommandPolicy(policy.id, { is_active: !policy.is_active });
    await load();
  };

  const remove = async (id: number) => {
    await deleteCommandPolicy(id);
    await load();
  };

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center py-20 text-muted-foreground/50">
        <Loader2 className="mb-3 h-6 w-6 animate-spin" />
        <span className="text-sm">Command Policies laden...</span>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-lg font-semibold">Command Policies</h2>
          <p className="mt-1 text-sm text-muted-foreground/60">
            Regex-Regeln fuer Bash-Befehle: blockieren, erlauben oder automatisch Approval anfordern.
          </p>
        </div>
        <button
          onClick={() => setShowForm(true)}
          className="inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-lg shadow-primary/20 transition-all hover:bg-primary/90"
        >
          <Plus className="h-4 w-4" />
          {mode === "agent" ? "Override" : "Policy"}
        </button>
      </div>

      {showForm && (
        <div className="rounded-xl border border-foreground/[0.08] bg-card/80 p-5">
          <div className="mb-4 flex items-center justify-between">
            <h3 className="text-sm font-semibold">
              {mode === "agent" ? "Agent Override anlegen" : "Globale Policy anlegen"}
            </h3>
            <button
              onClick={() => setShowForm(false)}
              className="rounded-lg p-1 text-muted-foreground hover:bg-foreground/[0.04] hover:text-foreground"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            <input
              value={draft.name}
              onChange={(event) => setDraft({ ...draft, name: event.target.value })}
              placeholder="Name, z.B. git force push"
              className="rounded-xl border border-foreground/[0.08] bg-background px-3 py-2 text-sm outline-none focus:ring-1 focus:ring-primary/40"
            />
            <input
              value={draft.pattern}
              onChange={(event) => setDraft({ ...draft, pattern: event.target.value })}
              placeholder="Regex, z.B. git\\s+push\\s+--force"
              className="rounded-xl border border-foreground/[0.08] bg-background px-3 py-2 font-mono text-sm outline-none focus:ring-1 focus:ring-primary/40"
            />
            <select
              value={draft.effect}
              onChange={(event) => setDraft({ ...draft, effect: event.target.value as CommandPolicyEffect })}
              className="rounded-xl border border-foreground/[0.08] bg-background px-3 py-2 text-sm outline-none focus:ring-1 focus:ring-primary/40"
            >
              <option value="allow">Allow - explizit erlauben</option>
              <option value="medium">Medium - Approval anfordern</option>
              <option value="high">High - Approval anfordern</option>
              <option value="blocked">Blocked - nie ausfuehren</option>
            </select>
            <input
              value={draft.sort_order}
              onChange={(event) => setDraft({ ...draft, sort_order: event.target.value })}
              placeholder="Sort order"
              className="rounded-xl border border-foreground/[0.08] bg-background px-3 py-2 text-sm outline-none focus:ring-1 focus:ring-primary/40"
            />
            <input
              value={draft.description}
              onChange={(event) => setDraft({ ...draft, description: event.target.value })}
              placeholder="Beschreibung"
              className="md:col-span-2 rounded-xl border border-foreground/[0.08] bg-background px-3 py-2 text-sm outline-none focus:ring-1 focus:ring-primary/40"
            />
          </div>
          <div className="mt-4 flex justify-end gap-2">
            <button
              onClick={() => setShowForm(false)}
              className="rounded-xl px-3 py-2 text-sm text-muted-foreground transition-all hover:bg-foreground/[0.04] hover:text-foreground"
            >
              Abbrechen
            </button>
            <button
              onClick={save}
              disabled={saving || !draft.name.trim() || !draft.pattern.trim()}
              className="inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-all hover:bg-primary/90 disabled:opacity-50"
            >
              {saving && <Loader2 className="h-4 w-4 animate-spin" />}
              Speichern
            </button>
          </div>
        </div>
      )}

      {mode === "agent" && (
        <section>
          <div className="mb-3 flex items-center gap-2 text-sm font-medium text-muted-foreground/70">
            <Lock className="h-4 w-4" />
            Globale Policies geerbt
            <span className="rounded-md bg-foreground/[0.04] px-1.5 py-0.5 text-[10px]">{globalPolicies.length}</span>
          </div>
          <div className="space-y-2">
            {globalPolicies.map((policy) => (
              <PolicyRow key={policy.id} policy={policy} readOnly onToggle={toggle} onDelete={remove} />
            ))}
          </div>
        </section>
      )}

      <section>
        <div className="mb-3 flex items-center gap-2 text-sm font-medium">
          <Shield className="h-4 w-4 text-primary/70" />
          {mode === "agent" ? "Agent Overrides" : "Globale Policies"}
          <span className="rounded-md bg-foreground/[0.04] px-1.5 py-0.5 text-[10px] text-muted-foreground">{ownPolicies.length}</span>
        </div>
        {ownPolicies.length === 0 ? (
          <div className="rounded-xl border border-dashed border-foreground/[0.08] bg-card/30 p-8 text-center text-sm text-muted-foreground/50">
            Keine Command Policies konfiguriert.
          </div>
        ) : (
          <div className="space-y-2">
            {ownPolicies.map((policy) => (
              <PolicyRow key={policy.id} policy={policy} onToggle={toggle} onDelete={remove} />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

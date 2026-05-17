"use client";

import { useState, useEffect, useCallback } from "react";
import {
  KeyRound, Plus, Trash2, Eye, EyeOff, Pencil, Check, X,
  Shield, Server, User, Loader2, Copy,
} from "lucide-react";
import { Header } from "@/components/layout/header";
import { cn } from "@/lib/utils";
import * as api from "@/lib/api";
import type { AgentSecretEntry } from "@/lib/api";

const TYPE_LABELS: Record<string, { label: string; Icon: typeof KeyRound }> = {
  api_key: { label: "API Key", Icon: KeyRound },
  sso_profile: { label: "SSO Profile", Icon: User },
  oauth_token: { label: "OAuth Token", Icon: Shield },
};

const TYPE_COLORS: Record<string, string> = {
  api_key: "bg-violet-500/10 text-violet-400 border-violet-500/20",
  sso_profile: "bg-blue-500/10 text-blue-400 border-blue-500/20",
  oauth_token: "bg-amber-500/10 text-amber-400 border-amber-500/20",
};

function Toast({ type, message, onClose }: { type: "success" | "error"; message: string; onClose: () => void }) {
  useEffect(() => { const t = setTimeout(onClose, 3500); return () => clearTimeout(t); }, [onClose]);
  return (
    <div className={cn(
      "fixed bottom-6 right-6 z-50 flex items-center gap-3 rounded-xl px-4 py-3 text-sm font-medium shadow-xl",
      type === "success" ? "bg-emerald-500/20 text-emerald-300 border border-emerald-500/30" : "bg-red-500/20 text-red-300 border border-red-500/30"
    )}>
      {type === "success" ? <Check size={16} /> : <X size={16} />}
      {message}
    </div>
  );
}

export function SecretsView({ embedded = false }: { embedded?: boolean }) {
  const [secrets, setSecrets] = useState<AgentSecretEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [toast, setToast] = useState<{ type: "success" | "error"; message: string } | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [revealedIds, setRevealedIds] = useState<Set<number>>(new Set());
  const [deleting, setDeleting] = useState<number | null>(null);

  const [form, setForm] = useState({
    name: "",
    key_name: "",
    value: "",
    secret_type: "api_key" as "api_key" | "sso_profile" | "oauth_token",
    description: "",
  });
  const [editForm, setEditForm] = useState<{ name: string; description: string; value: string; is_active: boolean }>({
    name: "", description: "", value: "", is_active: true,
  });
  const [saving, setSaving] = useState(false);

  const showToast = (type: "success" | "error", message: string) => setToast({ type, message });

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setSecrets(await api.listSecrets());
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  async function handleCreate() {
    if (!form.name || !form.key_name || !form.value) {
      showToast("error", "Name, Env-Var Name and Value are required");
      return;
    }
    setSaving(true);
    try {
      await api.createSecret(form);
      showToast("success", "Secret created");
      setShowCreate(false);
      setForm({ name: "", key_name: "", value: "", secret_type: "api_key", description: "" });
      await load();
    } catch {
      showToast("error", "Failed to create secret");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(id: number) {
    setDeleting(id);
    try {
      await api.deleteSecret(id);
      showToast("success", "Secret deleted");
      await load();
    } catch {
      showToast("error", "Failed to delete secret");
    } finally {
      setDeleting(null);
    }
  }

  function startEdit(s: AgentSecretEntry) {
    setEditingId(s.id);
    setEditForm({ name: s.name, description: s.description, value: "", is_active: s.is_active });
  }

  async function handleUpdate(id: number) {
    setSaving(true);
    try {
      const payload: Parameters<typeof api.updateSecret>[1] = {
        name: editForm.name,
        description: editForm.description,
        is_active: editForm.is_active,
      };
      if (editForm.value) payload.value = editForm.value;
      await api.updateSecret(id, payload);
      showToast("success", "Secret updated");
      setEditingId(null);
      await load();
    } catch {
      showToast("error", "Failed to update secret");
    } finally {
      setSaving(false);
    }
  }

  function toggleReveal(id: number) {
    setRevealedIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  async function copyMasked(s: AgentSecretEntry) {
    await navigator.clipboard.writeText(s.key_name);
    showToast("success", `Copied env-var name: ${s.key_name}`);
  }

  return (
    <div className={embedded ? "" : "flex flex-col h-screen bg-background"}>
      {!embedded && <Header title="Key Management" subtitle="Encrypted API keys, SSO profiles and OAuth tokens" />}
      <div className={embedded ? "max-w-4xl mx-auto w-full" : "flex-1 overflow-auto p-6 max-w-4xl mx-auto w-full"}>
        {/* Page header */}
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-xl font-semibold flex items-center gap-2">
              <KeyRound size={20} className="text-primary" />
              Key Management
            </h1>
            <p className="text-sm text-muted-foreground mt-0.5">
              Encrypted API keys, SSO profiles and OAuth tokens — injected as env vars when agents start.
            </p>
          </div>
          <button
            onClick={() => setShowCreate(v => !v)}
            className="flex items-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground shadow-lg shadow-primary/20"
          >
            <Plus size={16} />
            New Secret
          </button>
        </div>

        {/* Create form */}
        {showCreate && (
          <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-5 mb-4">
            <h2 className="text-sm font-semibold mb-4">New Secret</h2>
            <div className="grid grid-cols-2 gap-3">
              <div className="flex flex-col gap-1">
                <label className="text-[11px] font-medium text-muted-foreground/70">Name</label>
                <input
                  placeholder="Azure AI Search Key"
                  value={form.name}
                  onChange={e => setForm(p => ({ ...p, name: e.target.value }))}
                  className="rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3.5 py-2.5 text-sm"
                />
              </div>
              <div className="flex flex-col gap-1">
                <label className="text-[11px] font-medium text-muted-foreground/70">Env-Var Name</label>
                <input
                  placeholder="AZURE_AI_SEARCH_KEY"
                  value={form.key_name}
                  onChange={e => setForm(p => ({ ...p, key_name: e.target.value.toUpperCase().replace(/\s/g, "_") }))}
                  className="rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3.5 py-2.5 text-sm font-mono"
                />
              </div>
              <div className="flex flex-col gap-1">
                <label className="text-[11px] font-medium text-muted-foreground/70">Value</label>
                <input
                  type="password"
                  placeholder="sk-..."
                  value={form.value}
                  onChange={e => setForm(p => ({ ...p, value: e.target.value }))}
                  className="rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3.5 py-2.5 text-sm font-mono"
                />
              </div>
              <div className="flex flex-col gap-1">
                <label className="text-[11px] font-medium text-muted-foreground/70">Type</label>
                <select
                  value={form.secret_type}
                  onChange={e => setForm(p => ({ ...p, secret_type: e.target.value as typeof p.secret_type }))}
                  className="rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3.5 py-2.5 text-sm"
                >
                  <option value="api_key">API Key</option>
                  <option value="sso_profile">SSO Profile</option>
                  <option value="oauth_token">OAuth Token</option>
                </select>
              </div>
              <div className="col-span-2 flex flex-col gap-1">
                <label className="text-[11px] font-medium text-muted-foreground/70">Description (optional)</label>
                <input
                  placeholder="Used for Azure AI Search queries"
                  value={form.description}
                  onChange={e => setForm(p => ({ ...p, description: e.target.value }))}
                  className="rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3.5 py-2.5 text-sm"
                />
              </div>
            </div>
            <div className="flex justify-end gap-2 mt-4">
              <button onClick={() => setShowCreate(false)} className="rounded-xl px-4 py-2 text-sm text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04]">
                Cancel
              </button>
              <button onClick={handleCreate} disabled={saving} className="flex items-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground shadow-lg shadow-primary/20 disabled:opacity-50">
                {saving ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
                Create
              </button>
            </div>
          </div>
        )}

        {/* Secret list */}
        {loading ? (
          <div className="flex items-center justify-center py-16 text-muted-foreground">
            <Loader2 className="animate-spin mr-2" size={20} />
            Loading secrets…
          </div>
        ) : secrets.length === 0 ? (
          <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-12 text-center">
            <KeyRound size={32} className="mx-auto mb-3 text-muted-foreground/40" />
            <p className="text-sm text-muted-foreground">No secrets yet. Add an API key or SSO profile above.</p>
          </div>
        ) : (
          <div className="flex flex-col gap-3">
            {secrets.map(s => {
              const { label, Icon } = TYPE_LABELS[s.secret_type] ?? { label: s.secret_type, Icon: KeyRound };
              const isEditing = editingId === s.id;
              const revealed = revealedIds.has(s.id);

              return (
                <div key={s.id} className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-5">
                  {isEditing ? (
                    <div className="flex flex-col gap-3">
                      <div className="grid grid-cols-2 gap-3">
                        <div className="flex flex-col gap-1">
                          <label className="text-[11px] font-medium text-muted-foreground/70">Name</label>
                          <input
                            value={editForm.name}
                            onChange={e => setEditForm(p => ({ ...p, name: e.target.value }))}
                            className="rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3.5 py-2.5 text-sm"
                          />
                        </div>
                        <div className="flex flex-col gap-1">
                          <label className="text-[11px] font-medium text-muted-foreground/70">New Value (leave blank to keep)</label>
                          <input
                            type="password"
                            placeholder="New value…"
                            value={editForm.value}
                            onChange={e => setEditForm(p => ({ ...p, value: e.target.value }))}
                            className="rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3.5 py-2.5 text-sm font-mono"
                          />
                        </div>
                        <div className="col-span-2 flex flex-col gap-1">
                          <label className="text-[11px] font-medium text-muted-foreground/70">Description</label>
                          <input
                            value={editForm.description}
                            onChange={e => setEditForm(p => ({ ...p, description: e.target.value }))}
                            className="rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3.5 py-2.5 text-sm"
                          />
                        </div>
                      </div>
                      <div className="flex items-center justify-between">
                        <label className="flex items-center gap-2 text-sm cursor-pointer">
                          <input
                            type="checkbox"
                            checked={editForm.is_active}
                            onChange={e => setEditForm(p => ({ ...p, is_active: e.target.checked }))}
                          />
                          Active
                        </label>
                        <div className="flex gap-2">
                          <button onClick={() => setEditingId(null)} className="rounded-xl px-3 py-1.5 text-sm text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04]">
                            Cancel
                          </button>
                          <button onClick={() => handleUpdate(s.id)} disabled={saving} className="flex items-center gap-2 rounded-xl bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground shadow-lg shadow-primary/20 disabled:opacity-50">
                            {saving ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />}
                            Save
                          </button>
                        </div>
                      </div>
                    </div>
                  ) : (
                    <div className="flex items-start justify-between gap-4">
                      <div className="flex items-start gap-3 min-w-0">
                        <div className="mt-0.5 rounded-lg p-2 bg-foreground/[0.04]">
                          <Icon size={16} className="text-muted-foreground" />
                        </div>
                        <div className="min-w-0">
                          <div className="flex items-center gap-2 flex-wrap">
                            <span className="font-medium text-sm">{s.name}</span>
                            <span className={cn("inline-flex items-center rounded-full border px-2.5 py-1 text-[11px] font-medium", TYPE_COLORS[s.secret_type])}>
                              {label}
                            </span>
                            {!s.is_active && (
                              <span className="inline-flex items-center rounded-full border px-2.5 py-1 text-[11px] font-medium bg-foreground/[0.04] text-muted-foreground border-foreground/[0.08]">
                                Inactive
                              </span>
                            )}
                          </div>
                          <button
                            onClick={() => copyMasked(s)}
                            className="mt-1 flex items-center gap-1 text-xs font-mono text-muted-foreground hover:text-foreground transition-colors"
                          >
                            <Copy size={11} />
                            {s.key_name}
                          </button>
                          {s.description && (
                            <p className="text-xs text-muted-foreground/60 mt-0.5">{s.description}</p>
                          )}
                          <div className="flex items-center gap-1.5 mt-2">
                            <span className="text-xs text-muted-foreground/50 font-mono">
                              {revealed ? s.masked_value : "••••••••"}
                            </span>
                            <button onClick={() => toggleReveal(s.id)} className="text-muted-foreground/40 hover:text-muted-foreground transition-colors">
                              {revealed ? <EyeOff size={12} /> : <Eye size={12} />}
                            </button>
                          </div>
                          {s.assigned_agent_ids.length > 0 && (
                            <p className="text-[11px] text-muted-foreground/40 mt-1">
                              Assigned to {s.assigned_agent_ids.length} agent{s.assigned_agent_ids.length !== 1 ? "s" : ""}
                            </p>
                          )}
                        </div>
                      </div>
                      <div className="flex items-center gap-1 flex-shrink-0">
                        <button
                          onClick={() => startEdit(s)}
                          className="rounded-lg p-2 text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04] transition-colors"
                        >
                          <Pencil size={14} />
                        </button>
                        <button
                          onClick={() => handleDelete(s.id)}
                          disabled={deleting === s.id}
                          className="rounded-lg p-2 text-muted-foreground hover:text-red-400 hover:bg-red-500/[0.06] transition-colors disabled:opacity-40"
                        >
                          {deleting === s.id ? <Loader2 size={14} className="animate-spin" /> : <Trash2 size={14} />}
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
      {toast && <Toast type={toast.type} message={toast.message} onClose={() => setToast(null)} />}
    </div>
  );
}

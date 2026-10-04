"use client";

import { useState, useEffect, useCallback } from "react";
import {
  KeyRound, Plus, Trash2, Eye, EyeOff, Pencil, Check, X,
  Shield, Server, User, Loader2, Copy, Users,
} from "lucide-react";
import { Header } from "@/components/layout/header";
import { cn } from "@/lib/utils";
import * as api from "@/lib/api";
import type { AgentSecretEntry } from "@/lib/api";
import { KeyFreigabeDialog } from "@/components/secrets/key-freigabe-dialog";

const TYPE_LABELS: Record<string, { label: string; Icon: typeof KeyRound }> = {
  api_key: { label: "API-Schlüssel", Icon: KeyRound },
  sso_profile: { label: "SSO-Profil", Icon: User },
  oauth_token: { label: "OAuth-Token", Icon: Shield },
};

const TYPE_COLORS: Record<string, string> = {
  api_key: "bg-violet-500/10 text-violet-400 border-violet-500/20",
  sso_profile: "bg-blue-500/10 text-blue-400 border-blue-500/20",
  oauth_token: "bg-amber-500/10 text-amber-700 dark:text-amber-400 border-amber-500/20",
};

const TYPE_COPY = {
  api_key: {
    namePlaceholder: "z. B. GitHub-Zugangstoken",
    envPlaceholder: "GIT_PAT",
    valueLabel: "API-Schlüssel",
    valuePlaceholder: "ghp_… / sk-… / Token …",
    descriptionPlaceholder: "Wird den zugewiesenen Agenten als Umgebungsvariable mitgegeben",
  },
  sso_profile: {
    namePlaceholder: "z. B. SSO-Profil Supabase",
    envPlaceholder: "SSO_PROFILE_SUPABASE",
    valueLabel: "SSO-Profil / Geheimnis",
    valuePlaceholder:
      '{\n  "provider": "supabase",\n  "server_url": "https://example.com",\n  "token": "paste-secret-here"\n}',
    descriptionPlaceholder: "Zugangsdaten, mit denen sich die zugewiesenen Agenten an einem Server anmelden",
  },
  oauth_token: {
    namePlaceholder: "z. B. OpenAI-OAuth-Token",
    envPlaceholder: "OPENAI_OAUTH_TOKEN",
    valueLabel: "OAuth-Token / Anmelde-JSON",
    valuePlaceholder: "OAuth-Token oder Anmelde-JSON einfügen …",
    descriptionPlaceholder: "Token oder Anmeldedaten, die den zugewiesenen Agenten mitgegeben werden",
  },
} as const;

function normalizeEnvName(value: string) {
  return value
    .trim()
    .toUpperCase()
    .replace(/[^A-Z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
}

function defaultEnvName(name: string, type: keyof typeof TYPE_COPY) {
  const normalized = normalizeEnvName(name);
  if (!normalized) return "";
  if (type === "sso_profile" && !normalized.startsWith("SSO_")) return `SSO_PROFILE_${normalized}`;
  if (type === "oauth_token" && !normalized.includes("OAUTH")) return `${normalized}_OAUTH_TOKEN`;
  return normalized;
}

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
  const [keyNameTouched, setKeyNameTouched] = useState(false);
  const [editForm, setEditForm] = useState<{
    name: string;
    description: string;
    value: string;
    is_active: boolean;
    secret_type: "api_key" | "sso_profile" | "oauth_token";
    key_name: string;
  }>({
    name: "", description: "", value: "", is_active: true, secret_type: "api_key", key_name: "",
  });
  const [saving, setSaving] = useState(false);
  // Freigabe an Personen (nie an Rollen — das macht ein Admin in den Rollen).
  const [teilen, setTeilen] = useState<AgentSecretEntry | null>(null);

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
      showToast("error", "Name, Variablenname und Wert werden benötigt.");
      return;
    }
    setSaving(true);
    try {
      await api.createSecret(form);
      showToast("success", "Schlüssel angelegt");
      setShowCreate(false);
      setForm({ name: "", key_name: "", value: "", secret_type: "api_key", description: "" });
      setKeyNameTouched(false);
      await load();
    } catch {
      showToast("error", "Schlüssel konnte nicht angelegt werden");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(id: number) {
    setDeleting(id);
    try {
      await api.deleteSecret(id);
      showToast("success", "Schlüssel gelöscht");
      await load();
    } catch {
      showToast("error", "Schlüssel konnte nicht gelöscht werden");
    } finally {
      setDeleting(null);
    }
  }

  function startEdit(s: AgentSecretEntry) {
    setEditingId(s.id);
    setEditForm({
      name: s.name,
      description: s.description,
      value: "",
      is_active: s.is_active,
      secret_type: s.secret_type,
      key_name: s.key_name,
    });
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
      showToast("success", "Schlüssel gespeichert");
      setEditingId(null);
      await load();
    } catch {
      showToast("error", "Schlüssel konnte nicht gespeichert werden");
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
    showToast("success", `Variablenname kopiert: ${s.key_name}`);
  }

  const createCopy = TYPE_COPY[form.secret_type];

  return (
    <div className={embedded ? "" : "flex flex-col h-screen bg-background"}>
      {!embedded && <Header title="Schlüssel & Zugangsdaten" subtitle="Verschlüsselte API-Schlüssel, SSO-Profile und OAuth-Token" />}
      <div className={embedded ? "max-w-4xl mx-auto w-full" : "flex-1 overflow-auto p-6 max-w-4xl mx-auto w-full"}>
        {/* Page header */}
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-xl font-semibold flex items-center gap-2">
              <KeyRound size={20} className="text-primary" />
              Schlüssel & Zugangsdaten
            </h1>
            <p className="text-sm text-muted-foreground mt-0.5">
              Verschlüsselte API-Schlüssel, SSO-Profile und OAuth-Token — beim Start der Agenten als Umgebungsvariablen mitgegeben.
            </p>
          </div>
          <button
            onClick={() => setShowCreate(v => !v)}
            className="flex items-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground shadow-lg shadow-primary/20"
          >
            <Plus size={16} />
            Neuer Schlüssel
          </button>
        </div>

        {/* Create form */}
        {showCreate && (
          <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-5 mb-4">
            <h2 className="text-sm font-semibold mb-4">Neuer Schlüssel</h2>
            <div className="grid grid-cols-2 gap-3">
              <div className="col-span-2 flex flex-col gap-1">
                <label className="text-[11px] font-medium text-muted-foreground/70">Art</label>
                <div className="grid grid-cols-3 gap-2">
                  {(["api_key", "sso_profile", "oauth_token"] as const).map(type => {
                    const { label, Icon } = TYPE_LABELS[type];
                    const selected = form.secret_type === type;
                    return (
                      <button
                        key={type}
                        type="button"
                        onClick={() => {
                          setForm(p => {
                            const next = { ...p, secret_type: type };
                            if (!keyNameTouched) next.key_name = defaultEnvName(p.name, type);
                            return next;
                          });
                        }}
                        className={cn(
                          "flex items-center gap-2 rounded-lg border px-3.5 py-2.5 text-left text-sm transition-colors",
                          selected
                            ? "border-primary/50 bg-primary/10 text-foreground"
                            : "border-foreground/[0.08] bg-foreground/[0.02] text-muted-foreground hover:text-foreground"
                        )}
                      >
                        <Icon size={15} />
                        {label}
                      </button>
                    );
                  })}
                </div>
              </div>
              <div className="flex flex-col gap-1">
                <label className="text-[11px] font-medium text-muted-foreground/70">Name</label>
                <input
                  placeholder={createCopy.namePlaceholder}
                  value={form.name}
                  onChange={e => {
                    const name = e.target.value;
                    setForm(p => ({
                      ...p,
                      name,
                      key_name: keyNameTouched ? p.key_name : defaultEnvName(name, p.secret_type),
                    }));
                  }}
                  className="rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3.5 py-2.5 text-sm"
                />
              </div>
              <div className="flex flex-col gap-1">
                <label className="text-[11px] font-medium text-muted-foreground/70">Name der Umgebungsvariable</label>
                <input
                  placeholder={createCopy.envPlaceholder}
                  value={form.key_name}
                  onChange={e => {
                    setKeyNameTouched(true);
                    setForm(p => ({ ...p, key_name: normalizeEnvName(e.target.value) }));
                  }}
                  className="rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3.5 py-2.5 text-sm font-mono"
                />
              </div>
              <div className="col-span-2 flex flex-col gap-1">
                <label className="text-[11px] font-medium text-muted-foreground/70">{createCopy.valueLabel}</label>
                <textarea
                  rows={form.secret_type === "api_key" ? 3 : 7}
                  placeholder={createCopy.valuePlaceholder}
                  value={form.value}
                  onChange={e => setForm(p => ({ ...p, value: e.target.value }))}
                  className="resize-y rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3.5 py-2.5 text-sm font-mono leading-relaxed"
                />
              </div>
              <div className="col-span-2 flex flex-col gap-1">
                <label className="text-[11px] font-medium text-muted-foreground/70">Beschreibung (optional)</label>
                <input
                  placeholder={createCopy.descriptionPlaceholder}
                  value={form.description}
                  onChange={e => setForm(p => ({ ...p, description: e.target.value }))}
                  className="rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3.5 py-2.5 text-sm"
                />
              </div>
              <p className="col-span-2 text-[11px] leading-relaxed text-muted-foreground/60">
                Zugewiesene Agenten bekommen den Wert als <span className="font-mono text-muted-foreground">{form.key_name || createCopy.envPlaceholder}</span>.
                Das Modell nutzt den Variablennamen in Werkzeugen und Skripten und gibt den Wert nie aus.
              </p>
            </div>
            <div className="flex justify-end gap-2 mt-4">
              <button onClick={() => setShowCreate(false)} className="rounded-xl px-4 py-2 text-sm text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04]">
                Abbrechen
              </button>
              <button onClick={handleCreate} disabled={saving} className="flex items-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground shadow-lg shadow-primary/20 disabled:opacity-50">
                {saving ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
                Anlegen
              </button>
            </div>
          </div>
        )}

        {/* Secret list */}
        {loading ? (
          <div className="flex items-center justify-center py-16 text-muted-foreground">
            <Loader2 className="animate-spin mr-2" size={20} />
            Schlüssel werden geladen …
          </div>
        ) : secrets.length === 0 ? (
          <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-12 text-center">
            <KeyRound size={32} className="mx-auto mb-3 text-muted-foreground/40" />
            <p className="text-sm text-muted-foreground">Noch keine Schlüssel. Lege oben einen API-Schlüssel oder ein SSO-Profil an.</p>
          </div>
        ) : (
          <div className="flex flex-col gap-3">
            {secrets.map(s => {
              const { label, Icon } = TYPE_LABELS[s.secret_type] ?? { label: s.secret_type, Icon: KeyRound };
              const isEditing = editingId === s.id;
              const revealed = revealedIds.has(s.id);
              const editCopy = TYPE_COPY[editForm.secret_type] ?? TYPE_COPY.api_key;

              return (
                <div key={s.id} className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-5">
                  {isEditing ? (
                    <div className="flex flex-col gap-3">
                      <div className="flex items-center gap-2">
                        <span className={cn("inline-flex items-center rounded-full border px-2.5 py-1 text-[11px] font-medium", TYPE_COLORS[editForm.secret_type])}>
                          {TYPE_LABELS[editForm.secret_type]?.label ?? editForm.secret_type}
                        </span>
                        <span className="text-xs text-muted-foreground/50">
                          Der Wert ist verschlüsselt. Bleibt das Ersatzfeld leer, gilt der bisherige.
                        </span>
                      </div>
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
                          <label className="text-[11px] font-medium text-muted-foreground/70">Env-Var Name</label>
                          <input
                            value={editForm.key_name}
                            readOnly
                            className="rounded-lg border border-foreground/[0.08] bg-foreground/[0.03] px-3.5 py-2.5 text-sm font-mono text-muted-foreground"
                          />
                        </div>
                        <div className="col-span-2 flex flex-col gap-1">
                          <label className="text-[11px] font-medium text-muted-foreground/70">
                            {editCopy.valueLabel} ersetzen (leer lassen = behalten)
                          </label>
                          <textarea
                            rows={editForm.secret_type === "api_key" ? 3 : 8}
                            placeholder={editCopy.valuePlaceholder}
                            value={editForm.value}
                            onChange={e => setEditForm(p => ({ ...p, value: e.target.value }))}
                            className="resize-y rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3.5 py-2.5 text-sm font-mono leading-relaxed"
                          />
                          {editForm.secret_type === "sso_profile" && (
                            <p className="text-[11px] leading-relaxed text-muted-foreground/60">
                              Für SSO-Profile alle Angaben hier als JSON hinterlegen, etwa Server-Adresse,
                              Aussteller, Zielgruppe, Token und optionale Header. Der zugewiesene Agent liest sie aus
                              <span className="font-mono text-muted-foreground"> ${editForm.key_name}</span>.
                            </p>
                          )}
                        </div>
                        <div className="col-span-2 flex flex-col gap-1">
                          <label className="text-[11px] font-medium text-muted-foreground/70">Beschreibung</label>
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
                          Aktiv
                        </label>
                        <div className="flex gap-2">
                          <button onClick={() => setEditingId(null)} className="rounded-xl px-3 py-1.5 text-sm text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04]">
                            Abbrechen
                          </button>
                          <button onClick={() => handleUpdate(s.id)} disabled={saving} className="flex items-center gap-2 rounded-xl bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground shadow-lg shadow-primary/20 disabled:opacity-50">
                            {saving ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />}
                            Speichern
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
                                Inaktiv
                              </span>
                            )}
                            {s.zugang === "person" && (
                              <span className="inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-[11px] font-medium bg-blue-500/10 text-blue-700 dark:text-blue-300 border-blue-500/20">
                                <Users size={11} /> Freigegeben von {s.owner_name || "einer Person"}
                              </span>
                            )}
                            {s.zugang === "rolle" && (
                              <span className="inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-[11px] font-medium bg-foreground/[0.04] text-muted-foreground border-foreground/[0.08]">
                                <Shield size={11} /> Über deine Rolle freigegeben
                              </span>
                            )}
                            {s.manageable && !!s.shared_with_count && (
                              <span className="inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-[11px] font-medium bg-foreground/[0.04] text-muted-foreground border-foreground/[0.08]">
                                <Users size={11} /> An {s.shared_with_count} Person{s.shared_with_count === 1 ? "" : "en"} freigegeben
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
                              Zugewiesen an {s.assigned_agent_ids.length} Agent{s.assigned_agent_ids.length !== 1 ? "en" : ""}
                            </p>
                          )}
                        </div>
                      </div>
                      {s.manageable && (
                      <div className="flex items-center gap-1 flex-shrink-0">
                        <button
                          onClick={() => setTeilen(s)}
                          title="An Personen freigeben"
                          aria-label="An Personen freigeben"
                          className="rounded-lg p-2 text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04] transition-colors"
                        >
                          <Users size={14} />
                        </button>
                        <button
                          onClick={() => startEdit(s)}
                          title="Bearbeiten"
                          aria-label="Bearbeiten"
                          className="rounded-lg p-2 text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04] transition-colors"
                        >
                          <Pencil size={14} />
                        </button>
                        <button
                          onClick={() => handleDelete(s.id)}
                          title="Löschen"
                          aria-label="Löschen"
                          disabled={deleting === s.id}
                          className="rounded-lg p-2 text-muted-foreground hover:text-red-400 hover:bg-red-500/[0.06] transition-colors disabled:opacity-40"
                        >
                          {deleting === s.id ? <Loader2 size={14} className="animate-spin" /> : <Trash2 size={14} />}
                        </button>
                      </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
      {teilen && (
        <KeyFreigabeDialog
          secret={teilen}
          onClose={() => setTeilen(null)}
          onSaved={(meldung) => { showToast("success", meldung); void load(); }}
          onError={(meldung) => showToast("error", meldung)}
        />
      )}
      {toast && <Toast type={toast.type} message={toast.message} onClose={() => setToast(null)} />}
    </div>
  );
}

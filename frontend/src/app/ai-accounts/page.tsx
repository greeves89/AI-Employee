"use client";

import { useState, useEffect, useCallback } from "react";
import { Cpu, Plus, Trash2, Pencil, Check, X, Loader2, Power } from "lucide-react";
import { Header } from "@/components/layout/header";
import { cn } from "@/lib/utils";
import * as api from "@/lib/api";
import type { AIAccount } from "@/lib/types";

const PROVIDERS = [
  { value: "azure-openai", label: "Azure OpenAI" },
  { value: "openai", label: "OpenAI" },
  { value: "anthropic", label: "Anthropic" },
  { value: "google", label: "Google" },
  { value: "ollama", label: "Ollama" },
  { value: "lm-studio", label: "LM Studio" },
] as const;

const PROVIDER_COLORS: Record<string, string> = {
  "azure-openai": "bg-sky-500/10 text-sky-400 border-sky-500/20",
  openai: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
  anthropic: "bg-orange-500/10 text-orange-400 border-orange-500/20",
  google: "bg-blue-500/10 text-blue-400 border-blue-500/20",
  ollama: "bg-violet-500/10 text-violet-400 border-violet-500/20",
  "lm-studio": "bg-violet-500/10 text-violet-400 border-violet-500/20",
};

type FormState = {
  name: string;
  provider_type: string;
  api_endpoint: string;
  api_key: string;
  models: string;       // comma-separated; for Azure these are deployment names
  api_version: string;
};

const EMPTY_FORM: FormState = {
  name: "", provider_type: "azure-openai", api_endpoint: "",
  api_key: "", models: "", api_version: "",
};

function Toast({ type, message, onClose }: { type: "success" | "error"; message: string; onClose: () => void }) {
  useEffect(() => { const t = setTimeout(onClose, 3500); return () => clearTimeout(t); }, [onClose]);
  return (
    <div className={cn(
      "fixed bottom-6 right-6 z-50 flex items-center gap-3 rounded-xl px-4 py-3 text-sm font-medium shadow-xl",
      type === "success"
        ? "bg-emerald-500/20 text-emerald-300 border border-emerald-500/30"
        : "bg-red-500/20 text-red-300 border border-red-500/30"
    )}>
      {type === "success" ? <Check size={16} /> : <X size={16} />}
      {message}
    </div>
  );
}

export default function AIAccountsPage() {
  const [accounts, setAccounts] = useState<AIAccount[]>([]);
  const [loading, setLoading] = useState(true);
  const [toast, setToast] = useState<{ type: "success" | "error"; message: string } | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState<number | null>(null);

  const showToast = (type: "success" | "error", message: string) => setToast({ type, message });

  const load = useCallback(async () => {
    try {
      setAccounts(await api.listAIAccounts());
    } catch {
      showToast("error", "Konnte AI-Accounts nicht laden");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const openCreate = () => { setForm(EMPTY_FORM); setEditingId(null); setShowForm(true); };
  const openEdit = (a: AIAccount) => {
    const extra = a.extra || {};
    setForm({
      name: a.name,
      provider_type: a.provider_type,
      api_endpoint: a.api_endpoint || "",
      api_key: "",
      models: (a.models || []).join(", "),
      api_version: String(extra.api_version || ""),
    });
    setEditingId(a.id);
    setShowForm(true);
  };

  const save = async () => {
    const modelList = form.models.split(",").map((m) => m.trim()).filter(Boolean);
    if (!form.name.trim() || modelList.length === 0) {
      showToast("error", "Name und mindestens ein Modell sind Pflicht");
      return;
    }
    setSaving(true);
    try {
      const extra: Record<string, unknown> = {};
      if (form.provider_type === "azure-openai" && form.api_version) {
        extra.api_version = form.api_version;
      }
      const payload = {
        name: form.name.trim(),
        provider_type: form.provider_type,
        api_endpoint: form.api_endpoint.trim() || null,
        models: modelList,
        extra,
        ...(form.api_key ? { api_key: form.api_key } : {}),
      };
      if (editingId) {
        await api.updateAIAccount(editingId, payload);
        showToast("success", "AI-Account aktualisiert");
      } else {
        await api.createAIAccount(payload);
        showToast("success", "AI-Account erstellt");
      }
      setShowForm(false);
      await load();
    } catch (e) {
      showToast("error", e instanceof Error ? e.message : "Speichern fehlgeschlagen");
    } finally {
      setSaving(false);
    }
  };

  const toggleActive = async (a: AIAccount) => {
    try {
      await api.updateAIAccount(a.id, { is_active: !a.is_active });
      await load();
    } catch {
      showToast("error", "Status konnte nicht geändert werden");
    }
  };

  const remove = async (a: AIAccount) => {
    setDeleting(a.id);
    try {
      await api.deleteAIAccount(a.id);
      showToast("success", "AI-Account gelöscht");
      await load();
    } catch {
      showToast("error", "Löschen fehlgeschlagen");
    } finally {
      setDeleting(null);
    }
  };

  const inputCls = "w-full rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3.5 py-2.5 text-sm outline-none focus:border-primary/50";

  return (
    <div className="min-h-screen">
      <Header title="AI-Accounts" subtitle="Wiederverwendbare Modell-Zugänge — einmal anlegen, an Agents hängen" />
      <div className="mx-auto max-w-4xl px-6 py-8">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-xl font-semibold flex items-center gap-2">
              <Cpu className="h-5 w-5 text-primary" /> AI-Accounts
            </h1>
            <p className="text-sm text-muted-foreground/70 mt-1">
              Wiederverwendbare Modell-Zugänge — einmal anlegen, an beliebige Agents hängen.
            </p>
          </div>
          <button
            onClick={openCreate}
            className="inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground shadow-lg shadow-primary/20"
          >
            <Plus className="h-4 w-4" /> Neuer Account
          </button>
        </div>

        {showForm && (
          <div className="mb-6 rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-5 space-y-3">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold">{editingId ? "Account bearbeiten" : "Neuer Account"}</h2>
              <button onClick={() => setShowForm(false)} className="text-muted-foreground hover:text-foreground">
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-[11px] font-medium text-muted-foreground/70 mb-1">Name</label>
                <input className={inputCls} value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                  placeholder="z.B. Azure GPT-4o Prod" />
              </div>
              <div>
                <label className="block text-[11px] font-medium text-muted-foreground/70 mb-1">Provider</label>
                <select className={inputCls} value={form.provider_type}
                  onChange={(e) => setForm({ ...form, provider_type: e.target.value })}>
                  {PROVIDERS.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
                </select>
              </div>
              <div>
                <label className="block text-[11px] font-medium text-muted-foreground/70 mb-1">API-Endpoint</label>
                <input className={inputCls} value={form.api_endpoint}
                  onChange={(e) => setForm({ ...form, api_endpoint: e.target.value })}
                  placeholder="https://…" />
              </div>
              <div className="col-span-2">
                <label className="block text-[11px] font-medium text-muted-foreground/70 mb-1">
                  Modelle <span className="text-muted-foreground/40">(kommagetrennt)</span>
                </label>
                <input className={inputCls} value={form.models}
                  onChange={(e) => setForm({ ...form, models: e.target.value })}
                  placeholder={form.provider_type === "azure-openai"
                    ? "Deployment-Namen, z.B. gpt-4o, gpt-4o-mini, o1"
                    : "z.B. gpt-4o, gpt-4o-mini"} />
                <p className="text-[10px] text-muted-foreground/50 mt-1">
                  {form.provider_type === "azure-openai"
                    ? "Bei Azure: die Deployment-Namen. Der Agent wählt beim Verbinden eines davon."
                    : "Der Agent wählt beim Verbinden eines dieser Modelle."}
                </p>
              </div>
              <div className="col-span-2">
                <label className="block text-[11px] font-medium text-muted-foreground/70 mb-1">
                  API-Key {editingId && <span className="text-muted-foreground/40">(leer = unverändert)</span>}
                </label>
                <input className={inputCls} type="password" value={form.api_key}
                  onChange={(e) => setForm({ ...form, api_key: e.target.value })}
                  placeholder="sk-… / Azure-Key" />
              </div>
              {form.provider_type === "azure-openai" && (
                <div className="col-span-2">
                  <label className="block text-[11px] font-medium text-muted-foreground/70 mb-1">Azure api-version</label>
                  <input className={inputCls} value={form.api_version}
                    onChange={(e) => setForm({ ...form, api_version: e.target.value })}
                    placeholder="2024-08-01-preview" />
                </div>
              )}
            </div>
            <div className="flex justify-end gap-2 pt-1">
              <button onClick={() => setShowForm(false)}
                className="rounded-xl px-4 py-2 text-sm text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04]">
                Abbrechen
              </button>
              <button onClick={save} disabled={saving}
                className="inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50">
                {saving && <Loader2 className="h-3.5 w-3.5 animate-spin" />} Speichern
              </button>
            </div>
          </div>
        )}

        {loading ? (
          <div className="flex justify-center py-20"><Loader2 className="h-6 w-6 animate-spin text-muted-foreground" /></div>
        ) : accounts.length === 0 ? (
          <div className="text-center py-16 text-muted-foreground">
            <Cpu className="h-8 w-8 mx-auto mb-2 opacity-30" />
            Noch keine AI-Accounts. Lege den ersten an.
          </div>
        ) : (
          <div className="space-y-2">
            {accounts.map((a) => (
              <div key={a.id} className={cn(
                "rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-4 flex items-center gap-4",
                !a.is_active && "opacity-50"
              )}>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium truncate">{a.name}</span>
                    <span className={cn("inline-flex items-center rounded-full border px-2.5 py-1 text-[11px] font-medium", PROVIDER_COLORS[a.provider_type])}>
                      {a.provider_type}
                    </span>
                    {!a.has_key && a.provider_type !== "ollama" && a.provider_type !== "lm-studio" && (
                      <span className="text-[10px] text-amber-400">kein Key</span>
                    )}
                  </div>
                  <p className="text-[11px] text-muted-foreground/60 mt-0.5 truncate">
                    {(a.models || []).join(", ") || "— keine Modelle —"}
                    {a.api_endpoint ? ` · ${a.api_endpoint}` : ""}
                  </p>
                </div>
                <button onClick={() => toggleActive(a)} title={a.is_active ? "Deaktivieren" : "Aktivieren"}
                  className={cn("rounded-lg p-2 hover:bg-foreground/[0.06]", a.is_active ? "text-emerald-400" : "text-muted-foreground")}>
                  <Power className="h-4 w-4" />
                </button>
                <button onClick={() => openEdit(a)} className="rounded-lg p-2 text-muted-foreground hover:text-foreground hover:bg-foreground/[0.06]">
                  <Pencil className="h-4 w-4" />
                </button>
                <button onClick={() => remove(a)} disabled={deleting === a.id}
                  className="rounded-lg p-2 text-muted-foreground hover:text-red-400 hover:bg-red-500/[0.06]">
                  {deleting === a.id ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
      {toast && <Toast type={toast.type} message={toast.message} onClose={() => setToast(null)} />}
    </div>
  );
}

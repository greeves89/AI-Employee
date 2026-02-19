"use client";

import { useState, useEffect, useCallback } from "react";
import {
  Bot, Code2, BarChart3, FileText, Server, Search, Presentation,
  Pencil, Trash2, Save, X, Plus, Lock, ChevronDown, ChevronUp,
  Loader2, CheckCircle2, AlertCircle, Copy,
} from "lucide-react";
import { cn } from "@/lib/utils";
import * as api from "@/lib/api";
import type { AgentTemplate } from "@/lib/types";

const TEMPLATE_ICON_MAP: Record<string, React.ElementType> = {
  Bot, Code2, BarChart3, FileText, Server, Search, Presentation,
};

const CATEGORY_LABELS: Record<string, string> = {
  dev: "Development",
  data: "Data & Analytics",
  writing: "Writing & Docs",
  ops: "Operations",
  creative: "Creative",
  general: "General",
};

const CATEGORY_COLORS: Record<string, string> = {
  dev: "bg-blue-500/10 text-blue-400 border-blue-500/20",
  data: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
  writing: "bg-purple-500/10 text-purple-400 border-purple-500/20",
  ops: "bg-amber-500/10 text-amber-400 border-amber-500/20",
  creative: "bg-pink-500/10 text-pink-400 border-pink-500/20",
  general: "bg-gray-500/10 text-gray-400 border-gray-500/20",
};

const AVAILABLE_PERMISSIONS = [
  { id: "package-install", label: "Paket-Installation", desc: "apt-get, dpkg" },
  { id: "system-config", label: "System-Konfiguration", desc: "chmod, chown, mkdir, ln" },
  { id: "full-access", label: "Vollzugriff", desc: "Alle Befehle erlaubt" },
];

const AVAILABLE_INTEGRATIONS = [
  { id: "google", label: "Google", desc: "Gmail, Calendar, Drive" },
  { id: "github", label: "GitHub", desc: "Repos, Issues, PRs" },
];

const ICON_OPTIONS = Object.keys(TEMPLATE_ICON_MAP);
const CATEGORY_OPTIONS = Object.keys(CATEGORY_LABELS);

interface TemplateManagerProps {
  isAdmin: boolean;
}

interface EditState {
  display_name: string;
  description: string;
  icon: string;
  category: string;
  role: string;
  permissions: string[];
  integrations: string[];
  knowledge_template: string;
}

function templateToEditState(t: AgentTemplate): EditState {
  return {
    display_name: t.display_name,
    description: t.description,
    icon: t.icon,
    category: t.category,
    role: t.role,
    permissions: [...t.permissions],
    integrations: [...t.integrations],
    knowledge_template: t.knowledge_template || "",
  };
}

export function TemplateManager({ isAdmin }: TemplateManagerProps) {
  const [templates, setTemplates] = useState<AgentTemplate[]>([]);
  const [loading, setLoading] = useState(true);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editState, setEditState] = useState<EditState | null>(null);
  const [saving, setSaving] = useState(false);
  const [creating, setCreating] = useState(false);
  const [newTemplate, setNewTemplate] = useState<EditState & { name: string }>({
    name: "",
    display_name: "",
    description: "",
    icon: "Bot",
    category: "general",
    role: "",
    permissions: [],
    integrations: [],
    knowledge_template: "",
  });
  const [message, setMessage] = useState<{ type: "success" | "error"; text: string } | null>(null);

  const fetchTemplates = useCallback(async () => {
    try {
      const data = await api.getTemplates();
      setTemplates(data.templates);
    } catch (e) {
      setMessage({ type: "error", text: `Fehler beim Laden: ${e}` });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchTemplates(); }, [fetchTemplates]);

  useEffect(() => {
    if (message) {
      const t = setTimeout(() => setMessage(null), 4000);
      return () => clearTimeout(t);
    }
  }, [message]);

  const handleExpand = (id: number) => {
    if (expandedId === id) {
      setExpandedId(null);
      setEditingId(null);
      setEditState(null);
    } else {
      setExpandedId(id);
      setEditingId(null);
      setEditState(null);
    }
  };

  const handleEdit = (t: AgentTemplate) => {
    setEditingId(t.id);
    setEditState(templateToEditState(t));
  };

  const handleCancel = () => {
    setEditingId(null);
    setEditState(null);
  };

  const handleSave = async (id: number) => {
    if (!editState) return;
    setSaving(true);
    try {
      await api.updateTemplate(id, editState);
      setMessage({ type: "success", text: "Template gespeichert" });
      setEditingId(null);
      setEditState(null);
      await fetchTemplates();
    } catch (e) {
      setMessage({ type: "error", text: `Fehler: ${e}` });
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: number) => {
    if (!confirm("Template wirklich loeschen?")) return;
    try {
      await api.deleteTemplate(id);
      setMessage({ type: "success", text: "Template geloescht" });
      setExpandedId(null);
      await fetchTemplates();
    } catch (e) {
      setMessage({ type: "error", text: `Fehler: ${e}` });
    }
  };

  const handleCreate = async () => {
    if (!newTemplate.name || !newTemplate.display_name) {
      setMessage({ type: "error", text: "Name und Anzeigename sind Pflicht" });
      return;
    }
    setSaving(true);
    try {
      await api.createTemplate(newTemplate);
      setMessage({ type: "success", text: "Template erstellt" });
      setCreating(false);
      setNewTemplate({
        name: "", display_name: "", description: "", icon: "Bot",
        category: "general", role: "", permissions: [], integrations: [], knowledge_template: "",
      });
      await fetchTemplates();
    } catch (e) {
      setMessage({ type: "error", text: `Fehler: ${e}` });
    } finally {
      setSaving(false);
    }
  };

  const toggleArrayItem = (arr: string[], item: string): string[] =>
    arr.includes(item) ? arr.filter(i => i !== item) : [...arr, item];

  if (loading) {
    return (
      <div className="flex items-center justify-center py-8">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {/* Message toast */}
      {message && (
        <div className={cn(
          "flex items-center gap-2 rounded-lg px-3 py-2 text-sm",
          message.type === "success" ? "bg-emerald-500/10 text-emerald-400" : "bg-red-500/10 text-red-400",
        )}>
          {message.type === "success" ? <CheckCircle2 className="h-4 w-4" /> : <AlertCircle className="h-4 w-4" />}
          {message.text}
        </div>
      )}

      {/* Template list */}
      {templates.map((t) => {
        const Icon = TEMPLATE_ICON_MAP[t.icon] || Bot;
        const isExpanded = expandedId === t.id;
        const isEditing = editingId === t.id;
        const canEdit = isAdmin || (!t.is_builtin && t.created_by !== null);

        return (
          <div key={t.id} className="rounded-xl border border-foreground/[0.06] bg-card/80 overflow-hidden">
            {/* Header row */}
            <button
              onClick={() => handleExpand(t.id)}
              className="w-full flex items-center gap-3 px-4 py-3 hover:bg-foreground/[0.02] transition-colors"
            >
              <div className={cn(
                "flex h-8 w-8 items-center justify-center rounded-lg border",
                CATEGORY_COLORS[t.category] || CATEGORY_COLORS.general,
              )}>
                <Icon className="h-4 w-4" />
              </div>
              <div className="flex-1 text-left">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium">{t.display_name}</span>
                  {t.is_builtin && <Lock className="h-3 w-3 text-muted-foreground/40" />}
                  <span className={cn(
                    "text-[10px] px-1.5 py-0.5 rounded-full border",
                    CATEGORY_COLORS[t.category] || CATEGORY_COLORS.general,
                  )}>
                    {CATEGORY_LABELS[t.category] || t.category}
                  </span>
                </div>
                <p className="text-xs text-muted-foreground/60 truncate">{t.description}</p>
              </div>
              {isExpanded ? <ChevronUp className="h-4 w-4 text-muted-foreground/40" /> : <ChevronDown className="h-4 w-4 text-muted-foreground/40" />}
            </button>

            {/* Expanded content */}
            {isExpanded && (
              <div className="border-t border-foreground/[0.06] px-4 py-3 space-y-4">
                {!isEditing ? (
                  /* Read-only view */
                  <>
                    <div className="grid grid-cols-2 gap-3 text-sm">
                      <div>
                        <span className="text-muted-foreground/60 text-xs">Rolle</span>
                        <p className="text-foreground/80">{t.role || "–"}</p>
                      </div>
                      <div>
                        <span className="text-muted-foreground/60 text-xs">Model</span>
                        <p className="text-foreground/80">{t.model}</p>
                      </div>
                      <div>
                        <span className="text-muted-foreground/60 text-xs">Berechtigungen</span>
                        <p className="text-foreground/80">{t.permissions.length > 0 ? t.permissions.join(", ") : "–"}</p>
                      </div>
                      <div>
                        <span className="text-muted-foreground/60 text-xs">Integrationen</span>
                        <p className="text-foreground/80">{t.integrations.length > 0 ? t.integrations.join(", ") : "–"}</p>
                      </div>
                    </div>
                    {t.knowledge_template && (
                      <div>
                        <span className="text-muted-foreground/60 text-xs">Knowledge Template</span>
                        <pre className="mt-1 max-h-48 overflow-auto rounded-lg bg-background/60 p-3 text-xs text-foreground/70 font-mono whitespace-pre-wrap">
                          {t.knowledge_template}
                        </pre>
                      </div>
                    )}
                    <div className="flex items-center gap-2 pt-1">
                      {canEdit && (
                        <button
                          onClick={() => handleEdit(t)}
                          className="flex items-center gap-1.5 rounded-lg bg-foreground/[0.06] px-3 py-1.5 text-xs hover:bg-foreground/[0.1] transition-colors"
                        >
                          <Pencil className="h-3 w-3" /> Bearbeiten
                        </button>
                      )}
                      {!t.is_builtin && canEdit && (
                        <button
                          onClick={() => handleDelete(t.id)}
                          className="flex items-center gap-1.5 rounded-lg bg-red-500/10 text-red-400 px-3 py-1.5 text-xs hover:bg-red-500/20 transition-colors"
                        >
                          <Trash2 className="h-3 w-3" /> Loeschen
                        </button>
                      )}
                    </div>
                  </>
                ) : (
                  /* Edit view */
                  <TemplateEditForm
                    state={editState!}
                    onChange={setEditState as (s: EditState) => void}
                    onSave={() => handleSave(t.id)}
                    onCancel={handleCancel}
                    saving={saving}
                    toggleArrayItem={toggleArrayItem}
                    isBuiltin={t.is_builtin}
                  />
                )}
              </div>
            )}
          </div>
        );
      })}

      {/* Create new template */}
      {creating ? (
        <div className="rounded-xl border border-emerald-500/20 bg-card/80 p-4 space-y-4">
          <h3 className="text-sm font-medium text-emerald-400">Neues Template erstellen</h3>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs text-muted-foreground/60">Slug (eindeutig)</label>
              <input
                value={newTemplate.name}
                onChange={e => setNewTemplate(s => ({ ...s, name: e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, "-") }))}
                placeholder="my-template"
                className="mt-1 w-full rounded-lg bg-background/60 border border-foreground/[0.08] px-3 py-2 text-sm"
              />
            </div>
            <div>
              <label className="text-xs text-muted-foreground/60">Anzeigename</label>
              <input
                value={newTemplate.display_name}
                onChange={e => setNewTemplate(s => ({ ...s, display_name: e.target.value }))}
                placeholder="Mein Template"
                className="mt-1 w-full rounded-lg bg-background/60 border border-foreground/[0.08] px-3 py-2 text-sm"
              />
            </div>
          </div>
          <TemplateEditForm
            state={newTemplate}
            onChange={(s) => setNewTemplate(prev => ({ ...prev, ...s }))}
            onSave={handleCreate}
            onCancel={() => setCreating(false)}
            saving={saving}
            toggleArrayItem={toggleArrayItem}
            isBuiltin={false}
          />
        </div>
      ) : (
        <button
          onClick={() => setCreating(true)}
          className="w-full flex items-center justify-center gap-2 rounded-xl border border-dashed border-foreground/[0.1] py-3 text-sm text-muted-foreground/60 hover:border-foreground/[0.2] hover:text-muted-foreground transition-colors"
        >
          <Plus className="h-4 w-4" /> Neues Template erstellen
        </button>
      )}
    </div>
  );
}

/* ── Shared Edit Form ───────────────────────────────────────────── */

function TemplateEditForm({
  state,
  onChange,
  onSave,
  onCancel,
  saving,
  toggleArrayItem,
  isBuiltin,
}: {
  state: EditState;
  onChange: (s: EditState) => void;
  onSave: () => void;
  onCancel: () => void;
  saving: boolean;
  toggleArrayItem: (arr: string[], item: string) => string[];
  isBuiltin: boolean;
}) {
  return (
    <div className="space-y-3">
      {/* Basic fields */}
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="text-xs text-muted-foreground/60">Anzeigename</label>
          <input
            value={state.display_name}
            onChange={e => onChange({ ...state, display_name: e.target.value })}
            className="mt-1 w-full rounded-lg bg-background/60 border border-foreground/[0.08] px-3 py-2 text-sm"
          />
        </div>
        <div>
          <label className="text-xs text-muted-foreground/60">Beschreibung</label>
          <input
            value={state.description}
            onChange={e => onChange({ ...state, description: e.target.value })}
            className="mt-1 w-full rounded-lg bg-background/60 border border-foreground/[0.08] px-3 py-2 text-sm"
          />
        </div>
      </div>

      {/* Icon + Category */}
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="text-xs text-muted-foreground/60">Icon</label>
          <div className="mt-1 flex gap-1">
            {ICON_OPTIONS.map(icon => {
              const I = TEMPLATE_ICON_MAP[icon];
              return (
                <button
                  key={icon}
                  onClick={() => onChange({ ...state, icon })}
                  className={cn(
                    "flex h-8 w-8 items-center justify-center rounded-lg border transition-colors",
                    state.icon === icon
                      ? "border-blue-500/40 bg-blue-500/10 text-blue-400"
                      : "border-foreground/[0.08] text-muted-foreground/40 hover:text-muted-foreground",
                  )}
                >
                  <I className="h-4 w-4" />
                </button>
              );
            })}
          </div>
        </div>
        <div>
          <label className="text-xs text-muted-foreground/60">Kategorie</label>
          <select
            value={state.category}
            onChange={e => onChange({ ...state, category: e.target.value })}
            className="mt-1 w-full rounded-lg bg-background/60 border border-foreground/[0.08] px-3 py-2 text-sm"
          >
            {CATEGORY_OPTIONS.map(cat => (
              <option key={cat} value={cat}>{CATEGORY_LABELS[cat]}</option>
            ))}
          </select>
        </div>
      </div>

      {/* Role */}
      <div>
        <label className="text-xs text-muted-foreground/60">Rolle (System-Beschreibung)</label>
        <input
          value={state.role}
          onChange={e => onChange({ ...state, role: e.target.value })}
          placeholder="Senior Developer with expertise in..."
          className="mt-1 w-full rounded-lg bg-background/60 border border-foreground/[0.08] px-3 py-2 text-sm"
        />
      </div>

      {/* Permissions */}
      <div>
        <label className="text-xs text-muted-foreground/60">Berechtigungen</label>
        <div className="mt-1 flex flex-wrap gap-2">
          {AVAILABLE_PERMISSIONS.map(p => (
            <button
              key={p.id}
              onClick={() => onChange({ ...state, permissions: toggleArrayItem(state.permissions, p.id) })}
              className={cn(
                "flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs transition-colors",
                state.permissions.includes(p.id)
                  ? "border-blue-500/30 bg-blue-500/10 text-blue-400"
                  : "border-foreground/[0.08] text-muted-foreground/50 hover:text-muted-foreground",
              )}
            >
              {state.permissions.includes(p.id) && <CheckCircle2 className="h-3 w-3" />}
              {p.label}
            </button>
          ))}
        </div>
      </div>

      {/* Integrations */}
      <div>
        <label className="text-xs text-muted-foreground/60">Integrationen</label>
        <div className="mt-1 flex flex-wrap gap-2">
          {AVAILABLE_INTEGRATIONS.map(i => (
            <button
              key={i.id}
              onClick={() => onChange({ ...state, integrations: toggleArrayItem(state.integrations, i.id) })}
              className={cn(
                "flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs transition-colors",
                state.integrations.includes(i.id)
                  ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-400"
                  : "border-foreground/[0.08] text-muted-foreground/50 hover:text-muted-foreground",
              )}
            >
              {state.integrations.includes(i.id) && <CheckCircle2 className="h-3 w-3" />}
              {i.label}
            </button>
          ))}
        </div>
      </div>

      {/* Knowledge Template */}
      <div>
        <label className="text-xs text-muted-foreground/60">Knowledge Template (Markdown)</label>
        <textarea
          value={state.knowledge_template}
          onChange={e => onChange({ ...state, knowledge_template: e.target.value })}
          placeholder="## Role: ...\n\n### Skills\n- ..."
          rows={12}
          className="mt-1 w-full rounded-lg bg-background/60 border border-foreground/[0.08] px-3 py-2 text-xs font-mono resize-y min-h-[200px]"
        />
      </div>

      {/* Actions */}
      <div className="flex items-center gap-2 pt-1">
        <button
          onClick={onSave}
          disabled={saving}
          className="flex items-center gap-1.5 rounded-lg bg-blue-600 px-4 py-1.5 text-xs text-white hover:bg-blue-500 transition-colors disabled:opacity-50"
        >
          {saving ? <Loader2 className="h-3 w-3 animate-spin" /> : <Save className="h-3 w-3" />}
          Speichern
        </button>
        <button
          onClick={onCancel}
          className="flex items-center gap-1.5 rounded-lg bg-foreground/[0.06] px-4 py-1.5 text-xs hover:bg-foreground/[0.1] transition-colors"
        >
          <X className="h-3 w-3" /> Abbrechen
        </button>
      </div>
    </div>
  );
}

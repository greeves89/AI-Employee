"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import {
  Sparkles, Search, Download, Loader2, RefreshCw,
  Package, Code2, FileText, Wrench,
  GitBranch, Layers, Repeat, ChefHat,
  CheckCircle2, Bot, ChevronDown, Plus, Pencil, Trash2, X, Save,
  Paperclip, Upload, File as FileIcon,
} from "lucide-react";
import { Header } from "@/components/layout/header";
import { cn } from "@/lib/utils";
import * as api from "@/lib/api";
import type { CatalogSkill, AgentSkill, MarketplaceSkill, SkillFileAttachment } from "@/lib/api";
import type { Agent } from "@/lib/types";

const CATEGORY_CONFIG: Record<string, { label: string; icon: typeof Code2; color: string }> = {
  // DB enum values (uppercase)
  TOOL:     { label: "Tools",      icon: Wrench,    color: "bg-amber-500/10 text-amber-400 border-amber-500/20" },
  WORKFLOW: { label: "Workflows",  icon: GitBranch, color: "bg-blue-500/10 text-blue-400 border-blue-500/20" },
  TEMPLATE: { label: "Templates",  icon: FileText,  color: "bg-purple-500/10 text-purple-400 border-purple-500/20" },
  PATTERN:  { label: "Patterns",   icon: Layers,    color: "bg-pink-500/10 text-pink-400 border-pink-500/20" },
  ROUTINE:  { label: "Routinen",   icon: Repeat,    color: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20" },
  RECIPE:   { label: "Rezepte",    icon: ChefHat,   color: "bg-orange-500/10 text-orange-400 border-orange-500/20" },
  // Legacy lowercase keys (from older crawled skills)
  tools:    { label: "Tools",      icon: Wrench,    color: "bg-amber-500/10 text-amber-400 border-amber-500/20" },
  dev:      { label: "Dev",        icon: Code2,     color: "bg-blue-500/10 text-blue-400 border-blue-500/20" },
};

const EMPTY_SKILL = { name: "", description: "", content: "" };

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

interface SkillDetailModalProps {
  skill: MarketplaceSkill;
  onClose: () => void;
}

function SkillDetailModal({ skill, onClose }: SkillDetailModalProps) {
  const [files, setFiles] = useState<SkillFileAttachment[]>([]);
  const [loadingFiles, setLoadingFiles] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [deletingFile, setDeletingFile] = useState<string | null>(null);
  const [downloadingFile, setDownloadingFile] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [manualDuration, setManualDuration] = useState<string>(
    skill.manual_duration_seconds ? String(Math.round(skill.manual_duration_seconds / 60)) : ""
  );
  const [savingDuration, setSavingDuration] = useState(false);

  const loadFiles = useCallback(async () => {
    if (!skill.id) return;
    setLoadingFiles(true);
    try {
      const res = await api.getSkillFiles(skill.id);
      setFiles(res.files || []);
    } catch {
      // ignore
    } finally {
      setLoadingFiles(false);
    }
  }, [skill.id]);

  useEffect(() => { loadFiles(); }, [loadFiles]);

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file || !skill.id) return;
    setUploading(true);
    setUploadError("");
    try {
      await api.uploadSkillFile(skill.id, file);
      await loadFiles();
    } catch (err: any) {
      setUploadError(err?.message || "Upload fehlgeschlagen");
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const handleDownload = async (filename: string) => {
    if (!skill.id) return;
    setDownloadingFile(filename);
    try {
      await api.downloadSkillFile(skill.id, filename);
    } catch {
      // ignore
    } finally {
      setDownloadingFile(null);
    }
  };

  const handleDeleteFile = async (filename: string) => {
    if (!skill.id) return;
    setDeletingFile(filename);
    try {
      await api.deleteSkillFile(skill.id, filename);
      setFiles(f => f.filter(x => x.filename !== filename));
    } catch {
      // ignore
    } finally {
      setDeletingFile(null);
    }
  };

  const cfg = CATEGORY_CONFIG[skill.category?.toUpperCase()] || CATEGORY_CONFIG.ROUTINE;
  const Icon = cfg.icon;

  const handleSaveDuration = async () => {
    if (!skill.id) return;
    setSavingDuration(true);
    try {
      const minutes = parseFloat(manualDuration);
      const seconds = isNaN(minutes) || manualDuration === "" ? null : Math.round(minutes * 60);
      await api.updateSkillManualDuration(skill.id, seconds);
    } catch {
      // ignore
    } finally {
      setSavingDuration(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
      <div className="w-full max-w-3xl rounded-2xl border border-foreground/[0.08] bg-card shadow-2xl flex flex-col max-h-[90vh]">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-foreground/[0.06] shrink-0">
          <div className="flex items-center gap-3">
            <div className={cn("flex h-9 w-9 items-center justify-center rounded-lg border", cfg.color)}>
              <Icon className="h-4 w-4" />
            </div>
            <div>
              <h2 className="text-sm font-semibold">{skill.name}</h2>
              <p className="text-[10px] text-muted-foreground/60">{skill.description}</p>
            </div>
          </div>
          <button onClick={onClose} className="rounded-lg p-1.5 hover:bg-foreground/[0.06] text-muted-foreground hover:text-foreground transition-all">
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-6 space-y-6">
          {/* Metadata */}
          <div className="grid grid-cols-2 gap-3 text-xs">
            <div className="rounded-lg border border-foreground/[0.06] bg-foreground/[0.02] p-3 space-y-1">
              <p className="text-muted-foreground/60 text-[10px] uppercase tracking-wide">Kategorie</p>
              <p className="font-medium">{cfg.label}</p>
            </div>
            <div className="rounded-lg border border-foreground/[0.06] bg-foreground/[0.02] p-3 space-y-1">
              <p className="text-muted-foreground/60 text-[10px] uppercase tracking-wide">Nutzung</p>
              <p className="font-medium">{skill.usage_count ?? 0}× verwendet</p>
            </div>
            {skill.source_repo && (
              <div className="col-span-2 rounded-lg border border-foreground/[0.06] bg-foreground/[0.02] p-3 space-y-1">
                <p className="text-muted-foreground/60 text-[10px] uppercase tracking-wide">Quelle</p>
                <a
                  href={skill.source_url || `https://github.com/${skill.source_repo}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="font-medium text-primary hover:underline"
                >
                  {skill.source_repo} ↗
                </a>
              </div>
            )}
          </div>

          {/* Manual duration for ROI analytics */}
          {skill.id && (
            <div className="rounded-lg border border-foreground/[0.06] bg-foreground/[0.02] p-3 space-y-2">
              <p className="text-muted-foreground/60 text-[10px] uppercase tracking-wide">Manuelle Dauer (Minuten)</p>
              <p className="text-[11px] text-muted-foreground/50">
                Wie lange würde diese Aufgabe manuell dauern? Basis für die Zeitersparnis-Berechnung in Analytics.
              </p>
              <div className="flex items-center gap-2">
                <input
                  type="number"
                  min="0"
                  step="1"
                  placeholder="z.B. 30"
                  value={manualDuration}
                  onChange={(e) => setManualDuration(e.target.value)}
                  className="w-28 rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3 py-1.5 text-sm focus:outline-none focus:border-primary/50"
                />
                <span className="text-xs text-muted-foreground">min</span>
                <button
                  onClick={handleSaveDuration}
                  disabled={savingDuration}
                  className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium bg-primary/10 text-primary hover:bg-primary/20 border border-primary/20 transition-all disabled:opacity-50"
                >
                  {savingDuration ? <Loader2 className="h-3 w-3 animate-spin" /> : <Save className="h-3 w-3" />}
                  Speichern
                </button>
              </div>
            </div>
          )}

          {/* Instructions preview */}
          {skill.content && (
            <div className="space-y-2">
              <p className="text-[10px] font-medium text-muted-foreground/60 uppercase tracking-wide">Instruktionen</p>
              <div className="rounded-lg border border-foreground/[0.06] bg-foreground/[0.02] p-3 font-mono text-xs text-foreground/70 whitespace-pre-wrap max-h-48 overflow-y-auto">
                {skill.content}
              </div>
            </div>
          )}

          {/* File Attachments */}
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Paperclip className="h-3.5 w-3.5 text-muted-foreground/60" />
                <p className="text-xs font-semibold">Datei-Anhänge</p>
                <span className="text-[10px] text-muted-foreground/50">({files.length})</span>
              </div>
              <div>
                <input
                  ref={fileInputRef}
                  type="file"
                  className="hidden"
                  onChange={handleUpload}
                  accept=".py,.js,.ts,.sh,.bash,.yaml,.yml,.json,.toml,.txt,.md,.csv,.xml,.html,.css,.conf,.cfg,.ini"
                />
                <button
                  onClick={() => fileInputRef.current?.click()}
                  disabled={uploading}
                  className="flex items-center gap-1.5 rounded-lg bg-violet-500/10 text-violet-400 hover:bg-violet-500/20 border border-violet-500/20 px-3 py-1.5 text-xs font-medium transition-all disabled:opacity-50"
                >
                  {uploading ? <Loader2 className="h-3 w-3 animate-spin" /> : <Upload className="h-3 w-3" />}
                  {uploading ? "Hochladen..." : "Datei hinzufügen"}
                </button>
              </div>
            </div>

            {uploadError && (
              <p className="text-xs text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2">{uploadError}</p>
            )}

            <p className="text-[10px] text-muted-foreground/50">
              Dateien werden beim Installieren dieses Skills automatisch in den Agenten-Workspace kopiert
              (<code className="font-mono">/workspace/skills/{skill.name}/</code>).
            </p>

            {loadingFiles ? (
              <div className="flex items-center justify-center py-6">
                <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
              </div>
            ) : files.length === 0 ? (
              <div className="rounded-lg border border-dashed border-foreground/[0.08] bg-foreground/[0.01] p-6 flex flex-col items-center gap-2 text-muted-foreground/50">
                <FileIcon className="h-6 w-6" />
                <p className="text-xs">Noch keine Dateien angehängt</p>
              </div>
            ) : (
              <div className="space-y-1.5">
                {files.map((f) => (
                  <div
                    key={f.filename}
                    className="flex items-center gap-3 rounded-lg border border-foreground/[0.06] bg-foreground/[0.02] px-3 py-2"
                  >
                    <FileIcon className="h-3.5 w-3.5 text-muted-foreground/50 shrink-0" />
                    <div className="flex-1 min-w-0">
                      <p className="text-xs font-medium truncate">{f.filename}</p>
                      <p className="text-[10px] text-muted-foreground/50">{formatBytes(f.size_bytes)} · {f.content_type}</p>
                    </div>
                    <div className="flex items-center gap-1.5 shrink-0">
                      <button
                        onClick={() => handleDownload(f.filename)}
                        disabled={downloadingFile === f.filename}
                        className="rounded-lg p-1.5 text-muted-foreground hover:text-foreground hover:bg-foreground/[0.06] transition-all disabled:opacity-50"
                        title="Herunterladen"
                      >
                        {downloadingFile === f.filename
                          ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                          : <Download className="h-3.5 w-3.5" />}
                      </button>
                      <button
                        onClick={() => handleDeleteFile(f.filename)}
                        disabled={deletingFile === f.filename}
                        className="rounded-lg p-1.5 text-red-400/60 hover:text-red-400 hover:bg-red-500/10 transition-all disabled:opacity-50"
                        title="Löschen"
                      >
                        {deletingFile === f.filename
                          ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                          : <Trash2 className="h-3.5 w-3.5" />}
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end px-6 py-4 border-t border-foreground/[0.06] shrink-0">
          <button
            onClick={onClose}
            className="rounded-xl px-4 py-2 text-sm text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04] transition-all"
          >
            Schließen
          </button>
        </div>
      </div>
    </div>
  );
}

interface SkillModalProps {
  initial?: AgentSkill | null;
  onClose: () => void;
  onSave: (skill: { name: string; description: string; content: string }) => Promise<void>;
}

function SkillModal({ initial, onClose, onSave }: SkillModalProps) {
  const [form, setForm] = useState(initial ?? EMPTY_SKILL);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const handleSave = async () => {
    if (!form.name.trim()) { setError("Name is required"); return; }
    if (!form.description.trim()) { setError("Description is required"); return; }
    setSaving(true);
    setError("");
    try {
      await onSave(form);
      onClose();
    } catch (e: any) {
      setError(e?.message || "Failed to save skill");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
      <div className="w-full max-w-2xl rounded-2xl border border-foreground/[0.08] bg-card shadow-2xl flex flex-col max-h-[90vh]">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-foreground/[0.06]">
          <div className="flex items-center gap-2">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-violet-500/10 border border-violet-500/20">
              <Sparkles className="h-4 w-4 text-violet-400" />
            </div>
            <h2 className="text-sm font-semibold">
              {initial ? "Skill bearbeiten" : "Neuer Skill"}
            </h2>
          </div>
          <button onClick={onClose} className="rounded-lg p-1.5 hover:bg-foreground/[0.06] text-muted-foreground hover:text-foreground transition-all">
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-6 space-y-4">
          {/* Name */}
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-muted-foreground">Name <span className="text-red-400">*</span></label>
            <input
              value={form.name}
              onChange={(e) => setForm(f => ({ ...f, name: e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, "-") }))}
              disabled={!!initial}
              placeholder="mein-skill"
              className="w-full rounded-xl border border-foreground/[0.08] bg-foreground/[0.02] px-3 py-2 text-sm outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-all placeholder:text-muted-foreground/30 disabled:opacity-50"
            />
            <p className="text-[10px] text-muted-foreground/50">Kleinbuchstaben und Bindestriche. Wird als Verzeichnisname verwendet.</p>
          </div>

          {/* Description */}
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-muted-foreground">Beschreibung <span className="text-red-400">*</span></label>
            <input
              value={form.description}
              onChange={(e) => setForm(f => ({ ...f, description: e.target.value }))}
              placeholder="Kurze Beschreibung was dieser Skill macht"
              className="w-full rounded-xl border border-foreground/[0.08] bg-foreground/[0.02] px-3 py-2 text-sm outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-all placeholder:text-muted-foreground/30"
            />
          </div>

          {/* Content */}
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-muted-foreground">Instruktionen (Markdown)</label>
            <textarea
              value={form.content}
              onChange={(e) => setForm(f => ({ ...f, content: e.target.value }))}
              rows={12}
              placeholder={`# ${form.name || "Mein Skill"}\n\nBeschreibe hier detailliert was der Agent tun soll wenn dieser Skill aktiv ist.\n\n## Wann verwenden\n- ...\n\n## Vorgehen\n1. ...\n2. ...`}
              className="w-full rounded-xl border border-foreground/[0.08] bg-foreground/[0.02] px-3 py-2 text-sm font-mono outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-all placeholder:text-muted-foreground/20 resize-none leading-relaxed"
            />
            <p className="text-[10px] text-muted-foreground/50">Markdown-Instruktionen für den Agenten. Der Skill wird als SKILL.md gespeichert.</p>
          </div>

          {error && (
            <p className="text-xs text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2">{error}</p>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-2 px-6 py-4 border-t border-foreground/[0.06]">
          <button
            onClick={onClose}
            className="rounded-xl px-4 py-2 text-sm text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04] transition-all"
          >
            Abbrechen
          </button>
          <button
            onClick={handleSave}
            disabled={saving}
            className="flex items-center gap-2 rounded-xl bg-primary/10 text-primary hover:bg-primary/20 border border-primary/20 px-4 py-2 text-sm font-medium transition-all disabled:opacity-50"
          >
            {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
            {saving ? "Speichern..." : "Speichern"}
          </button>
        </div>
      </div>
    </div>
  );
}

export default function SkillsPage() {
  const [catalog, setCatalog] = useState<CatalogSkill[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [selectedAgent, setSelectedAgent] = useState<Agent | null>(null);
  const [agentSkills, setAgentSkills] = useState<AgentSkill[]>([]);
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<"catalog" | "mine" | "pending">("catalog");
  const [pendingSkills, setPendingSkills] = useState<MarketplaceSkill[]>([]);
  const [reviewing, setReviewing] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [installing, setInstalling] = useState<string | null>(null);
  const [showAgentPicker, setShowAgentPicker] = useState(false);
  const [showModal, setShowModal] = useState(false);
  const [editSkill, setEditSkill] = useState<AgentSkill | null>(null);
  const [deletingSkill, setDeletingSkill] = useState<string | null>(null);
  const [detailSkill, setDetailSkill] = useState<MarketplaceSkill | null>(null);

  const refreshAgentSkills = useCallback(async (agent: Agent) => {
    const skills = await api.getAgentSkills(agent.id).catch(() => []);
    setAgentSkills(skills);
  }, []);

  useEffect(() => {
    const load = async () => {
      try {
        const [catalogData, agentsData, pendingData] = await Promise.all([
          api.getSkillCatalog(),
          api.getAgents(),
          api.getMarketplaceSkills({ status: "draft" }),
        ]);
        setCatalog(catalogData.skills || []);
        setPendingSkills(pendingData.skills || []);
        const online = agentsData.agents.filter((a) =>
          ["running", "idle", "working"].includes(a.state)
        );
        setAgents(online);
        if (online.length > 0) {
          setSelectedAgent(online[0]);
        }
      } catch {
        // ignore
      } finally {
        setLoading(false);
      }
    };
    load();
  }, []);

  useEffect(() => {
    if (!selectedAgent) return;
    refreshAgentSkills(selectedAgent);
  }, [selectedAgent, refreshAgentSkills]);

  const handleRefresh = async () => {
    setRefreshing(true);
    try {
      await api.refreshSkillCatalog();
      const data = await api.getSkillCatalog();
      setCatalog(data.skills || []);
    } catch {
      // ignore
    } finally {
      setRefreshing(false);
    }
  };

  const handleInstall = async (skill: CatalogSkill) => {
    if (!selectedAgent || installing) return;
    setInstalling(skill.name);
    try {
      if (skill.type === "db" && skill.id) {
        await api.assignDbSkill(skill.id, selectedAgent.id);
      } else {
        await api.installSkill(selectedAgent.id, skill.repo, skill.name);
      }
      await refreshAgentSkills(selectedAgent);
    } catch {
      // ignore
    } finally {
      setInstalling(null);
    }
  };

  const handleCreate = async (form: { name: string; description: string; content: string }) => {
    if (!selectedAgent) return;
    await api.createAgentSkill(selectedAgent.id, form);
    await refreshAgentSkills(selectedAgent);
  };

  const handleUpdate = async (form: { name: string; description: string; content: string }) => {
    if (!selectedAgent || !editSkill) return;
    await api.updateAgentSkill(selectedAgent.id, editSkill.name, form);
    await refreshAgentSkills(selectedAgent);
  };

  const handleDelete = async (skillName: string) => {
    if (!selectedAgent) return;
    setDeletingSkill(skillName);
    try {
      await api.deleteAgentSkill(selectedAgent.id, skillName);
      await refreshAgentSkills(selectedAgent);
    } catch {
      // ignore
    } finally {
      setDeletingSkill(null);
    }
  };

  const isInstalled = (name: string) => agentSkills.some((s) => s.name === name);

  const filtered = catalog.filter((s) => {
    const matchSearch = !search ||
      s.name.toLowerCase().includes(search.toLowerCase()) ||
      s.description.toLowerCase().includes(search.toLowerCase());
    const matchCategory = !category || s.category === category;
    return matchSearch && matchCategory;
  });

  const filteredMine = agentSkills.filter((s) =>
    !search || s.name.toLowerCase().includes(search.toLowerCase()) ||
    s.description.toLowerCase().includes(search.toLowerCase())
  );

  const categories = Array.from(new Set(catalog.map((s) => s.category)));

  return (
    <div className="min-h-screen">
      <Header title="Skills" subtitle="Browse, install, and create skills for your agents" />

      <div className="p-6 space-y-6">
        {/* Top bar */}
        <div className="flex items-center gap-3 flex-wrap">
          {/* Agent Selector */}
          <div className="relative">
            <button
              onClick={() => setShowAgentPicker(!showAgentPicker)}
              className="flex items-center gap-2 rounded-xl border border-foreground/[0.08] bg-card/80 px-3 py-2 text-sm hover:bg-accent/50 transition-all"
            >
              <Bot className="h-4 w-4 text-violet-400" />
              <span className="font-medium">{selectedAgent?.name || "Agent wählen"}</span>
              <ChevronDown className="h-3 w-3 text-muted-foreground" />
            </button>
            {showAgentPicker && (
              <>
                <div className="fixed inset-0 z-40" onClick={() => setShowAgentPicker(false)} />
                <div className="absolute top-full left-0 mt-1 z-50 w-56 rounded-xl border border-border bg-card shadow-2xl overflow-hidden">
                  <div className="p-1.5">
                    {agents.map((agent) => (
                      <button
                        key={agent.id}
                        onClick={() => { setSelectedAgent(agent); setShowAgentPicker(false); }}
                        className={cn(
                          "w-full flex items-center gap-2 rounded-lg px-3 py-2 text-sm text-left transition-all",
                          selectedAgent?.id === agent.id
                            ? "bg-primary/10 text-foreground"
                            : "text-muted-foreground hover:bg-accent/50"
                        )}
                      >
                        <Bot className="h-3.5 w-3.5 text-violet-400" />
                        {agent.name}
                      </button>
                    ))}
                  </div>
                </div>
              </>
            )}
          </div>

          {/* Search */}
          <div className="relative flex-1 max-w-md">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground/50" />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Skills durchsuchen..."
              className="w-full rounded-xl border border-foreground/[0.08] bg-foreground/[0.02] pl-10 pr-4 py-2 text-sm outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-all placeholder:text-muted-foreground/30"
            />
          </div>

          {/* Refresh */}
          {activeTab === "catalog" && (
            <button
              onClick={handleRefresh}
              disabled={refreshing}
              className="rounded-xl px-4 py-2 text-sm text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04] flex items-center gap-2"
            >
              <RefreshCw className={cn("w-4 h-4", refreshing && "animate-spin")} />
              Aktualisieren
            </button>
          )}

          {/* New Skill Button */}
          <button
            onClick={() => { setEditSkill(null); setShowModal(true); }}
            disabled={!selectedAgent}
            className="flex items-center gap-2 rounded-xl bg-violet-500/10 text-violet-400 hover:bg-violet-500/20 border border-violet-500/20 px-4 py-2 text-sm font-medium transition-all disabled:opacity-40"
          >
            <Plus className="h-4 w-4" />
            Neuer Skill
          </button>

          {/* Installed count */}
          <div className="flex items-center gap-1.5 text-xs text-muted-foreground/60">
            <Package className="h-3.5 w-3.5" />
            {agentSkills.length} installiert
          </div>
        </div>

        {/* Tabs */}
        <div className="flex gap-1 rounded-xl border border-foreground/[0.06] bg-foreground/[0.02] p-1 w-fit">
          {[
            { id: "catalog" as const, label: `Katalog (${catalog.length})` },
            { id: "mine" as const, label: `Meine Skills (${agentSkills.length})` },
            { id: "pending" as const, label: pendingSkills.length > 0 ? `✨ Ausstehend (${pendingSkills.length})` : "Ausstehend" },
          ].map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={cn(
                "rounded-lg px-4 py-1.5 text-xs font-medium transition-all",
                activeTab === tab.id
                  ? "bg-card text-foreground shadow-sm border border-foreground/[0.06]"
                  : "text-muted-foreground hover:text-foreground"
              )}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {/* Category filters (catalog only) */}
        {activeTab === "catalog" && (
          <div className="flex items-center gap-2 flex-wrap">
            <button
              onClick={() => setCategory(null)}
              className={cn(
                "rounded-full border px-3 py-1 text-[11px] font-medium transition-all",
                !category
                  ? "bg-foreground/[0.08] text-foreground border-foreground/[0.12]"
                  : "text-muted-foreground/60 border-foreground/[0.06] hover:text-foreground"
              )}
            >
              Alle ({catalog.length})
            </button>
            {categories.map((cat) => {
              const cfg = CATEGORY_CONFIG[cat] || CATEGORY_CONFIG.tools;
              const Icon = cfg.icon;
              const count = catalog.filter((s) => s.category === cat).length;
              return (
                <button
                  key={cat}
                  onClick={() => setCategory(category === cat ? null : cat)}
                  className={cn(
                    "inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-[11px] font-medium transition-all",
                    category === cat ? cfg.color : "text-muted-foreground/60 border-foreground/[0.06] hover:text-foreground"
                  )}
                >
                  <Icon className="h-3 w-3" />
                  {cfg.label} ({count})
                </button>
              );
            })}
          </div>
        )}

        {/* Content */}
        {loading ? (
          <div className="flex items-center justify-center py-16">
            <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
          </div>
        ) : activeTab === "catalog" ? (
          /* Catalog Grid */
          filtered.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
              <Sparkles className="h-8 w-8 mb-3" />
              <p className="text-sm">Keine Skills gefunden</p>
              <p className="text-xs mt-1 text-muted-foreground/60">
                {catalog.length === 0
                  ? "Skill-Katalog wird noch geladen..."
                  : "Versuche einen anderen Suchbegriff"}
              </p>
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
              {filtered.map((skill) => {
                const installed = isInstalled(skill.name);
                const isInstalling = installing === skill.name;
                const cfg = CATEGORY_CONFIG[skill.category] || CATEGORY_CONFIG.tools;
                const Icon = cfg.icon;
                return (
                  <div
                    key={`${skill.repo}-${skill.name}`}
                    className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-4 flex flex-col gap-3 cursor-pointer hover:border-foreground/[0.12] transition-all group"
                    onClick={() => {
                      if (skill.type === "db" && skill.id) {
                        setDetailSkill(skill as unknown as MarketplaceSkill);
                      }
                    }}
                  >
                    <div className="flex items-start justify-between">
                      <div className="flex items-center gap-2.5">
                        <div className={cn("flex h-8 w-8 items-center justify-center rounded-lg border", cfg.color)}>
                          <Icon className="h-4 w-4" />
                        </div>
                        <div>
                          <p className="text-sm font-semibold">{skill.name}</p>
                          <p className="text-[10px] text-muted-foreground/50">{skill.repo}</p>
                        </div>
                      </div>
                      <div className="flex items-center gap-1.5">
                        {skill.type === "db" && skill.id && (
                          <span className="text-[10px] text-muted-foreground/40 group-hover:text-muted-foreground/60 transition-colors">Details →</span>
                        )}
                        <span className={cn("inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-medium", cfg.color)}>
                          {cfg.label}
                        </span>
                      </div>
                    </div>

                    <p className="text-xs text-muted-foreground leading-relaxed flex-1">
                      {skill.description || "No description available"}
                    </p>

                    <button
                      onClick={(e) => { e.stopPropagation(); handleInstall(skill); }}
                      disabled={installed || isInstalling || !selectedAgent}
                      className={cn(
                        "w-full flex items-center justify-center gap-2 rounded-lg py-2 text-xs font-medium transition-all",
                        installed
                          ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20"
                          : "bg-primary/10 text-primary hover:bg-primary/20 border border-primary/20"
                      )}
                    >
                      {isInstalling ? (
                        <><Loader2 className="h-3.5 w-3.5 animate-spin" /> Installiere...</>
                      ) : installed ? (
                        <><CheckCircle2 className="h-3.5 w-3.5" /> Installiert</>
                      ) : (
                        <><Download className="h-3.5 w-3.5" /> Installieren</>
                      )}
                    </button>
                  </div>
                );
              })}
            </div>
          )
        ) : activeTab === "pending" ? (
          /* Pending (auto-generated) Skills */
          pendingSkills.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
              <p className="text-sm">Keine ausstehenden Skills</p>
              <p className="text-xs mt-1 text-muted-foreground/60">
                Der Trend-Scanner läuft täglich und sucht neue Tools automatisch.
              </p>
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {pendingSkills.map((skill) => (
                <div
                key={skill.id}
                className="rounded-xl border border-amber-500/20 bg-amber-500/5 p-4 flex flex-col gap-3 cursor-pointer hover:border-amber-500/30 transition-all"
                onClick={() => setDetailSkill(skill)}
              >
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-semibold truncate">{skill.name}</p>
                      <p className="text-xs text-muted-foreground mt-0.5 line-clamp-2">{skill.description}</p>
                      {skill.source_repo && (
                        <a
                          href={skill.source_url || `https://github.com/${skill.source_repo}`}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-[10px] text-amber-400/70 hover:text-amber-400 mt-1 inline-flex items-center gap-1 transition-colors"
                        >
                          📦 {skill.source_repo} ↗
                        </a>
                      )}
                    </div>
                    <span className="shrink-0 text-[10px] rounded-full border border-amber-500/30 bg-amber-500/10 text-amber-400 px-2 py-0.5">
                      Auto-generiert
                    </span>
                  </div>
                  <div className="text-xs text-foreground/70 bg-foreground/[0.03] rounded-lg p-3 font-mono whitespace-pre-wrap line-clamp-6 border border-foreground/[0.05]">
                    {skill.content?.slice(0, 400)}
                    {(skill.content?.length ?? 0) > 400 && "…"}
                  </div>
                  <div className="flex gap-2 justify-end" onClick={(e) => e.stopPropagation()}>
                    <button
                      onClick={async () => {
                        setReviewing(skill.id);
                        try {
                          await api.rejectSkill(skill.id);
                          setPendingSkills(p => p.filter(s => s.id !== skill.id));
                        } finally { setReviewing(null); }
                      }}
                      disabled={reviewing === skill.id}
                      className="flex items-center gap-1.5 rounded-lg bg-red-500/10 px-3 py-1.5 text-xs font-medium text-red-400 hover:bg-red-500/20 disabled:opacity-50 transition-colors"
                    >
                      {reviewing === skill.id ? <Loader2 className="h-3 w-3 animate-spin" /> : "✕"} Ablehnen
                    </button>
                    <button
                      onClick={async () => {
                        setReviewing(skill.id);
                        try {
                          await api.approveSkill(skill.id);
                          setPendingSkills(p => p.filter(s => s.id !== skill.id));
                          setCatalog(c => [...c, { ...skill, status: "active" } as any]);
                        } finally { setReviewing(null); }
                      }}
                      disabled={reviewing === skill.id}
                      className="flex items-center gap-1.5 rounded-lg bg-emerald-500/10 px-3 py-1.5 text-xs font-medium text-emerald-400 hover:bg-emerald-500/20 disabled:opacity-50 transition-colors"
                    >
                      {reviewing === skill.id ? <Loader2 className="h-3 w-3 animate-spin" /> : "✓"} Freigeben
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )
        ) : (
          /* My Skills */
          filteredMine.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
              <Package className="h-8 w-8 mb-3" />
              <p className="text-sm">Keine eigenen Skills</p>
              <p className="text-xs mt-1 text-muted-foreground/60">
                {!selectedAgent
                  ? "Wähle zuerst einen Agenten"
                  : "Klicke auf \"Neuer Skill\" um einen zu erstellen"}
              </p>
              <button
                onClick={() => { setEditSkill(null); setShowModal(true); }}
                disabled={!selectedAgent}
                className="mt-4 flex items-center gap-2 rounded-xl bg-violet-500/10 text-violet-400 hover:bg-violet-500/20 border border-violet-500/20 px-4 py-2 text-sm font-medium transition-all disabled:opacity-40"
              >
                <Plus className="h-4 w-4" />
                Neuer Skill erstellen
              </button>
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
              {filteredMine.map((skill) => (
                <div
                  key={skill.name}
                  className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-4 flex flex-col gap-3"
                >
                  <div className="flex items-start justify-between">
                    <div className="flex items-center gap-2.5">
                      <div className="flex h-8 w-8 items-center justify-center rounded-lg border bg-violet-500/10 text-violet-400 border-violet-500/20">
                        <Sparkles className="h-4 w-4" />
                      </div>
                      <div>
                        <p className="text-sm font-semibold">{skill.name}</p>
                        <p className="text-[10px] text-muted-foreground/50">Eigener Skill</p>
                      </div>
                    </div>
                  </div>

                  <p className="text-xs text-muted-foreground leading-relaxed flex-1">
                    {skill.description || "Keine Beschreibung"}
                  </p>

                  <div className="flex gap-2">
                    <button
                      onClick={() => { setEditSkill(skill); setShowModal(true); }}
                      className="flex-1 flex items-center justify-center gap-1.5 rounded-lg py-2 text-xs font-medium bg-foreground/[0.04] hover:bg-foreground/[0.08] text-muted-foreground hover:text-foreground border border-foreground/[0.06] transition-all"
                    >
                      <Pencil className="h-3 w-3" />
                      Bearbeiten
                    </button>
                    <button
                      onClick={() => setDetailSkill(skill as unknown as MarketplaceSkill)}
                      className="flex items-center justify-center gap-1.5 rounded-lg px-3 py-2 text-xs font-medium bg-foreground/[0.04] hover:bg-foreground/[0.08] text-muted-foreground hover:text-foreground border border-foreground/[0.06] transition-all"
                      title="Dateien & Details"
                    >
                      <Paperclip className="h-3 w-3" />
                    </button>
                    <button
                      onClick={() => handleDelete(skill.name)}
                      disabled={deletingSkill === skill.name}
                      className="flex items-center justify-center gap-1.5 rounded-lg px-3 py-2 text-xs font-medium bg-red-500/10 hover:bg-red-500/20 text-red-400 border border-red-500/20 transition-all disabled:opacity-50"
                    >
                      {deletingSkill === skill.name
                        ? <Loader2 className="h-3 w-3 animate-spin" />
                        : <Trash2 className="h-3 w-3" />}
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )
        )}
      </div>

      {/* Create / Edit Modal */}
      {showModal && (
        <SkillModal
          initial={editSkill}
          onClose={() => { setShowModal(false); setEditSkill(null); }}
          onSave={editSkill ? handleUpdate : handleCreate}
        />
      )}

      {/* Skill Detail + File Attachments Modal */}
      {detailSkill && (
        <SkillDetailModal
          skill={detailSkill}
          onClose={() => setDetailSkill(null)}
        />
      )}
    </div>
  );
}

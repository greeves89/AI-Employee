"use client";

import { useState, useEffect, useCallback } from "react";
import {
  Sparkles, Search, Download, Loader2, RefreshCw,
  Package, Code2, Palette, Megaphone, FileText, Wrench,
  CheckCircle2, Bot, ChevronDown, Plus, Pencil, Trash2, X, Save,
} from "lucide-react";
import { Header } from "@/components/layout/header";
import { cn } from "@/lib/utils";
import * as api from "@/lib/api";
import type { CatalogSkill, AgentSkill } from "@/lib/api";
import type { Agent } from "@/lib/types";

const CATEGORY_CONFIG: Record<string, { label: string; icon: typeof Code2; color: string }> = {
  dev: { label: "Development", icon: Code2, color: "bg-blue-500/10 text-blue-400 border-blue-500/20" },
  design: { label: "Design", icon: Palette, color: "bg-pink-500/10 text-pink-400 border-pink-500/20" },
  marketing: { label: "Marketing", icon: Megaphone, color: "bg-orange-500/10 text-orange-400 border-orange-500/20" },
  docs: { label: "Documents", icon: FileText, color: "bg-purple-500/10 text-purple-400 border-purple-500/20" },
  tools: { label: "Tools", icon: Wrench, color: "bg-amber-500/10 text-amber-400 border-amber-500/20" },
  core: { label: "Core", icon: Sparkles, color: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20" },
};

const EMPTY_SKILL = { name: "", description: "", content: "" };

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
  const [activeTab, setActiveTab] = useState<"catalog" | "mine">("catalog");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [installing, setInstalling] = useState<string | null>(null);
  const [showAgentPicker, setShowAgentPicker] = useState(false);
  const [showModal, setShowModal] = useState(false);
  const [editSkill, setEditSkill] = useState<AgentSkill | null>(null);
  const [deletingSkill, setDeletingSkill] = useState<string | null>(null);

  const refreshAgentSkills = useCallback(async (agent: Agent) => {
    const skills = await api.getAgentSkills(agent.id).catch(() => []);
    setAgentSkills(skills);
  }, []);

  useEffect(() => {
    const load = async () => {
      try {
        const [catalogData, agentsData] = await Promise.all([
          api.getSkillCatalog(),
          api.getAgents(),
        ]);
        setCatalog(catalogData.skills || []);
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
                    className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-4 flex flex-col gap-3"
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
                      <span className={cn("inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-medium", cfg.color)}>
                        {cfg.label}
                      </span>
                    </div>

                    <p className="text-xs text-muted-foreground leading-relaxed flex-1">
                      {skill.description || "No description available"}
                    </p>

                    <button
                      onClick={() => handleInstall(skill)}
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
    </div>
  );
}

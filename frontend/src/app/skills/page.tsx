"use client";

import { useState, useEffect, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Sparkles, Search, Loader2, Plus, X, Trash2, Star, Check,
  CheckCircle2, XCircle, Eye, BookOpen, Workflow, Lightbulb,
  FileText, Wrench, ChevronDown, Users,
} from "lucide-react";
import { Header } from "@/components/layout/header";
import { useAgents } from "@/hooks/use-agents";
import { cn } from "@/lib/utils";
import * as api from "@/lib/api";

const CATEGORY_CONFIG: Record<string, { label: string; icon: typeof Sparkles; color: string }> = {
  routine: { label: "Routine", icon: Workflow, color: "bg-blue-500/10 text-blue-400 border-blue-500/20" },
  template: { label: "Template", icon: FileText, color: "bg-purple-500/10 text-purple-400 border-purple-500/20" },
  workflow: { label: "Workflow", icon: Workflow, color: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20" },
  pattern: { label: "Pattern", icon: Lightbulb, color: "bg-amber-500/10 text-amber-400 border-amber-500/20" },
  recipe: { label: "Rezept", icon: BookOpen, color: "bg-pink-500/10 text-pink-400 border-pink-500/20" },
  tool: { label: "Tool", icon: Wrench, color: "bg-zinc-500/10 text-zinc-400 border-zinc-500/20" },
};

const containerVariants = {
  hidden: { opacity: 0 },
  visible: { opacity: 1, transition: { staggerChildren: 0.04 } },
};
const itemVariants = {
  hidden: { opacity: 0, y: 10 },
  visible: { opacity: 1, y: 0, transition: { duration: 0.3, ease: [0.25, 0.46, 0.45, 0.94] as const } },
};

type TabKey = "active" | "drafts" | "all";

export default function SkillsMarketplace() {
  const [skills, setSkills] = useState<api.MarketplaceSkill[]>([]);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<TabKey>("active");
  const [search, setSearch] = useState("");
  const [categoryFilter, setCategoryFilter] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [creating, setCreating] = useState(false);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [assignAgent, setAssignAgent] = useState<number | null>(null); // skill id being assigned
  const { agents } = useAgents();

  // Create form
  const [newName, setNewName] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [newContent, setNewContent] = useState("");
  const [newCategory, setNewCategory] = useState("routine");

  const refresh = useCallback(async () => {
    try {
      const status = tab === "drafts" ? "draft" : tab === "active" ? "active" : undefined;
      const data = await api.getMarketplaceSkills({
        status, q: search || undefined, category: categoryFilter || undefined,
      });
      setSkills(data.skills);
    } catch (e) {
      console.error("Failed to load skills:", e);
    } finally {
      setLoading(false);
    }
  }, [tab, search, categoryFilter]);

  useEffect(() => { refresh(); }, [refresh]);

  const handleCreate = async () => {
    if (!newName.trim() || !newContent.trim()) return;
    setCreating(true);
    try {
      await api.createMarketplaceSkill({
        name: newName.trim().toLowerCase().replace(/\s+/g, "-"),
        description: newDesc.trim(),
        content: newContent.trim(),
        category: newCategory,
      });
      setNewName(""); setNewDesc(""); setNewContent(""); setNewCategory("routine");
      setShowCreate(false);
      await refresh();
    } finally { setCreating(false); }
  };

  const handleDelete = async (id: number) => {
    await api.deleteMarketplaceSkill(id);
    await refresh();
  };

  const handleApprove = async (id: number) => {
    await api.approveSkill(id);
    await refresh();
  };

  const handleReject = async (id: number) => {
    await api.rejectSkill(id);
    await refresh();
  };

  const handleAssign = async (skillId: number, agentId: string) => {
    await api.assignSkill(skillId, agentId);
    setAssignAgent(null);
    await refresh();
  };

  const handleUnassign = async (skillId: number, agentId: string) => {
    await api.unassignSkill(skillId, agentId);
    await refresh();
  };

  const draftCount = skills.filter((s) => s.status === "draft").length;

  const stars = (rating: number | null) => {
    if (!rating) return null;
    const full = Math.round(rating);
    return (
      <span className="inline-flex gap-0.5">
        {[1, 2, 3, 4, 5].map((i) => (
          <Star key={i} className={cn("h-3 w-3", i <= full ? "text-amber-400 fill-amber-400" : "text-zinc-600")} />
        ))}
      </span>
    );
  };

  return (
    <div className="px-8 py-8 max-w-6xl mx-auto">
      <Header title="Skill Marketplace" subtitle="Routinen, Templates und Workflows f\u00fcr deine Agents" />

      <div className="space-y-6 mt-6">
        {/* Top bar */}
        <div className="flex items-center gap-4">
          <div className="flex-1 relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground/40" />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Skills durchsuchen..."
              className="w-full rounded-xl border border-foreground/[0.08] bg-foreground/[0.02] pl-10 pr-4 py-2.5 text-sm"
            />
          </div>
          <select
            value={categoryFilter || ""}
            onChange={(e) => setCategoryFilter(e.target.value || null)}
            className="rounded-xl border border-foreground/[0.08] bg-foreground/[0.02] px-4 py-2.5 text-sm"
          >
            <option value="">Alle Kategorien</option>
            {Object.entries(CATEGORY_CONFIG).map(([k, v]) => (
              <option key={k} value={k}>{v.label}</option>
            ))}
          </select>
          <button
            onClick={() => setShowCreate(!showCreate)}
            className="flex items-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground shadow-lg shadow-primary/20"
          >
            {showCreate ? <X className="h-4 w-4" /> : <Plus className="h-4 w-4" />}
            {showCreate ? "Abbrechen" : "Neuer Skill"}
          </button>
        </div>

        {/* Tabs */}
        <div className="flex gap-1 p-1 rounded-xl bg-foreground/[0.03] border border-foreground/[0.06] w-fit">
          {([
            { key: "active" as TabKey, label: "Aktiv" },
            { key: "drafts" as TabKey, label: `Vorschl\u00e4ge${draftCount > 0 ? ` (${draftCount})` : ""}` },
            { key: "all" as TabKey, label: "Alle" },
          ]).map((t) => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={cn(
                "rounded-lg px-4 py-2 text-xs font-medium transition-all",
                tab === t.key
                  ? "bg-foreground/[0.08] text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground"
              )}
            >
              {t.label}
            </button>
          ))}
        </div>

        {/* Create form */}
        <AnimatePresence>
          {showCreate && (
            <motion.div
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: "auto" }}
              exit={{ opacity: 0, height: 0 }}
              className="overflow-hidden"
            >
              <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-5 space-y-4">
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="text-[11px] font-medium text-muted-foreground/70">Name</label>
                    <input value={newName} onChange={(e) => setNewName(e.target.value)}
                      placeholder="z.B. pr-review-workflow"
                      className="mt-1 w-full rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3.5 py-2.5 text-sm" />
                  </div>
                  <div>
                    <label className="text-[11px] font-medium text-muted-foreground/70">Kategorie</label>
                    <select value={newCategory} onChange={(e) => setNewCategory(e.target.value)}
                      className="mt-1 w-full rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3.5 py-2.5 text-sm">
                      {Object.entries(CATEGORY_CONFIG).map(([k, v]) => (
                        <option key={k} value={k}>{v.label}</option>
                      ))}
                    </select>
                  </div>
                </div>
                <div>
                  <label className="text-[11px] font-medium text-muted-foreground/70">Beschreibung</label>
                  <input value={newDesc} onChange={(e) => setNewDesc(e.target.value)}
                    placeholder="Eine Zeile: was macht der Skill?"
                    className="mt-1 w-full rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3.5 py-2.5 text-sm" />
                </div>
                <div>
                  <label className="text-[11px] font-medium text-muted-foreground/70">Anweisungen (Markdown)</label>
                  <textarea value={newContent} onChange={(e) => setNewContent(e.target.value)}
                    rows={6}
                    placeholder={"## Schritte\n\n1. ...\n2. ...\n\n## Beispiel\n\n..."}
                    className="mt-1 w-full rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3.5 py-2.5 text-sm font-mono" />
                </div>
                <div className="flex justify-end">
                  <button onClick={handleCreate}
                    disabled={creating || !newName.trim() || !newContent.trim()}
                    className="flex items-center gap-2 rounded-xl bg-primary px-5 py-2.5 text-sm font-medium text-primary-foreground shadow-lg shadow-primary/20 disabled:opacity-40">
                    {creating && <Loader2 className="h-4 w-4 animate-spin" />}
                    Skill erstellen
                  </button>
                </div>
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Skill list */}
        {loading ? (
          <div className="flex items-center justify-center py-20">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        ) : skills.length === 0 ? (
          <div className="text-center py-20 text-muted-foreground">
            <Sparkles className="h-10 w-10 mx-auto mb-3 opacity-30" />
            <p className="text-sm">Keine Skills gefunden</p>
          </div>
        ) : (
          <motion.div variants={containerVariants} initial="hidden" animate="visible" className="space-y-3">
            {skills.map((skill) => {
              const cat = CATEGORY_CONFIG[skill.category] || CATEGORY_CONFIG.tool;
              const CatIcon = cat.icon;
              const isDraft = skill.status === "draft";
              const isExpanded = expandedId === skill.id;
              const isAssigning = assignAgent === skill.id;

              return (
                <motion.div key={skill.id} variants={itemVariants}
                  className={cn(
                    "rounded-xl border bg-card/80 backdrop-blur-sm p-5 transition-all",
                    isDraft ? "border-amber-500/20" : "border-foreground/[0.06]"
                  )}>
                  <div className="flex items-start justify-between gap-4">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <h3 className="text-sm font-medium">{skill.name}</h3>
                        <span className={cn("inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-medium", cat.color)}>
                          <CatIcon className="h-3 w-3 mr-1" />{cat.label}
                        </span>
                        {isDraft && (
                          <span className="inline-flex items-center rounded-full border border-amber-500/20 bg-amber-500/10 px-2 py-0.5 text-[10px] font-medium text-amber-400">
                            Vorschlag
                          </span>
                        )}
                        {stars(skill.avg_rating)}
                      </div>
                      <p className="text-xs text-muted-foreground/70 mt-1">{skill.description}</p>
                      <div className="mt-2 flex items-center gap-3 text-[11px] text-muted-foreground/50">
                        <span>{skill.usage_count}x genutzt</span>
                        <span>von: {skill.created_by}</span>
                        {skill.assigned_agents.length > 0 && (
                          <span className="flex items-center gap-1">
                            <Users className="h-3 w-3" />{skill.assigned_agents.length} Agents
                          </span>
                        )}
                      </div>
                    </div>

                    <div className="flex items-center gap-1 shrink-0">
                      {isDraft && (
                        <>
                          <button onClick={() => handleApprove(skill.id)}
                            className="rounded-lg p-2 text-emerald-400 hover:bg-emerald-500/10 transition-colors"
                            title="Freigeben">
                            <CheckCircle2 className="h-4 w-4" />
                          </button>
                          <button onClick={() => handleReject(skill.id)}
                            className="rounded-lg p-2 text-red-400 hover:bg-red-500/10 transition-colors"
                            title="Ablehnen">
                            <XCircle className="h-4 w-4" />
                          </button>
                        </>
                      )}
                      <button onClick={() => setAssignAgent(isAssigning ? null : skill.id)}
                        className="rounded-lg p-2 text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04] transition-colors"
                        title="Agent zuweisen">
                        <Users className="h-4 w-4" />
                      </button>
                      <button onClick={() => setExpandedId(isExpanded ? null : skill.id)}
                        className="rounded-lg p-2 text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04] transition-colors"
                        title="Details">
                        <Eye className="h-4 w-4" />
                      </button>
                      <button onClick={() => handleDelete(skill.id)}
                        className="rounded-lg p-2 text-muted-foreground hover:text-red-400 hover:bg-red-500/10 transition-colors">
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </div>
                  </div>

                  {/* Assign panel */}
                  <AnimatePresence>
                    {isAssigning && (
                      <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }} className="overflow-hidden">
                        <div className="mt-3 pt-3 border-t border-foreground/[0.06] flex flex-wrap gap-2">
                          {agents.map((a) => {
                            const isAssigned = skill.assigned_agents.includes(a.id);
                            return (
                              <button key={a.id}
                                onClick={() => isAssigned ? handleUnassign(skill.id, a.id) : handleAssign(skill.id, a.id)}
                                className={cn(
                                  "inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-[11px] font-medium transition-colors",
                                  isAssigned
                                    ? "bg-emerald-500/10 border-emerald-500/20 text-emerald-400"
                                    : "border-foreground/[0.08] text-muted-foreground hover:border-primary/30 hover:text-foreground"
                                )}>
                                {isAssigned && <Check className="h-3 w-3" />}
                                {a.name}
                              </button>
                            );
                          })}
                        </div>
                      </motion.div>
                    )}
                  </AnimatePresence>

                  {/* Content preview */}
                  <AnimatePresence>
                    {isExpanded && (
                      <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }} className="overflow-hidden">
                        <div className="mt-3 pt-3 border-t border-foreground/[0.06]">
                          <pre className="text-xs text-muted-foreground/80 font-mono whitespace-pre-wrap leading-relaxed max-h-80 overflow-y-auto">
                            {skill.content}
                          </pre>
                        </div>
                      </motion.div>
                    )}
                  </AnimatePresence>
                </motion.div>
              );
            })}
          </motion.div>
        )}
      </div>
    </div>
  );
}

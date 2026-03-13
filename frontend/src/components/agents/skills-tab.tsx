"use client";

import { useCallback, useEffect, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Sparkles,
  Plus,
  Pencil,
  Trash2,
  ChevronDown,
  Code2,
  RefreshCw,
  Save,
  X,
  Download,
  Github,
  Loader2,
  ExternalLink,
  Search,
  Store,
  Check,
} from "lucide-react";
import { cn } from "@/lib/utils";

const API = process.env.NEXT_PUBLIC_API_URL || "";

interface Skill {
  name: string;
  description: string;
  content: string;
}

interface CatalogSkill {
  name: string;
  repo: string;
  description: string;
  installs?: string;
  category: string;
}

// Fallback catalog used when the API hasn't crawled yet
const FALLBACK_CATALOG: CatalogSkill[] = [
  { name: "find-skills", repo: "vercel-labs/skills", description: "Discover and install new skills from the community", installs: "", category: "core" },
  { name: "ui-ux-pro-max", repo: "nextlevelbuilder/ui-ux-pro-max-skill", description: "UI/UX design intelligence with 50+ styles, color palettes, fonts", installs: "", category: "design" },
  { name: "frontend-design", repo: "anthropics/skills", description: "Guidance for visual interface development", installs: "", category: "design" },
  { name: "skill-creator", repo: "anthropics/skills", description: "Create new custom skills", installs: "", category: "core" },
];

const CATALOG_CATEGORIES: Record<string, string> = {
  all: "All",
  core: "Core",
  dev: "Development",
  design: "Design",
  marketing: "Marketing",
  docs: "Documents",
  tools: "Tools",
};

const CATEGORY_COLORS: Record<string, string> = {
  core: "bg-violet-500/10 text-violet-400 border-violet-500/20",
  dev: "bg-blue-500/10 text-blue-400 border-blue-500/20",
  design: "bg-pink-500/10 text-pink-400 border-pink-500/20",
  marketing: "bg-orange-500/10 text-orange-400 border-orange-500/20",
  docs: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
  tools: "bg-cyan-500/10 text-cyan-400 border-cyan-500/20",
};

interface SkillsTabProps {
  agentId: string;
}

export function SkillsTab({ agentId }: SkillsTabProps) {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [loading, setLoading] = useState(true);
  const [expandedSkill, setExpandedSkill] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [editingSkill, setEditingSkill] = useState<string | null>(null);
  const [formName, setFormName] = useState("");
  const [formDescription, setFormDescription] = useState("");
  const [formContent, setFormContent] = useState("");
  const [saving, setSaving] = useState(false);
  const [showInstall, setShowInstall] = useState(false);
  const [installRepo, setInstallRepo] = useState("");
  const [installSkill, setInstallSkill] = useState("");
  const [installing, setInstalling] = useState(false);
  const [installError, setInstallError] = useState("");
  const [showCatalog, setShowCatalog] = useState(false);
  const [catalogFilter, setCatalogFilter] = useState("all");
  const [catalogSearch, setCatalogSearch] = useState("");
  const [installingSkill, setInstallingSkill] = useState<string | null>(null);
  const [catalog, setCatalog] = useState<CatalogSkill[]>(FALLBACK_CATALOG);
  const [catalogLoading, setCatalogLoading] = useState(false);
  const [catalogCrawledAt, setCatalogCrawledAt] = useState<string | null>(null);

  const installedNames = skills.map((s) => s.name);

  // Fetch catalog from API when store is opened
  const fetchCatalog = useCallback(async () => {
    setCatalogLoading(true);
    try {
      const res = await fetch(`${API}/api/v1/skills/catalog`, {
        credentials: "include",
      });
      if (res.ok) {
        const data = await res.json();
        if (data.skills && data.skills.length > 0) {
          setCatalog(data.skills);
          setCatalogCrawledAt(data.crawled_at || null);
        }
      }
    } catch {
      // Use fallback
    }
    setCatalogLoading(false);
  }, []);

  const handleInstallFromCatalog = async (cat: CatalogSkill) => {
    setInstallingSkill(cat.name);
    setInstallError("");
    try {
      const res = await fetch(`${API}/api/v1/agents/${agentId}/skills/install`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ repo: cat.repo, skill: cat.name }),
      });
      if (res.ok) {
        fetchSkills();
      } else {
        const data = await res.json().catch(() => ({ detail: "Install failed" }));
        setInstallError(`${cat.name}: ${data.detail || "Install failed"}`);
      }
    } catch {
      setInstallError(`${cat.name}: Network error`);
    }
    setInstallingSkill(null);
  };

  // Fetch catalog when store is opened
  useEffect(() => {
    if (showCatalog) {
      fetchCatalog();
    }
  }, [showCatalog, fetchCatalog]);

  // Build dynamic category list from catalog data
  const dynamicCategories: Record<string, string> = { all: "All" };
  for (const s of catalog) {
    if (s.category && !dynamicCategories[s.category]) {
      dynamicCategories[s.category] = CATALOG_CATEGORIES[s.category] || s.category.charAt(0).toUpperCase() + s.category.slice(1);
    }
  }

  const filteredCatalog = catalog.filter((s) => {
    if (catalogFilter !== "all" && s.category !== catalogFilter) return false;
    if (catalogSearch && !s.name.includes(catalogSearch.toLowerCase()) && !s.description.toLowerCase().includes(catalogSearch.toLowerCase())) return false;
    return true;
  });

  const handleInstallFromRepo = async () => {
    if (!installRepo.trim() || !installSkill.trim()) return;
    setInstalling(true);
    setInstallError("");
    try {
      const res = await fetch(`${API}/api/v1/agents/${agentId}/skills/install`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ repo: installRepo.trim(), skill: installSkill.trim() }),
      });
      if (res.ok) {
        setShowInstall(false);
        setInstallRepo("");
        setInstallSkill("");
        fetchSkills();
      } else {
        const data = await res.json().catch(() => ({ detail: "Install failed" }));
        setInstallError(data.detail || "Install failed");
      }
    } catch {
      setInstallError("Network error");
    }
    setInstalling(false);
  };

  const fetchSkills = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch(`${API}/api/v1/agents/${agentId}/skills`, {
        credentials: "include",
      });
      if (res.ok) {
        const data = await res.json();
        setSkills(data);
      }
    } catch {
      // ignore
    }
    setLoading(false);
  }, [agentId]);

  useEffect(() => {
    fetchSkills();
  }, [fetchSkills]);

  const resetForm = () => {
    setFormName("");
    setFormDescription("");
    setFormContent("");
    setShowForm(false);
    setEditingSkill(null);
  };

  const startEdit = (skill: Skill) => {
    setEditingSkill(skill.name);
    setFormName(skill.name);
    setFormDescription(skill.description);
    setFormContent(skill.content);
    setShowForm(true);
  };

  const handleSave = async () => {
    if (!formName.trim() || !formDescription.trim()) return;
    setSaving(true);
    try {
      const payload = {
        name: formName.trim().toLowerCase().replace(/\s+/g, "-"),
        description: formDescription.trim(),
        content: formContent.trim(),
      };

      const isEdit = editingSkill !== null;
      const url = isEdit
        ? `${API}/api/v1/agents/${agentId}/skills/${editingSkill}`
        : `${API}/api/v1/agents/${agentId}/skills`;

      const res = await fetch(url, {
        method: isEdit ? "PUT" : "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify(payload),
      });

      if (res.ok) {
        resetForm();
        fetchSkills();
      }
    } catch {
      // ignore
    }
    setSaving(false);
  };

  const handleDelete = async (name: string) => {
    try {
      const res = await fetch(
        `${API}/api/v1/agents/${agentId}/skills/${name}`,
        { method: "DELETE", credentials: "include" }
      );
      if (res.ok) {
        setSkills((prev) => prev.filter((s) => s.name !== name));
        if (editingSkill === name) resetForm();
      }
    } catch {
      // ignore
    }
  };

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Sparkles className="h-5 w-5 text-amber-400" />
          <h2 className="text-sm font-semibold">Skills</h2>
          <span className="text-xs text-muted-foreground">
            ({skills.length} {skills.length === 1 ? "skill" : "skills"})
          </span>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={fetchSkills}
            className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs text-muted-foreground hover:bg-accent hover:text-foreground transition-colors"
          >
            <RefreshCw className={cn("h-3 w-3", loading && "animate-spin")} />
            Refresh
          </button>
          <button
            onClick={() => {
              setShowInstall(!showInstall);
              setShowCatalog(false);
              resetForm();
            }}
            className="inline-flex items-center gap-1.5 rounded-xl px-3 py-2 text-xs font-medium border border-foreground/[0.08] text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04] transition-colors"
          >
            <Github className="h-3.5 w-3.5" />
            Custom URL
          </button>
          <button
            onClick={() => {
              setShowCatalog(!showCatalog);
              setShowInstall(false);
              resetForm();
            }}
            className="inline-flex items-center gap-1.5 rounded-xl px-4 py-2.5 text-sm font-medium border border-amber-500/20 bg-amber-500/10 text-amber-400 hover:bg-amber-500/20 transition-colors"
          >
            <Store className="h-3.5 w-3.5" />
            Skill Store
          </button>
          <button
            onClick={() => {
              resetForm();
              setShowForm(true);
              setShowInstall(false);
              setShowCatalog(false);
            }}
            className="rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground shadow-lg shadow-primary/20 inline-flex items-center gap-1.5"
          >
            <Plus className="h-3.5 w-3.5" />
            Add Skill
          </button>
        </div>
      </div>

      {/* Description */}
      <p className="text-xs text-muted-foreground/70">
        Skills are custom instructions stored as{" "}
        <code className="text-[11px] px-1 py-0.5 rounded bg-foreground/[0.04] font-mono">
          .claude/skills/&lt;name&gt;/SKILL.md
        </code>{" "}
        files. The agent can invoke them as slash commands.
      </p>

      {/* Install from GitHub Form */}
      <AnimatePresence>
        {showInstall && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            className="overflow-hidden"
          >
            <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-5 space-y-4">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Github className="h-4 w-4 text-muted-foreground" />
                  <h3 className="text-sm font-medium">Install Skill from GitHub</h3>
                </div>
                <button
                  onClick={() => { setShowInstall(false); setInstallError(""); }}
                  className="p-1 rounded-lg text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <div>
                  <label className="text-[11px] font-medium text-muted-foreground/70 block mb-1.5">
                    GitHub Repository URL
                  </label>
                  <input
                    type="text"
                    value={installRepo}
                    onChange={(e) => setInstallRepo(e.target.value)}
                    placeholder="https://github.com/vercel-labs/skills"
                    className="w-full rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3.5 py-2.5 text-sm placeholder:text-muted-foreground/40 focus:outline-none focus:ring-1 focus:ring-primary"
                  />
                </div>
                <div>
                  <label className="text-[11px] font-medium text-muted-foreground/70 block mb-1.5">
                    Skill Name
                  </label>
                  <input
                    type="text"
                    value={installSkill}
                    onChange={(e) => setInstallSkill(e.target.value)}
                    placeholder="e.g. find-skills"
                    className="w-full rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3.5 py-2.5 text-sm placeholder:text-muted-foreground/40 focus:outline-none focus:ring-1 focus:ring-primary"
                  />
                </div>
              </div>

              {/* Quick-install suggestions */}
              <div>
                <label className="text-[11px] font-medium text-muted-foreground/70 block mb-2">
                  Popular Skills
                </label>
                <div className="flex flex-wrap gap-2">
                  {[
                    { repo: "https://github.com/vercel-labs/skills", skill: "find-skills", label: "Skill Finder" },
                    { repo: "https://github.com/nextlevelbuilder/ui-ux-pro-max-skill", skill: "ui-ux-pro-max", label: "UI/UX Pro Max" },
                  ].map((s) => (
                    <button
                      key={s.skill}
                      onClick={() => { setInstallRepo(s.repo); setInstallSkill(s.skill); }}
                      className={cn(
                        "inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs font-medium transition-colors",
                        installSkill === s.skill
                          ? "bg-primary/10 text-primary border-primary/20"
                          : "border-foreground/[0.08] text-muted-foreground hover:text-foreground hover:border-foreground/[0.15]"
                      )}
                    >
                      <Sparkles className="h-3 w-3" />
                      {s.label}
                    </button>
                  ))}
                </div>
              </div>

              {installError && (
                <p className="text-xs text-red-400">{installError}</p>
              )}

              <div className="flex items-center justify-end gap-2">
                <button
                  onClick={() => { setShowInstall(false); setInstallError(""); }}
                  className="rounded-xl px-4 py-2 text-sm text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04]"
                >
                  Cancel
                </button>
                <button
                  onClick={handleInstallFromRepo}
                  disabled={installing || !installRepo.trim() || !installSkill.trim()}
                  className="rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground shadow-lg shadow-primary/20 inline-flex items-center gap-1.5 disabled:opacity-50"
                >
                  {installing ? (
                    <>
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      Installing...
                    </>
                  ) : (
                    <>
                      <Download className="h-3.5 w-3.5" />
                      Install
                    </>
                  )}
                </button>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Skill Store Catalog */}
      <AnimatePresence>
        {showCatalog && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            className="overflow-hidden"
          >
            <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-5 space-y-4">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Store className="h-4 w-4 text-amber-400" />
                  <h3 className="text-sm font-medium">Skill Store</h3>
                  <span className="text-[11px] text-muted-foreground/60">from skills.sh</span>
                  <span className="inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-medium bg-emerald-500/10 text-emerald-400 border-emerald-500/20">
                    {catalog.length} skills
                  </span>
                  {catalogCrawledAt && (
                    <span className="text-[10px] text-muted-foreground/40">
                      updated {new Date(catalogCrawledAt).toLocaleDateString()}
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-1">
                  <button
                    onClick={fetchCatalog}
                    disabled={catalogLoading}
                    className="p-1.5 rounded-lg text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
                    title="Refresh catalog"
                  >
                    <RefreshCw className={cn("h-3.5 w-3.5", catalogLoading && "animate-spin")} />
                  </button>
                  <button
                    onClick={() => setShowCatalog(false)}
                    className="p-1 rounded-lg text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
                  >
                    <X className="h-4 w-4" />
                  </button>
                </div>
              </div>

              {/* Search + Category filter */}
              <div className="flex flex-col sm:flex-row gap-3">
                <div className="relative flex-1">
                  <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground/50" />
                  <input
                    type="text"
                    value={catalogSearch}
                    onChange={(e) => setCatalogSearch(e.target.value)}
                    placeholder="Search skills..."
                    className="w-full rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] pl-9 pr-3.5 py-2 text-sm placeholder:text-muted-foreground/40 focus:outline-none focus:ring-1 focus:ring-primary"
                  />
                </div>
                <div className="flex gap-1 flex-wrap">
                  {Object.entries(dynamicCategories).map(([key, label]) => (
                    <button
                      key={key}
                      onClick={() => setCatalogFilter(key)}
                      className={cn(
                        "rounded-full px-2.5 py-1 text-[11px] font-medium border transition-colors",
                        catalogFilter === key
                          ? "bg-foreground/[0.08] text-foreground border-foreground/[0.15]"
                          : "border-foreground/[0.06] text-muted-foreground/60 hover:text-muted-foreground hover:border-foreground/[0.1]"
                      )}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>

              {installError && (
                <p className="text-xs text-red-400">{installError}</p>
              )}

              {/* Skill grid */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-2 max-h-[400px] overflow-y-auto pr-1">
                {catalogLoading && catalog.length <= 4 && (
                  <div className="col-span-full flex items-center justify-center py-8">
                    <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
                    <span className="ml-2 text-sm text-muted-foreground">Loading skill catalog...</span>
                  </div>
                )}
                {filteredCatalog.map((cat) => {
                  const isInstalled = installedNames.includes(cat.name);
                  const isInstalling = installingSkill === cat.name;
                  return (
                    <div
                      key={cat.name}
                      className="group flex items-center gap-3 rounded-lg border border-foreground/[0.06] bg-foreground/[0.01] p-3 hover:bg-foreground/[0.03] transition-colors"
                    >
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-medium">{cat.name}</span>
                          <span className={cn(
                            "inline-flex items-center rounded-full border px-1.5 py-0.5 text-[10px] font-medium",
                            CATEGORY_COLORS[cat.category] || "bg-zinc-500/10 text-zinc-400 border-zinc-500/20"
                          )}>
                            {CATALOG_CATEGORIES[cat.category]}
                          </span>
                        </div>
                        <p className="text-[11px] text-muted-foreground/70 mt-0.5 line-clamp-1">{cat.description}</p>
                        {cat.installs && <p className="text-[10px] text-muted-foreground/40 mt-0.5">{cat.installs} installs</p>}
                        {!cat.installs && <p className="text-[10px] text-muted-foreground/40 mt-0.5">{cat.repo}</p>}
                      </div>
                      <button
                        onClick={() => !isInstalled && !isInstalling && handleInstallFromCatalog(cat)}
                        disabled={isInstalled || isInstalling}
                        className={cn(
                          "shrink-0 rounded-lg px-3 py-1.5 text-xs font-medium transition-all",
                          isInstalled
                            ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 cursor-default"
                            : isInstalling
                              ? "bg-foreground/[0.04] text-muted-foreground"
                              : "bg-primary/10 text-primary hover:bg-primary/20 border border-primary/20"
                        )}
                      >
                        {isInstalled ? (
                          <span className="flex items-center gap-1"><Check className="h-3 w-3" /> Installed</span>
                        ) : isInstalling ? (
                          <span className="flex items-center gap-1"><Loader2 className="h-3 w-3 animate-spin" /> Installing</span>
                        ) : (
                          <span className="flex items-center gap-1"><Download className="h-3 w-3" /> Install</span>
                        )}
                      </button>
                    </div>
                  );
                })}
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Add/Edit Form */}
      <AnimatePresence>
        {showForm && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            className="overflow-hidden"
          >
            <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-5 space-y-4">
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-medium">
                  {editingSkill ? "Edit Skill" : "New Skill"}
                </h3>
                <button
                  onClick={resetForm}
                  className="p-1 rounded-lg text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>

              <div className="space-y-3">
                <div>
                  <label className="text-[11px] font-medium text-muted-foreground/70 block mb-1.5">
                    Name
                  </label>
                  <input
                    type="text"
                    value={formName}
                    onChange={(e) => setFormName(e.target.value)}
                    placeholder="e.g. deploy-to-prod"
                    className="w-full rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3.5 py-2.5 text-sm placeholder:text-muted-foreground/40 focus:outline-none focus:ring-1 focus:ring-primary"
                  />
                  <p className="text-[10px] text-muted-foreground/50 mt-1">
                    Lowercase with hyphens. Used as the directory name.
                  </p>
                </div>

                <div>
                  <label className="text-[11px] font-medium text-muted-foreground/70 block mb-1.5">
                    Description
                  </label>
                  <input
                    type="text"
                    value={formDescription}
                    onChange={(e) => setFormDescription(e.target.value)}
                    placeholder="What this skill does..."
                    className="w-full rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3.5 py-2.5 text-sm placeholder:text-muted-foreground/40 focus:outline-none focus:ring-1 focus:ring-primary"
                  />
                </div>

                <div>
                  <label className="text-[11px] font-medium text-muted-foreground/70 block mb-1.5">
                    Instructions (Markdown)
                  </label>
                  <textarea
                    value={formContent}
                    onChange={(e) => setFormContent(e.target.value)}
                    placeholder="Step-by-step instructions for the agent..."
                    rows={8}
                    className="w-full rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3.5 py-2.5 text-sm font-mono placeholder:text-muted-foreground/40 focus:outline-none focus:ring-1 focus:ring-primary resize-y"
                  />
                </div>
              </div>

              <div className="flex items-center justify-end gap-2">
                <button
                  onClick={resetForm}
                  className="rounded-xl px-4 py-2 text-sm text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04]"
                >
                  Cancel
                </button>
                <button
                  onClick={handleSave}
                  disabled={saving || !formName.trim() || !formDescription.trim()}
                  className="rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground shadow-lg shadow-primary/20 inline-flex items-center gap-1.5 disabled:opacity-50"
                >
                  <Save className="h-3.5 w-3.5" />
                  {saving ? "Saving..." : editingSkill ? "Update" : "Create"}
                </button>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Skills list */}
      {loading ? (
        <div className="flex items-center justify-center py-12">
          <RefreshCw className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      ) : skills.length === 0 && !showForm ? (
        <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
          <Sparkles className="h-10 w-10 mb-3 opacity-20" />
          <p className="text-sm font-medium">No skills defined</p>
          <p className="text-xs mt-1">
            Add custom skills to give this agent specialized capabilities.
          </p>
        </div>
      ) : (
        <div className="space-y-2">
          {skills.map((skill, i) => {
            const isExpanded = expandedSkill === skill.name;
            return (
              <motion.div
                key={skill.name}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: i * 0.05 }}
                className="group rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm overflow-hidden"
              >
                <div
                  className="flex items-center gap-3 p-4 cursor-pointer"
                  onClick={() =>
                    setExpandedSkill(isExpanded ? null : skill.name)
                  }
                >
                  <div className="flex items-center justify-center h-8 w-8 rounded-lg bg-amber-500/10">
                    <Code2 className="h-4 w-4 text-amber-400" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium">{skill.name}</span>
                      <span className="inline-flex items-center rounded-full border px-2.5 py-1 text-[11px] font-medium bg-amber-500/10 text-amber-400 border-amber-500/20">
                        skill
                      </span>
                    </div>
                    <p className="text-xs text-muted-foreground mt-0.5 line-clamp-2">
                      {skill.description}
                    </p>
                  </div>

                  {/* Actions */}
                  <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        startEdit(skill);
                      }}
                      className="p-1.5 rounded-lg text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
                      title="Edit"
                    >
                      <Pencil className="h-3.5 w-3.5" />
                    </button>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        handleDelete(skill.name);
                      }}
                      className="p-1.5 rounded-lg text-muted-foreground hover:text-red-400 hover:bg-accent transition-colors"
                      title="Delete"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>

                  <ChevronDown
                    className={cn(
                      "h-4 w-4 text-muted-foreground transition-transform",
                      isExpanded && "rotate-180"
                    )}
                  />
                </div>

                {/* Expanded content */}
                <AnimatePresence>
                  {isExpanded && (
                    <motion.div
                      initial={{ height: 0 }}
                      animate={{ height: "auto" }}
                      exit={{ height: 0 }}
                      className="overflow-hidden"
                    >
                      <div className="px-4 pb-4 pt-0">
                        <div className="rounded-lg border border-foreground/[0.06] bg-foreground/[0.02] p-3">
                          <pre className="text-xs text-muted-foreground whitespace-pre-wrap font-mono leading-relaxed">
                            {skill.content || "(no instructions)"}
                          </pre>
                        </div>
                      </div>
                    </motion.div>
                  )}
                </AnimatePresence>
              </motion.div>
            );
          })}
        </div>
      )}
    </div>
  );
}

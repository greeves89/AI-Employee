"use client";

import { useState, useEffect } from "react";
import {
  Sparkles, Search, Download, Loader2, RefreshCw,
  Package, Code2, Palette, Megaphone, FileText, Wrench,
  CheckCircle2, Bot, ChevronDown,
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

export default function SkillsPage() {
  const [catalog, setCatalog] = useState<CatalogSkill[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [selectedAgent, setSelectedAgent] = useState<Agent | null>(null);
  const [agentSkills, setAgentSkills] = useState<AgentSkill[]>([]);
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [installing, setInstalling] = useState<string | null>(null);
  const [showAgentPicker, setShowAgentPicker] = useState(false);

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
        if (online.length > 0) setSelectedAgent(online[0]);
      } catch {
        // ignore
      } finally {
        setLoading(false);
      }
    };
    load();
  }, []);

  // Load agent skills when agent changes
  useEffect(() => {
    if (!selectedAgent) return;
    api.getAgentSkills(selectedAgent.id).then(setAgentSkills).catch(() => setAgentSkills([]));
  }, [selectedAgent]);

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
      await api.installSkill(selectedAgent.id, skill.repo, skill.name);
      // Refresh agent skills
      const updated = await api.getAgentSkills(selectedAgent.id);
      setAgentSkills(updated);
    } catch {
      // ignore
    } finally {
      setInstalling(null);
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

  const categories = Array.from(new Set(catalog.map((s) => s.category)));

  return (
    <div className="min-h-screen">
      <Header title="Skills" subtitle="Browse and install skills for your agents" />

      <div className="p-6 space-y-6">
        {/* Top bar: Agent selector + Search + Refresh */}
        <div className="flex items-center gap-3 flex-wrap">
          {/* Agent Selector */}
          <div className="relative">
            <button
              onClick={() => setShowAgentPicker(!showAgentPicker)}
              className="flex items-center gap-2 rounded-xl border border-foreground/[0.08] bg-card/80 px-3 py-2 text-sm hover:bg-accent/50 transition-all"
            >
              <Bot className="h-4 w-4 text-violet-400" />
              <span className="font-medium">{selectedAgent?.name || "Agent waehlen"}</span>
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
          <button
            onClick={handleRefresh}
            disabled={refreshing}
            className="rounded-xl px-4 py-2 text-sm text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04] flex items-center gap-2"
          >
            <RefreshCw className={cn("w-4 h-4", refreshing && "animate-spin")} />
            Aktualisieren
          </button>

          {/* Installed count */}
          <div className="flex items-center gap-1.5 text-xs text-muted-foreground/60">
            <Package className="h-3.5 w-3.5" />
            {agentSkills.length} installiert
          </div>
        </div>

        {/* Category filters */}
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

        {/* Skills Grid */}
        {loading ? (
          <div className="flex items-center justify-center py-16">
            <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
          </div>
        ) : filtered.length === 0 ? (
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
        )}
      </div>
    </div>
  );
}

"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Brain,
  ChevronDown,
  ChevronRight,
  History,
  Loader2,
  Moon,
  Pencil,
  RefreshCw,
  Save,
  Star,
  Trash2,
  X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { deleteMemory, getAgentMemories, getMemoryHistory, updateMemory } from "@/lib/api";
import type { AgentMemory } from "@/lib/types";

// Provenance badge per memory source (null → no badge).
const SOURCE_CONFIG: Record<string, { label: string; className: string; icon?: typeof Moon }> = {
  agent: { label: "Agent", className: "bg-gray-500/10 text-gray-400 border-gray-500/20" },
  conversation: { label: "Gespräch", className: "bg-blue-500/10 text-blue-400 border-blue-500/20" },
  reflection: { label: "Nachtschicht", className: "bg-violet-500/10 text-violet-400 border-violet-500/20", icon: Moon },
  user: { label: "Du", className: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20" },
  improvement: { label: "Verbesserung", className: "bg-amber-500/10 text-amber-400 border-amber-500/20" },
  compaction: { label: "Kompaktierung", className: "bg-slate-500/10 text-slate-400 border-slate-500/20" },
};

const CATEGORIES = [
  { key: "all", label: "All", color: "bg-gray-400" },
  { key: "preference", label: "Preferences", color: "bg-blue-500" },
  { key: "contact", label: "Contacts", color: "bg-emerald-500" },
  { key: "project", label: "Projects", color: "bg-violet-500" },
  { key: "procedure", label: "Procedures", color: "bg-amber-500" },
  { key: "decision", label: "Decisions", color: "bg-cyan-500" },
  { key: "fact", label: "Facts", color: "bg-pink-500" },
  { key: "learning", label: "Learnings", color: "bg-orange-500" },
];

interface MemoryTabProps {
  agentId: string;
}

export function MemoryTab({ agentId }: MemoryTabProps) {
  const [memories, setMemories] = useState<AgentMemory[]>([]);
  const [categories, setCategories] = useState<Record<string, number>>({});
  const [activeCategory, setActiveCategory] = useState("all");
  const [loading, setLoading] = useState(true);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editContent, setEditContent] = useState("");
  const [editImportance, setEditImportance] = useState(3);
  const [reflectionOnly, setReflectionOnly] = useState(false);
  const [openHistoryId, setOpenHistoryId] = useState<number | null>(null);
  const [histories, setHistories] = useState<Record<number, AgentMemory[]>>({});
  const [historyLoadingId, setHistoryLoadingId] = useState<number | null>(null);

  const fetchMemories = useCallback(async () => {
    setLoading(true);
    try {
      const category = activeCategory === "all" ? undefined : activeCategory;
      const data = await getAgentMemories(agentId, category);
      setMemories(data.memories);
      setCategories(data.categories);
    } catch {
      // ignore
    }
    setLoading(false);
  }, [agentId, activeCategory]);

  useEffect(() => {
    fetchMemories();
  }, [fetchMemories]);

  const handleEdit = (mem: AgentMemory) => {
    setEditingId(mem.id);
    setEditContent(mem.content);
    setEditImportance(mem.importance);
  };

  const handleSave = async (id: number) => {
    try {
      await updateMemory(id, { content: editContent, importance: editImportance });
      setEditingId(null);
      fetchMemories();
    } catch {
      // ignore
    }
  };

  const handleDelete = async (id: number) => {
    try {
      await deleteMemory(id);
      setMemories((prev) => prev.filter((m) => m.id !== id));
    } catch {
      // ignore
    }
  };

  // Load the supersede chain on-demand when the "Verlauf" expander opens.
  const toggleHistory = async (memId: number) => {
    if (openHistoryId === memId) {
      setOpenHistoryId(null);
      return;
    }
    setOpenHistoryId(memId);
    if (!histories[memId]) {
      setHistoryLoadingId(memId);
      try {
        const data = await getMemoryHistory(memId);
        setHistories((prev) => ({ ...prev, [memId]: data.history }));
      } catch {
        // ignore
      }
      setHistoryLoadingId(null);
    }
  };

  const totalMemories = Object.values(categories).reduce((a, b) => a + b, 0);
  const visibleMemories = reflectionOnly
    ? memories.filter((m) => m.source === "reflection")
    : memories;

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Brain className="h-5 w-5 text-purple-400" />
          <h2 className="text-sm font-semibold">Long-term Memory</h2>
          <span className="text-xs text-muted-foreground">
            ({totalMemories} {totalMemories === 1 ? "entry" : "entries"})
          </span>
        </div>
        <button
          onClick={fetchMemories}
          className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs text-muted-foreground hover:bg-accent hover:text-foreground transition-colors"
        >
          <RefreshCw className={cn("h-3 w-3", loading && "animate-spin")} />
          Refresh
        </button>
      </div>

      {/* Category filter */}
      <div className="flex flex-wrap gap-1.5">
        {CATEGORIES.map((cat) => {
          const count = cat.key === "all" ? totalMemories : (categories[cat.key] || 0);
          return (
            <button
              key={cat.key}
              onClick={() => setActiveCategory(cat.key)}
              className={cn(
                "flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[11px] font-medium transition-colors",
                activeCategory === cat.key
                  ? "bg-accent text-foreground shadow-sm"
                  : "text-muted-foreground hover:bg-accent/50 hover:text-foreground"
              )}
            >
              <div className={cn("h-1.5 w-1.5 rounded-full", cat.color)} />
              {cat.label}
              {count > 0 && (
                <span className="text-[10px] text-muted-foreground/60">({count})</span>
              )}
            </button>
          );
        })}
        <button
          onClick={() => setReflectionOnly((v) => !v)}
          className={cn(
            "flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[11px] font-medium transition-colors",
            reflectionOnly
              ? "bg-violet-500/10 text-violet-400 shadow-sm"
              : "text-muted-foreground hover:bg-accent/50 hover:text-foreground"
          )}
        >
          <Moon className="h-3 w-3" />
          Nur Nachtschicht
        </button>
      </div>

      {/* Memory list */}
      {loading ? (
        <div className="flex items-center justify-center py-12">
          <RefreshCw className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      ) : visibleMemories.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
          <Brain className="h-10 w-10 mb-3 opacity-20" />
          <p className="text-sm font-medium">
            {reflectionOnly ? "Keine Nachtschicht-Einträge" : "No memories yet"}
          </p>
          <p className="text-xs mt-1">
            {reflectionOnly
              ? "Der nächtliche Reflexions-Lauf hat hier noch nichts abgelegt."
              : "This agent will automatically save important information here as it works."}
          </p>
        </div>
      ) : (
        <div className="space-y-2">
          {visibleMemories.map((mem) => {
            const cat = CATEGORIES.find((c) => c.key === mem.category);
            const isEditing = editingId === mem.id;
            const source = mem.source ? SOURCE_CONFIG[mem.source] : undefined;
            const SourceIcon = source?.icon;
            const isHistoryOpen = openHistoryId === mem.id;
            const history = histories[mem.id];

            return (
              <div
                key={mem.id}
                className={cn(
                  "group rounded-xl border border-border/50 bg-card/50 transition-colors hover:border-border",
                  isEditing && "border-primary/50"
                )}
              >
                <div className="flex items-start gap-3 p-3">
                  {/* Category dot */}
                  <div className="pt-1">
                    <div className={cn("h-2 w-2 rounded-full", cat?.color || "bg-gray-400")} />
                  </div>

                  {/* Content */}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="text-xs font-semibold text-foreground">{mem.key}</span>
                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-accent text-muted-foreground">
                        {mem.category}
                      </span>
                      {source && (
                        <span
                          className={cn(
                            "inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded border",
                            source.className
                          )}
                        >
                          {SourceIcon && <SourceIcon className="h-2.5 w-2.5" />}
                          {source.label}
                        </span>
                      )}
                      {/* Importance stars */}
                      <div className="flex gap-0.5 ml-auto">
                        {[1, 2, 3, 4, 5].map((i) => (
                          <Star
                            key={i}
                            className={cn(
                              "h-2.5 w-2.5",
                              i <= (isEditing ? editImportance : mem.importance)
                                ? "text-amber-400 fill-amber-400"
                                : "text-muted-foreground/20"
                            )}
                            onClick={() => isEditing && setEditImportance(i)}
                          />
                        ))}
                      </div>
                    </div>

                    {isEditing ? (
                      <textarea
                        value={editContent}
                        onChange={(e) => setEditContent(e.target.value)}
                        className="w-full text-xs bg-background border border-border rounded-lg p-2 resize-none focus:outline-none focus:ring-1 focus:ring-primary"
                        rows={3}
                      />
                    ) : (
                      <p className="text-xs text-muted-foreground whitespace-pre-wrap">
                        {mem.content}
                      </p>
                    )}

                    <div className="flex items-center gap-3 mt-1.5">
                      <span className="text-[10px] text-muted-foreground/50">
                        {new Date(mem.updated_at).toLocaleDateString()}
                      </span>
                      {mem.access_count > 0 && (
                        <span className="text-[10px] text-muted-foreground/50">
                          accessed {mem.access_count}x
                        </span>
                      )}
                      <button
                        onClick={() => toggleHistory(mem.id)}
                        className={cn(
                          "flex items-center gap-1 text-[10px] transition-colors",
                          isHistoryOpen
                            ? "text-foreground"
                            : "text-muted-foreground/50 hover:text-foreground"
                        )}
                      >
                        <History className="h-2.5 w-2.5" />
                        Verlauf
                        {isHistoryOpen ? (
                          <ChevronDown className="h-2.5 w-2.5" />
                        ) : (
                          <ChevronRight className="h-2.5 w-2.5" />
                        )}
                      </button>
                    </div>

                    {/* History: supersede chain, newest first */}
                    {isHistoryOpen && (
                      <div className="mt-2 border-t border-border/50 pt-2">
                        {historyLoadingId === mem.id ? (
                          <div className="flex items-center gap-2 py-1 text-[11px] text-muted-foreground/50">
                            <Loader2 className="h-3 w-3 animate-spin" />
                            Lade Verlauf...
                          </div>
                        ) : !history || history.length <= 1 ? (
                          <p className="py-1 text-[11px] text-muted-foreground/50">
                            Kein früherer Stand — dieser Eintrag wurde noch nie überschrieben.
                          </p>
                        ) : (
                          <div className="space-y-2">
                            {history.map((entry, i) => (
                              <div
                                key={entry.id}
                                className={cn(
                                  "border-l-2 pl-2.5",
                                  i === 0 ? "border-violet-400" : "border-border/60"
                                )}
                              >
                                <div className="flex items-center gap-2">
                                  <span className="text-[10px] text-muted-foreground/60">
                                    {new Date(entry.updated_at).toLocaleString("de-DE", {
                                      dateStyle: "medium",
                                      timeStyle: "short",
                                    })}
                                  </span>
                                  {i === 0 && (
                                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-violet-500/10 text-violet-400 border border-violet-500/20">
                                      Aktuell
                                    </span>
                                  )}
                                </div>
                                <p
                                  className={cn(
                                    "text-[11px] whitespace-pre-wrap mt-0.5",
                                    i === 0 ? "text-foreground/90" : "text-muted-foreground/70"
                                  )}
                                >
                                  {entry.content}
                                </p>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    )}
                  </div>

                  {/* Actions */}
                  <div className={cn(
                    "flex flex-col gap-1",
                    isEditing ? "opacity-100" : "opacity-0 group-hover:opacity-100 transition-opacity"
                  )}>
                    {isEditing ? (
                      <>
                        <button
                          onClick={() => handleSave(mem.id)}
                          className="p-1.5 rounded-lg text-emerald-400 hover:bg-accent transition-colors"
                          title="Save"
                        >
                          <Save className="h-3 w-3" />
                        </button>
                        <button
                          onClick={() => setEditingId(null)}
                          className="p-1.5 rounded-lg text-muted-foreground hover:bg-accent transition-colors"
                          title="Cancel"
                        >
                          <X className="h-3 w-3" />
                        </button>
                      </>
                    ) : (
                      <>
                        <button
                          onClick={() => handleEdit(mem)}
                          className="p-1.5 rounded-lg text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
                          title="Edit"
                        >
                          <Pencil className="h-3 w-3" />
                        </button>
                        <button
                          onClick={() => handleDelete(mem.id)}
                          className="p-1.5 rounded-lg text-muted-foreground hover:text-red-400 hover:bg-accent transition-colors"
                          title="Delete"
                        >
                          <Trash2 className="h-3 w-3" />
                        </button>
                      </>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

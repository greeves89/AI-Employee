"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Brain,
  ChevronDown,
  Pencil,
  RefreshCw,
  Save,
  Star,
  Trash2,
  X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { deleteMemory, getAgentMemories, updateMemory } from "@/lib/api";
import type { AgentMemory } from "@/lib/types";

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

  const totalMemories = Object.values(categories).reduce((a, b) => a + b, 0);

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
      </div>

      {/* Memory list */}
      {loading ? (
        <div className="flex items-center justify-center py-12">
          <RefreshCw className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      ) : memories.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
          <Brain className="h-10 w-10 mb-3 opacity-20" />
          <p className="text-sm font-medium">No memories yet</p>
          <p className="text-xs mt-1">
            This agent will automatically save important information here as it works.
          </p>
        </div>
      ) : (
        <div className="space-y-2">
          {memories.map((mem) => {
            const cat = CATEGORIES.find((c) => c.key === mem.category);
            const isEditing = editingId === mem.id;

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
                    </div>
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

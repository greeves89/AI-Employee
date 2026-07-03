"use client";

import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  BookOpen, Plus, Search, Tag, Network, FileText,
  Trash2, ArrowLeft, Clock, User, Link2, X, Hash, ZoomIn, ZoomOut, Maximize2,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { cn } from "@/lib/utils";
import {
  getKnowledgeEntries, getKnowledgeEntry, createKnowledgeEntry,
  updateKnowledgeEntry, deleteKnowledgeEntry, getKnowledgeTags,
  getBrainGraph,
} from "@/lib/api";
import type { KnowledgeEntry, KnowledgeTag, KnowledgeGraphNode, KnowledgeGraphEdge } from "@/lib/types";
import { useConfirm, useToast } from "@/components/ui/dialog-provider";

type ViewMode = "list" | "editor" | "graph";

export default function KnowledgePage() {
  const [entries, setEntries] = useState<KnowledgeEntry[]>([]);
  const [tags, setTags] = useState<KnowledgeTag[]>([]);
  const [selectedEntry, setSelectedEntry] = useState<KnowledgeEntry | null>(null);
  const [viewMode, setViewMode] = useState<ViewMode>("list");
  const [searchQuery, setSearchQuery] = useState("");
  const [activeTag, setActiveTag] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [editTitle, setEditTitle] = useState("");
  const [editContent, setEditContent] = useState("");
  const [editTags, setEditTags] = useState("");
  const [isNew, setIsNew] = useState(false);
  const [showPreview, setShowPreview] = useState(false);
  const [graphData, setGraphData] = useState<{ nodes: KnowledgeGraphNode[]; edges: KnowledgeGraphEdge[] } | null>(null);
  const [previousView, setPreviousView] = useState<ViewMode>("list");
  const [graphPreview, setGraphPreview] = useState<KnowledgeEntry | null>(null);
  const confirm = useConfirm();
  const toast = useToast();

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [entryData, tagData] = await Promise.all([
        getKnowledgeEntries(searchQuery || undefined, activeTag || undefined),
        getKnowledgeTags(),
      ]);
      setEntries(entryData.entries);
      setTags(tagData.tags);
    } catch (e) {
      console.error("Failed to load knowledge:", e);
    } finally {
      setLoading(false);
    }
  }, [searchQuery, activeTag]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const openEntry = useCallback(async (id: number) => {
    try {
      const entry = await getKnowledgeEntry(id);
      setSelectedEntry(entry);
      setEditTitle(entry.title);
      setEditContent(entry.content);
      setEditTags((entry.tags || []).join(", "));
      setIsNew(false);
      setPreviousView((prev) => (prev === "editor" ? prev : viewMode === "editor" ? "list" : viewMode));
      setViewMode("editor");
    } catch (e) {
      console.error("Failed to load entry:", e);
    }
  }, [viewMode]);

  const openNewEntry = useCallback(() => {
    setSelectedEntry(null);
    setEditTitle("");
    setEditContent("");
    setEditTags("");
    setIsNew(true);
    setPreviousView(viewMode === "editor" ? "list" : viewMode);
    setViewMode("editor");
    setShowPreview(false);
  }, [viewMode]);

  const saveEntry = useCallback(async () => {
    const tagsArray = editTags
      .split(",")
      .map((t) => t.trim().replace(/^#/, ""))
      .filter(Boolean);

    try {
      if (isNew) {
        await createKnowledgeEntry({ title: editTitle, content: editContent, tags: tagsArray });
      } else if (selectedEntry) {
        await updateKnowledgeEntry(selectedEntry.id, { title: editTitle, content: editContent, tags: tagsArray });
      }
      setViewMode("list");
      refresh();
    } catch (e) {
      console.error("Failed to save:", e);
    }
  }, [isNew, selectedEntry, editTitle, editContent, editTags, refresh]);

  const handleDelete = useCallback(async (id: number) => {
    const ok = await confirm({
      title: "Delete this entry?",
      message: "The knowledge entry will be permanently removed.",
      variant: "destructive",
      confirmLabel: "Delete",
    });
    if (!ok) return;
    try {
      await deleteKnowledgeEntry(id);
      setViewMode("list");
      setSelectedEntry(null);
      toast.success("Entry deleted");
      refresh();
    } catch (e) {
      console.error("Failed to delete:", e);
      toast.error("Failed to delete entry");
    }
  }, [refresh, confirm, toast]);

  const openGraph = useCallback(async () => {
    try {
      const data = await getBrainGraph();
      setGraphData(data);
      setGraphPreview(null);
      setViewMode("graph");
    } catch (e) {
      console.error("Failed to load graph:", e);
    }
  }, []);

  const openGraphPreview = useCallback(async (id: number) => {
    try {
      const entry = await getKnowledgeEntry(id);
      setGraphPreview(entry);
    } catch (e) {
      console.error("Failed to load entry:", e);
    }
  }, []);

  const handleBacklinkClick = useCallback(
    (title: string) => {
      const entry = entries.find((e) => e.title === title);
      if (entry) openEntry(entry.id);
    },
    [entries, openEntry]
  );

  return (
    <div className="flex h-full min-h-0 flex-col gap-6 p-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          {viewMode !== "list" && (
            <button
              onClick={() => setViewMode(viewMode === "editor" ? previousView : "list")}
              className="rounded-lg p-2 text-muted-foreground hover:bg-foreground/[0.04] hover:text-foreground"
            >
              <ArrowLeft className="h-4 w-4" />
            </button>
          )}
          <BookOpen className="h-6 w-6 text-primary" />
          <h1 className="text-xl font-semibold">Knowledge Base</h1>
          <span className="rounded-full bg-foreground/[0.06] px-2.5 py-0.5 text-xs text-muted-foreground">
            {entries.length} entries
          </span>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={openGraph}
            className={cn(
              "flex items-center gap-2 rounded-xl px-4 py-2 text-sm",
              viewMode === "graph"
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04]"
            )}
          >
            <Network className="h-4 w-4" />
            Graph
          </button>
          <button
            onClick={openNewEntry}
            className="flex items-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground shadow-lg shadow-primary/20"
          >
            <Plus className="h-4 w-4" />
            New Entry
          </button>
        </div>
      </div>

      {/* Content area */}
      <AnimatePresence mode="wait">
        {viewMode === "list" && (
          <motion.div
            key="list"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="flex flex-1 flex-col lg:flex-row gap-6 min-h-0"
          >
            {/* Main list */}
            <div className="flex flex-1 flex-col gap-4 min-h-0 min-w-0">
              {/* Search */}
              <div className="relative">
                <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground/50" />
                <input
                  type="text"
                  placeholder="Search knowledge base..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="w-full rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] py-2.5 pl-10 pr-4 text-sm placeholder:text-muted-foreground/40 focus:border-primary/30 focus:outline-none"
                />
              </div>

              {activeTag && (
                <div className="flex items-center gap-2">
                  <span className="text-[11px] text-muted-foreground/70">Filtered by:</span>
                  <button
                    onClick={() => setActiveTag(null)}
                    className="inline-flex items-center gap-1 rounded-full border border-primary/20 bg-primary/10 px-2.5 py-1 text-[11px] font-medium text-primary"
                  >
                    #{activeTag}
                    <X className="h-3 w-3" />
                  </button>
                </div>
              )}

              <div className="flex-1 overflow-y-auto space-y-2">
                {loading ? (
                  <div className="space-y-2">
                    {[1, 2, 3].map((i) => (
                      <div key={i} className="h-20 animate-pulse rounded-xl bg-foreground/[0.04]" />
                    ))}
                  </div>
                ) : entries.length === 0 ? (
                  <div className="flex flex-col items-center justify-center py-20 text-muted-foreground/50">
                    <BookOpen className="h-12 w-12 mb-3" />
                    <p className="text-sm">No entries yet</p>
                    <p className="text-xs mt-1">Create your first knowledge entry or let agents contribute</p>
                  </div>
                ) : (
                  entries.map((entry, idx) => (
                    <motion.button
                      key={entry.id}
                      initial={{ opacity: 0, y: 10 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ delay: idx * 0.03 }}
                      onClick={() => openEntry(entry.id)}
                      className="w-full text-left rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-4 hover:border-foreground/[0.12] transition-colors"
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2">
                            <FileText className="h-4 w-4 text-muted-foreground/50 shrink-0" />
                            <h3 className="font-medium text-sm truncate">{entry.title}</h3>
                          </div>
                          <p className="mt-1 text-xs text-muted-foreground/60 line-clamp-2">
                            {entry.content.replace(/[#\[\]*_`]/g, "").substring(0, 150)}
                          </p>
                          {entry.tags.length > 0 && (
                            <div className="mt-2 flex flex-wrap gap-1">
                              {entry.tags.slice(0, 5).map((tag) => (
                                <span
                                  key={tag}
                                  className="inline-flex items-center rounded-full bg-foreground/[0.04] px-2 py-0.5 text-[10px] text-muted-foreground/60"
                                >
                                  #{tag}
                                </span>
                              ))}
                            </div>
                          )}
                        </div>
                        <div className="flex flex-col items-end gap-1 shrink-0">
                          <span className="text-[10px] text-muted-foreground/40">
                            {new Date(entry.updated_at).toLocaleDateString()}
                          </span>
                          {entry.backlinks.length > 0 && (
                            <span className="inline-flex items-center gap-1 text-[10px] text-muted-foreground/40">
                              <Link2 className="h-3 w-3" />
                              {entry.backlinks.length}
                            </span>
                          )}
                        </div>
                      </div>
                    </motion.button>
                  ))
                )}
              </div>
            </div>

            {/* Tags sidebar */}
            {tags.length > 0 && (
              <div className="w-56 shrink-0 hidden lg:block">
                <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-4">
                  <h3 className="flex items-center gap-2 text-[11px] font-medium text-muted-foreground/70 uppercase tracking-wider mb-3">
                    <Tag className="h-3.5 w-3.5" />
                    Tags
                  </h3>
                  <div className="space-y-1">
                    {tags.map((tag) => (
                      <button
                        key={tag.name}
                        onClick={() => setActiveTag(activeTag === tag.name ? null : tag.name)}
                        className={cn(
                          "flex w-full items-center justify-between rounded-lg px-2.5 py-1.5 text-xs transition-colors",
                          activeTag === tag.name
                            ? "bg-primary/10 text-primary"
                            : "text-muted-foreground hover:bg-foreground/[0.04] hover:text-foreground"
                        )}
                      >
                        <span className="flex items-center gap-1.5">
                          <Hash className="h-3 w-3" />
                          {tag.name}
                        </span>
                        <span className="rounded-full bg-foreground/[0.06] px-1.5 py-0.5 text-[10px]">
                          {tag.count}
                        </span>
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            )}
          </motion.div>
        )}

        {viewMode === "editor" && (
          <motion.div
            key="editor"
            initial={{ opacity: 0, x: 20 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: -20 }}
            className="flex flex-1 flex-col gap-4 min-h-0"
          >
            <div className="flex items-center justify-between">
              <input
                type="text"
                value={editTitle}
                onChange={(e) => setEditTitle(e.target.value)}
                placeholder="Entry title..."
                className="text-lg font-semibold bg-transparent border-none outline-none placeholder:text-muted-foreground/30 flex-1"
              />
              <div className="flex items-center gap-2">
                {!isNew && selectedEntry && (
                  <>
                    <span className="flex items-center gap-1 text-[10px] text-muted-foreground/40">
                      <User className="h-3 w-3" />
                      {selectedEntry.created_by}
                    </span>
                    <span className="flex items-center gap-1 text-[10px] text-muted-foreground/40">
                      <Clock className="h-3 w-3" />
                      {new Date(selectedEntry.updated_at).toLocaleString()}
                    </span>
                  </>
                )}
                <button
                  onClick={() => setShowPreview(!showPreview)}
                  className={cn(
                    "rounded-lg px-3 py-1.5 text-xs",
                    showPreview ? "bg-primary/10 text-primary" : "text-muted-foreground hover:bg-foreground/[0.04]"
                  )}
                >
                  {showPreview ? "Edit" : "Preview"}
                </button>
                {!isNew && selectedEntry && (
                  <button
                    onClick={() => handleDelete(selectedEntry.id)}
                    className="rounded-lg p-1.5 text-muted-foreground/50 hover:text-red-400 hover:bg-red-500/10"
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                )}
                <button
                  onClick={saveEntry}
                  disabled={!editTitle.trim()}
                  className="rounded-xl bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-lg shadow-primary/20 disabled:opacity-50"
                >
                  Save
                </button>
              </div>
            </div>

            <div className="flex items-center gap-2">
              <Tag className="h-3.5 w-3.5 text-muted-foreground/50" />
              <input
                type="text"
                value={editTags}
                onChange={(e) => setEditTags(e.target.value)}
                placeholder="Tags (comma-separated): project, decision, contact..."
                className="flex-1 bg-transparent border-none outline-none text-xs text-muted-foreground placeholder:text-muted-foreground/30"
              />
            </div>

            <div className="flex-1 min-h-0 flex gap-4">
              {showPreview ? (
                <div className="flex-1 overflow-y-auto rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-6">
                  <div className="prose prose-sm prose-invert max-w-none">
                    <BacklinkMarkdown content={editContent} onBacklinkClick={handleBacklinkClick} />
                  </div>
                </div>
              ) : (
                <textarea
                  value={editContent}
                  onChange={(e) => setEditContent(e.target.value)}
                  placeholder={"Write markdown here...\n\nUse [[Title]] to link to other entries\nUse #tags inline for categorization"}
                  className="flex-1 resize-none rounded-xl border border-foreground/[0.08] bg-foreground/[0.02] p-4 text-sm font-mono placeholder:text-muted-foreground/30 focus:border-primary/30 focus:outline-none"
                />
              )}
            </div>

            {!isNew && selectedEntry && (selectedEntry.backlinks.length > 0 || (selectedEntry.incoming_backlinks || []).length > 0) && (
              <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-4">
                <div className="flex gap-8">
                  {selectedEntry.backlinks.length > 0 && (
                    <div>
                      <h4 className="text-[11px] font-medium text-muted-foreground/70 uppercase tracking-wider mb-2">
                        Links to
                      </h4>
                      <div className="flex flex-wrap gap-1.5">
                        {selectedEntry.backlinks.map((link) => (
                          <button
                            key={link}
                            onClick={() => handleBacklinkClick(link)}
                            className="inline-flex items-center gap-1 rounded-full border border-primary/20 bg-primary/5 px-2.5 py-1 text-[11px] text-primary hover:bg-primary/10 transition-colors"
                          >
                            <Link2 className="h-3 w-3" />
                            {link}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                  {(selectedEntry.incoming_backlinks || []).length > 0 && (
                    <div>
                      <h4 className="text-[11px] font-medium text-muted-foreground/70 uppercase tracking-wider mb-2">
                        Linked from
                      </h4>
                      <div className="flex flex-wrap gap-1.5">
                        {(selectedEntry.incoming_backlinks || []).map((link) => (
                          <button
                            key={link}
                            onClick={() => handleBacklinkClick(link)}
                            className="inline-flex items-center gap-1 rounded-full border border-emerald-500/20 bg-emerald-500/5 px-2.5 py-1 text-[11px] text-emerald-400 hover:bg-emerald-500/10 transition-colors"
                          >
                            <ArrowLeft className="h-3 w-3" />
                            {link}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              </div>
            )}
          </motion.div>
        )}

        {viewMode === "graph" && graphData && (
          <motion.div
            key="graph"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="flex-1 min-h-0 rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm overflow-hidden relative"
          >
            <ForceGraph
              nodes={graphData.nodes}
              edges={graphData.edges}
              onNodeClick={openGraphPreview}
            />

            {/* Reading panel — absolutely positioned so graph size never changes */}
            <AnimatePresence>
              {graphPreview && (
                <motion.div
                  key={graphPreview.id}
                  initial={{ opacity: 0, x: 24 }}
                  animate={{ opacity: 1, x: 0 }}
                  exit={{ opacity: 0, x: 24 }}
                  transition={{ duration: 0.18 }}
                  className="absolute right-0 top-0 h-full w-80 border-l border-foreground/[0.08] bg-black/80 backdrop-blur-md flex flex-col z-20"
                >
                  {/* Header */}
                  <div className="flex items-start justify-between gap-2 px-4 pt-4 pb-3 border-b border-foreground/[0.06]">
                    <div className="flex-1 min-w-0">
                      <h2 className="text-sm font-semibold leading-snug text-foreground">{graphPreview.title}</h2>
                      {graphPreview.tags.length > 0 && (
                        <div className="mt-1.5 flex flex-wrap gap-1">
                          {graphPreview.tags.map((tag) => (
                            <span key={tag} className="inline-flex items-center rounded-full bg-foreground/[0.06] px-2 py-0.5 text-[10px] text-muted-foreground/70">
                              #{tag}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                    <div className="flex items-center gap-1 shrink-0">
                      <button
                        onClick={() => openEntry(graphPreview.id)}
                        className="rounded-lg px-2.5 py-1.5 text-[11px] font-medium text-muted-foreground hover:text-foreground hover:bg-foreground/[0.06] transition-colors"
                      >
                        Edit
                      </button>
                      <button
                        onClick={() => setGraphPreview(null)}
                        className="rounded-lg p-1.5 text-muted-foreground/50 hover:text-foreground hover:bg-foreground/[0.06] transition-colors"
                      >
                        <X className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  </div>

                  {/* Content */}
                  <div className="flex-1 overflow-y-auto px-4 py-4">
                    <div className="prose prose-sm prose-invert max-w-none prose-p:text-muted-foreground/80 prose-headings:text-foreground prose-strong:text-foreground prose-code:text-primary">
                      <BacklinkMarkdown
                        content={graphPreview.content}
                        onBacklinkClick={(title) => {
                          const found = entries.find((e) => e.title === title);
                          if (found) openGraphPreview(found.id);
                        }}
                      />
                    </div>
                  </div>

                  {/* Backlinks */}
                  {(graphPreview.backlinks.length > 0 || (graphPreview.incoming_backlinks || []).length > 0) && (
                    <div className="border-t border-foreground/[0.06] px-4 py-3 space-y-2">
                      {graphPreview.backlinks.length > 0 && (
                        <div>
                          <p className="text-[9px] font-semibold uppercase tracking-widest text-muted-foreground/40 mb-1.5">Links to</p>
                          <div className="flex flex-wrap gap-1">
                            {graphPreview.backlinks.map((link) => (
                              <button
                                key={link}
                                onClick={() => { const f = entries.find((e) => e.title === link); if (f) openGraphPreview(f.id); }}
                                className="inline-flex items-center gap-1 rounded-full border border-primary/20 bg-primary/5 px-2 py-0.5 text-[10px] text-primary hover:bg-primary/10"
                              >
                                <Link2 className="h-2.5 w-2.5" />
                                {link}
                              </button>
                            ))}
                          </div>
                        </div>
                      )}
                      {(graphPreview.incoming_backlinks || []).length > 0 && (
                        <div>
                          <p className="text-[9px] font-semibold uppercase tracking-widest text-muted-foreground/40 mb-1.5">Linked from</p>
                          <div className="flex flex-wrap gap-1">
                            {(graphPreview.incoming_backlinks || []).map((link) => (
                              <button
                                key={link}
                                onClick={() => { const f = entries.find((e) => e.title === link); if (f) openGraphPreview(f.id); }}
                                className="inline-flex items-center gap-1 rounded-full border border-emerald-500/20 bg-emerald-500/5 px-2 py-0.5 text-[10px] text-emerald-400 hover:bg-emerald-500/10"
                              >
                                <ArrowLeft className="h-2.5 w-2.5" />
                                {link}
                              </button>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  )}

                  {/* Footer meta */}
                  <div className="border-t border-foreground/[0.06] px-4 py-2 flex items-center gap-3">
                    {graphPreview.created_by && (
                      <span className="flex items-center gap-1 text-[10px] text-muted-foreground/30">
                        <User className="h-2.5 w-2.5" />
                        {graphPreview.created_by}
                      </span>
                    )}
                    <span className="flex items-center gap-1 text-[10px] text-muted-foreground/30 ml-auto">
                      <Clock className="h-2.5 w-2.5" />
                      {new Date(graphPreview.updated_at).toLocaleDateString()}
                    </span>
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

// --- Markdown renderer with [[backlink]] support ---

function BacklinkMarkdown({ content, onBacklinkClick }: { content: string; onBacklinkClick: (title: string) => void }) {
  const processedContent = content.replace(
    /\[\[([^\]]+)\]\]/g,
    (_, title) => `[🔗 ${title}](#backlink:${encodeURIComponent(title)})`
  );

  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        a: ({ href, children, ...props }) => {
          if (href?.startsWith("#backlink:")) {
            const title = decodeURIComponent(href.replace("#backlink:", ""));
            return (
              <button
                onClick={() => onBacklinkClick(title)}
                className="inline-flex items-center gap-0.5 rounded bg-primary/10 px-1.5 py-0.5 text-primary hover:bg-primary/20 transition-colors font-medium no-underline"
              >
                {children}
              </button>
            );
          }
          return <a href={href} {...props} target="_blank" rel="noopener noreferrer">{children}</a>;
        },
      }}
    >
      {processedContent}
    </ReactMarkdown>
  );
}

// --- Force-directed graph — Obsidian-style ---

interface ForceGraphProps {
  nodes: KnowledgeGraphNode[];
  edges: KnowledgeGraphEdge[];
  onNodeClick: (id: number) => void;
}

interface SimNode extends KnowledgeGraphNode {
  x: number;
  y: number;
  vx: number;
  vy: number;
  degree: number;
}

const TAG_PALETTE = [
  "#818cf8", "#34d399", "#fbbf24", "#f87171", "#a78bfa",
  "#f472b6", "#22d3ee", "#fb923c", "#2dd4bf", "#c084fc",
  "#60a5fa", "#a3e635", "#e879f9", "#4ade80", "#facc15",
];

function buildTagColors(nodes: KnowledgeGraphNode[]): Record<string, string> {
  const map: Record<string, string> = {};
  let idx = 0;
  for (const n of nodes) {
    const tag = n.tags[0];
    if (tag && !map[tag]) {
      map[tag] = TAG_PALETTE[idx % TAG_PALETTE.length];
      idx++;
    }
  }
  return map;
}

function ForceGraph({ nodes, edges, onNodeClick }: ForceGraphProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const [simNodes, setSimNodes] = useState<SimNode[]>([]);
  const [dimensions, setDimensions] = useState({ width: 800, height: 500 });
  const [hoveredNode, setHoveredNode] = useState<SimNode | null>(null);
  const [selectedTag, setSelectedTag] = useState<string | null>(null);
  const [transform, setTransform] = useState({ x: 0, y: 0, scale: 1 });
  const animRef = useRef<number>(0);
  const simRef = useRef<SimNode[]>([]);
  const isPanning = useRef(false);
  const panStart = useRef({ x: 0, y: 0, tx: 0, ty: 0 });

  const tagColors = useMemo(() => buildTagColors(nodes), [nodes]);

  // Nodes in selected tag (for panel)
  const selectedTagNodes = useMemo(() => {
    if (!selectedTag) return [];
    return simNodes
      .filter((n) => n.tags[0] === selectedTag)
      .sort((a, b) => b.degree - a.degree);
  }, [selectedTag, simNodes]);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const observer = new ResizeObserver((entries) => {
      const rect = entries[0].contentRect;
      setDimensions({ width: rect.width, height: rect.height });
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    cancelAnimationFrame(animRef.current);
    if (nodes.length === 0) return;

    const degreeMap = new Map<number, number>();
    for (const e of edges) {
      degreeMap.set(e.source, (degreeMap.get(e.source) ?? 0) + 1);
      degreeMap.set(e.target, (degreeMap.get(e.target) ?? 0) + 1);
    }

    const cx = dimensions.width / 2;
    const cy = dimensions.height / 2;
    const spread = Math.min(dimensions.width, dimensions.height) * 0.35;
    const angle = (2 * Math.PI) / nodes.length;

    const initialNodes: SimNode[] = nodes.map((n, i) => ({
      ...n,
      x: cx + spread * Math.cos(i * angle) + (Math.random() - 0.5) * 50,
      y: cy + spread * Math.sin(i * angle) + (Math.random() - 0.5) * 50,
      vx: 0,
      vy: 0,
      degree: degreeMap.get(n.id) ?? 0,
    }));
    simRef.current = initialNodes;

    const idToIdx = new Map<number, number>();
    nodes.forEach((n, i) => idToIdx.set(n.id, i));

    let iteration = 0;
    const MAX_ITER = 500;
    // Obsidian-style: tight packing, short link distance
    const REPULSION = 1800;
    const LINK_DIST = 55;
    const DAMPING = 0.5;
    const GRAVITY = 0.018;

    function tick() {
      const sn = simRef.current;
      if (iteration >= MAX_ITER) { setSimNodes([...sn]); return; }

      const alpha = Math.max(0.005, 1 - iteration / MAX_ITER);
      const k = alpha * 0.18;

      // Center gravity — keeps graph visible
      for (const n of sn) {
        n.vx += (cx - n.x) * k * GRAVITY;
        n.vy += (cy - n.y) * k * GRAVITY;
      }

      // Node–node repulsion
      for (let i = 0; i < sn.length; i++) {
        for (let j = i + 1; j < sn.length; j++) {
          const dx = sn[j].x - sn[i].x || 0.01;
          const dy = sn[j].y - sn[i].y || 0.01;
          const dist2 = dx * dx + dy * dy;
          const dist = Math.sqrt(dist2);
          const f = (k * REPULSION) / dist2;
          const fx = (dx / dist) * f;
          const fy = (dy / dist) * f;
          sn[i].vx -= fx; sn[i].vy -= fy;
          sn[j].vx += fx; sn[j].vy += fy;
        }
      }

      // Edge spring forces — backlinks pull hard, semantic pull softly
      for (const edge of edges) {
        const si = idToIdx.get(edge.source);
        const ti = idToIdx.get(edge.target);
        if (si === undefined || ti === undefined) continue;
        const dx = sn[ti].x - sn[si].x;
        const dy = sn[ti].y - sn[si].y;
        const dist = Math.max(Math.sqrt(dx * dx + dy * dy), 1);
        const strength = edge.type === "semantic" ? 0.04 : 0.09;
        const f = (dist - LINK_DIST) * k * strength;
        const fx = (dx / dist) * f;
        const fy = (dy / dist) * f;
        sn[si].vx += fx; sn[si].vy += fy;
        sn[ti].vx -= fx; sn[ti].vy -= fy;
      }

      for (const n of sn) {
        n.vx *= DAMPING;
        n.vy *= DAMPING;
        n.x = Math.max(40, Math.min(dimensions.width - 40, n.x + n.vx));
        n.y = Math.max(40, Math.min(dimensions.height - 40, n.y + n.vy));
      }

      iteration++;
      if (iteration % 4 === 0) setSimNodes([...sn]);
      animRef.current = requestAnimationFrame(tick);
    }

    animRef.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(animRef.current);
  }, [nodes, edges, dimensions]);

  const idToNode = useMemo(() => {
    const map = new Map<number, SimNode>();
    simNodes.forEach((n) => map.set(n.id, n));
    return map;
  }, [simNodes]);

  const handleWheel = useCallback((e: React.WheelEvent<SVGSVGElement>) => {
    e.preventDefault();
    const factor = e.deltaY < 0 ? 1.12 : 0.89;
    const svg = svgRef.current;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    const mouseX = e.clientX - rect.left;
    const mouseY = e.clientY - rect.top;
    setTransform((t) => {
      const newScale = Math.max(0.15, Math.min(4, t.scale * factor));
      const ratio = newScale / t.scale;
      return {
        x: mouseX - (mouseX - t.x) * ratio,
        y: mouseY - (mouseY - t.y) * ratio,
        scale: newScale,
      };
    });
  }, []);

  const handleMouseDown = useCallback((e: React.MouseEvent<SVGSVGElement>) => {
    if ((e.target as SVGElement).closest(".graph-node")) return;
    isPanning.current = true;
    panStart.current = { x: e.clientX, y: e.clientY, tx: transform.x, ty: transform.y };
  }, [transform]);

  const handleMouseMove = useCallback((e: React.MouseEvent<SVGSVGElement>) => {
    if (!isPanning.current) return;
    const dx = e.clientX - panStart.current.x;
    const dy = e.clientY - panStart.current.y;
    setTransform((t) => ({ ...t, x: panStart.current.tx + dx, y: panStart.current.ty + dy }));
  }, []);

  const handleMouseUp = useCallback(() => {
    isPanning.current = false;
  }, []);

  const resetView = useCallback(() => setTransform({ x: 0, y: 0, scale: 1 }), []);

  if (nodes.length === 0) {
    return (
      <div className="flex h-full items-center justify-center text-muted-foreground/50">
        <div className="text-center">
          <Network className="h-12 w-12 mx-auto mb-3" />
          <p className="text-sm">No entries to visualize</p>
          <p className="text-xs mt-1">Create entries with [[backlinks]] to see connections</p>
        </div>
      </div>
    );
  }

  const backlinkCount = edges.filter((e) => e.type === "backlink" || !e.type).length;
  const semanticCount = edges.filter((e) => e.type === "semantic").length;

  // Tag legend entries (sorted by node count desc)
  const tagLegend = useMemo(() => {
    const counts: Record<string, number> = {};
    simNodes.forEach((n) => { const t = n.tags[0]; if (t) counts[t] = (counts[t] ?? 0) + 1; });
    return Object.entries(counts)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 10)
      .map(([tag, count]) => ({ tag, count, color: tagColors[tag] ?? "#6b7280" }));
  }, [simNodes, tagColors]);

  return (
    <div ref={containerRef} className="relative w-full h-full select-none">
      {/* Zoom controls */}
      <div className="absolute top-3 right-3 z-10 flex flex-col gap-1">
        <button
          onClick={() => setTransform((t) => ({ ...t, scale: Math.min(4, t.scale * 1.3) }))}
          className="rounded-lg border border-foreground/[0.08] bg-black/60 backdrop-blur-sm p-2 text-muted-foreground hover:text-foreground transition-colors"
        >
          <ZoomIn className="h-3.5 w-3.5" />
        </button>
        <button
          onClick={() => setTransform((t) => ({ ...t, scale: Math.max(0.15, t.scale / 1.3) }))}
          className="rounded-lg border border-foreground/[0.08] bg-black/60 backdrop-blur-sm p-2 text-muted-foreground hover:text-foreground transition-colors"
        >
          <ZoomOut className="h-3.5 w-3.5" />
        </button>
        <button
          onClick={resetView}
          className="rounded-lg border border-foreground/[0.08] bg-black/60 backdrop-blur-sm p-2 text-muted-foreground hover:text-foreground transition-colors"
        >
          <Maximize2 className="h-3.5 w-3.5" />
        </button>
      </div>

      {/* Bottom-left: tag legend + edge legend */}
      <div className="absolute bottom-3 left-3 z-10 flex flex-col gap-2">
        {/* Tag color legend */}
        <div className="rounded-lg border border-foreground/[0.08] bg-black/60 backdrop-blur-sm px-3 py-2">
          <p className="text-[9px] font-semibold uppercase tracking-widest text-muted-foreground/50 mb-1.5">Tags</p>
          <div className="flex flex-col gap-1">
            {tagLegend.map(({ tag, count, color }) => (
              <button
                key={tag}
                onClick={() => setSelectedTag(selectedTag === tag ? null : tag)}
                className="flex items-center gap-2 text-left group"
              >
                <span
                  className="h-2 w-2 rounded-full shrink-0 transition-transform group-hover:scale-125"
                  style={{ background: color, boxShadow: selectedTag === tag ? `0 0 6px ${color}` : "none" }}
                />
                <span className={`text-[10px] transition-colors ${selectedTag === tag ? "text-foreground font-medium" : "text-muted-foreground/60 group-hover:text-muted-foreground"}`}>
                  {tag}
                </span>
                <span className="ml-auto text-[9px] text-muted-foreground/30">{count}</span>
              </button>
            ))}
          </div>
        </div>
        {/* Edge type legend */}
        <div className="rounded-lg border border-foreground/[0.08] bg-black/60 backdrop-blur-sm px-3 py-2 flex flex-col gap-1">
          {backlinkCount > 0 && (
            <div className="flex items-center gap-2">
              <svg width="20" height="6"><line x1="0" y1="3" x2="20" y2="3" stroke="#818cf8" strokeWidth="1.5" /></svg>
              <span className="text-[10px] text-muted-foreground/50">Link ({backlinkCount})</span>
            </div>
          )}
          {semanticCount > 0 && (
            <div className="flex items-center gap-2">
              <svg width="20" height="6"><line x1="0" y1="3" x2="20" y2="3" stroke="#34d399" strokeWidth="1.5" strokeDasharray="3 2" /></svg>
              <span className="text-[10px] text-muted-foreground/50">Semantic ({semanticCount})</span>
            </div>
          )}
          <p className="text-[9px] text-muted-foreground/30 mt-0.5">Scroll · Drag</p>
        </div>
      </div>

      {/* Hover tooltip — follows cursor feel via top-left panel */}
      <AnimatePresence>
        {hoveredNode && !selectedTag && (
          <motion.div
            key={hoveredNode.id}
            initial={{ opacity: 0, y: -4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.1 }}
            className="absolute top-3 left-3 z-10 max-w-[200px] rounded-xl border border-foreground/[0.08] bg-black/80 backdrop-blur-md p-3 pointer-events-none"
          >
            <p className="text-xs font-semibold leading-snug text-foreground">{hoveredNode.title}</p>
            {hoveredNode.tags[0] && (
              <span
                className="mt-1.5 inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px] font-medium"
                style={{
                  background: `${tagColors[hoveredNode.tags[0]] ?? "#6b7280"}22`,
                  color: tagColors[hoveredNode.tags[0]] ?? "#6b7280",
                }}
              >
                #{hoveredNode.tags[0]}
              </span>
            )}
            <p className="mt-1.5 text-[10px] text-muted-foreground/50">
              {hoveredNode.degree} link{hoveredNode.degree !== 1 ? "s" : ""} · click to open
            </p>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Main SVG */}
      <svg
        ref={svgRef}
        width="100%"
        height="100%"
        onWheel={handleWheel}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseUp}
        onClick={() => setSelectedTag(null)}
        className={isPanning.current ? "cursor-grabbing" : "cursor-grab"}
      >
        <defs>
          <filter id="glow-sm" x="-60%" y="-60%" width="220%" height="220%">
            <feGaussianBlur stdDeviation="2.5" result="b" />
            <feComposite in="SourceGraphic" in2="b" operator="over" />
          </filter>
          <filter id="glow-lg" x="-100%" y="-100%" width="300%" height="300%">
            <feGaussianBlur stdDeviation="5" result="b" />
            <feComposite in="SourceGraphic" in2="b" operator="over" />
          </filter>
        </defs>

        <g transform={`translate(${transform.x},${transform.y}) scale(${transform.scale})`}>
          {/* Edges — Obsidian-style: subtle gray web */}
          {edges.map((edge, i) => {
            const s = idToNode.get(edge.source);
            const t = idToNode.get(edge.target);
            if (!s || !t) return null;
            const isSemantic = edge.type === "semantic";
            const isHoveredEdge = hoveredNode?.id === edge.source || hoveredNode?.id === edge.target;
            const sTag = s.tags[0];
            const tTag = t.tags[0];
            const tagMatch = !selectedTag || sTag === selectedTag || tTag === selectedTag;
            // Gray base, color on hover
            const color = isHoveredEdge
              ? (isSemantic ? "#34d399" : "#818cf8")
              : "#ffffff";
            const opacity = !tagMatch ? 0.02
              : isHoveredEdge ? 0.9
              : isSemantic ? 0.1 : 0.14;
            const sw = isHoveredEdge ? 1.5 : 0.6;
            return (
              <line
                key={i}
                x1={s.x} y1={s.y} x2={t.x} y2={t.y}
                stroke={color}
                strokeOpacity={opacity}
                strokeWidth={sw}
                strokeDasharray={isSemantic && !isHoveredEdge ? "3 2" : undefined}
                filter={isHoveredEdge ? "url(#glow-sm)" : undefined}
              />
            );
          })}

          {/* Nodes — Obsidian: tiny dots, size = connection count */}
          {simNodes.map((node) => {
            const tag = node.tags[0];
            const color = tagColors[tag] ?? "#9ca3af";
            const isHovered = hoveredNode?.id === node.id;
            const isSelected = selectedTag === tag;
            const isDimmed = selectedTag !== null && !isSelected;
            // Obsidian: base 3px, scales with connections, max 16px
            const baseR = Math.max(3, Math.min(16, 3 + node.degree * 1.6 + (node.size ?? 0) * 0.2));
            const r = isHovered ? baseR + 2.5 : baseR;
            const nodeOpacity = isDimmed ? 0.08 : 1;
            const showLabel = isHovered || isSelected;
            const label = node.title.length > 22 ? node.title.substring(0, 20) + "…" : node.title;
            return (
              <g
                key={node.id}
                transform={`translate(${node.x},${node.y})`}
                opacity={nodeOpacity}
                onClick={() => { if (!isDimmed) onNodeClick(node.id); }}
                onMouseEnter={() => setHoveredNode(node)}
                onMouseLeave={() => setHoveredNode(null)}
                style={{ cursor: isDimmed ? "default" : "pointer" }}
              >
                {isHovered && (
                  <circle r={r + 6} fill={color} fillOpacity={0.25} filter="url(#glow-lg)" />
                )}
                <circle
                  r={r}
                  fill={color}
                  fillOpacity={isHovered ? 1 : isDimmed ? 0.4 : 0.9}
                  stroke={isHovered ? "white" : "none"}
                  strokeWidth={1}
                  strokeOpacity={0.5}
                />
                {showLabel && (
                  <text
                    y={r + 11}
                    textAnchor="middle"
                    fill="white"
                    fillOpacity={isHovered ? 1 : 0.7}
                    fontSize={isHovered ? 10 : 9}
                    fontWeight={isHovered ? 600 : 400}
                    className="pointer-events-none"
                    style={{ userSelect: "none" }}
                  >
                    {label}
                  </text>
                )}
              </g>
            );
          })}
        </g>
      </svg>

      {/* Tag detail panel (slides in from right when tag selected) */}
      <AnimatePresence>
        {selectedTag && (
          <motion.div
            key={`panel-${selectedTag}`}
            initial={{ opacity: 0, x: 20 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: 20 }}
            transition={{ duration: 0.18 }}
            className="absolute right-0 top-0 h-full w-60 border-l border-foreground/[0.08] bg-black/80 backdrop-blur-md flex flex-col z-20"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between px-4 py-3 border-b border-foreground/[0.06]">
              <div className="flex items-center gap-2 min-w-0">
                <span
                  className="h-2.5 w-2.5 rounded-full shrink-0"
                  style={{ background: tagColors[selectedTag] ?? "#6b7280", boxShadow: `0 0 6px ${tagColors[selectedTag] ?? "#6b7280"}` }}
                />
                <span className="text-sm font-semibold truncate">#{selectedTag}</span>
                <span className="rounded-full bg-foreground/[0.08] px-1.5 py-0.5 text-[10px] text-muted-foreground shrink-0">
                  {selectedTagNodes.length}
                </span>
              </div>
              <button
                onClick={() => setSelectedTag(null)}
                className="rounded p-1 text-muted-foreground/40 hover:text-foreground hover:bg-foreground/[0.06]"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
            <div className="flex-1 overflow-y-auto py-1">
              {selectedTagNodes.map((n) => (
                <button
                  key={n.id}
                  onClick={() => onNodeClick(n.id)}
                  className="w-full text-left px-4 py-2.5 hover:bg-white/[0.04] transition-colors group"
                >
                  <p className="text-[11px] font-medium text-foreground/75 group-hover:text-foreground truncate leading-snug">
                    {n.title}
                  </p>
                  {n.degree > 0 && (
                    <p className="text-[10px] text-muted-foreground/35 mt-0.5">
                      {n.degree} connection{n.degree !== 1 ? "s" : ""}
                    </p>
                  )}
                </button>
              ))}
            </div>
            <div className="px-4 py-2 border-t border-foreground/[0.06]">
              <p className="text-[9px] text-muted-foreground/30 text-center">click entry to open · click elsewhere to close</p>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

"use client";

import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import dynamic from "next/dynamic";
import { motion, AnimatePresence } from "framer-motion";
import {
  BookOpen, Plus, Search, Tag, Network, FileText,
  Trash2, ArrowLeft, Clock, User, Link2, X, Hash,
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
import type { VaultGraph } from "@/lib/api";
import { useConfirm, useToast } from "@/components/ui/dialog-provider";

// Reuse the Second-Brain 3D graph renderer (WebGL + automatic 2D fallback) for the
// Knowledge base — same component, just fed with the knowledge graph. Client-only.
const VaultGraph3D = dynamic(() => import("@/app/second-brains/vault-graph-3d"), {
  ssr: false,
});

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

  // Map the knowledge graph onto the shared VaultGraph shape so the Second-Brain
  // 3D renderer can display it: colour by primary tag, size by node.size.
  const vaultGraph: VaultGraph | undefined = useMemo(() => {
    if (!graphData) return undefined;
    return {
      nodes: graphData.nodes.map((n) => ({
        id: String(n.id),
        name: n.title,
        path: "",
        folder: n.tags?.[0] ?? "",   // drives node colour (grouped by primary tag)
        tags: n.tags ?? [],
        in: 0,
        out: 0,
        degree: Math.max(1, n.size ?? 1),
      })),
      edges: graphData.edges.map((e) => ({ source: String(e.source), target: String(e.target) })),
    };
  }, [graphData]);

  return (
    <div className="flex h-full min-h-0 flex-col gap-6 p-6">
      {/* Header */}
      <div className="flex flex-col items-start gap-3 pl-10 sm:flex-row sm:items-center sm:justify-between sm:pl-0 lg:pl-0">
        <div className="flex min-w-0 items-center gap-3">
          {viewMode !== "list" && (
            <button
              onClick={() => setViewMode(viewMode === "editor" ? previousView : "list")}
              className="rounded-lg p-2 text-muted-foreground hover:bg-foreground/[0.04] hover:text-foreground"
            >
              <ArrowLeft className="h-4 w-4" />
            </button>
          )}
          <BookOpen className="h-6 w-6 text-primary" />
          <h1 className="truncate text-xl font-semibold">Knowledge Base</h1>
          <span className="shrink-0 rounded-full bg-foreground/[0.06] px-2.5 py-0.5 text-xs text-muted-foreground">
            {entries.length} entries
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-2">
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
                  <div className="prose prose-sm dark:prose-invert max-w-none prose-pre:bg-foreground/5 prose-pre:text-foreground/80">
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
            className="h-[72dvh] rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm overflow-hidden relative"
          >
            {vaultGraph && (
              <VaultGraph3D
                externalGraph={vaultGraph}
                onNodeSelect={(n) => openGraphPreview(Number(n.id))}
              />
            )}

            {/* Reading panel — absolutely positioned so graph size never changes */}
            <AnimatePresence>
              {graphPreview && (
                <motion.div
                  key={graphPreview.id}
                  initial={{ opacity: 0, x: 24 }}
                  animate={{ opacity: 1, x: 0 }}
                  exit={{ opacity: 0, x: 24 }}
                  transition={{ duration: 0.18 }}
                  className="absolute right-0 top-0 h-full w-80 border-l border-foreground/[0.08] bg-card/90 dark:bg-black/80 backdrop-blur-md flex flex-col z-20"
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
                    <div className="prose prose-sm dark:prose-invert max-w-none prose-p:text-muted-foreground/80 prose-headings:text-foreground prose-strong:text-foreground prose-code:text-primary prose-pre:bg-foreground/5 prose-pre:text-foreground/80">
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

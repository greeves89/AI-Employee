"use client";

import { useState, useEffect, useCallback, useRef, useMemo } from "react";
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
  getKnowledgeGraph,
} from "@/lib/api";
import type { KnowledgeEntry, KnowledgeTag, KnowledgeGraphNode, KnowledgeGraphEdge } from "@/lib/types";

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
      setViewMode("editor");
    } catch (e) {
      console.error("Failed to load entry:", e);
    }
  }, []);

  const openNewEntry = useCallback(() => {
    setSelectedEntry(null);
    setEditTitle("");
    setEditContent("");
    setEditTags("");
    setIsNew(true);
    setViewMode("editor");
    setShowPreview(false);
  }, []);

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
    if (!confirm("Delete this entry?")) return;
    try {
      await deleteKnowledgeEntry(id);
      setViewMode("list");
      setSelectedEntry(null);
      refresh();
    } catch (e) {
      console.error("Failed to delete:", e);
    }
  }, [refresh]);

  const openGraph = useCallback(async () => {
    try {
      const data = await getKnowledgeGraph();
      setGraphData(data);
      setViewMode("graph");
    } catch (e) {
      console.error("Failed to load graph:", e);
    }
  }, []);

  // Navigate backlinks
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
              onClick={() => setViewMode("list")}
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
            className="flex flex-1 gap-6 min-h-0"
          >
            {/* Main list */}
            <div className="flex flex-1 flex-col gap-4 min-h-0">
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

              {/* Active tag filter */}
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

              {/* Entry list */}
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
            {/* Editor header */}
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

            {/* Tags input */}
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

            {/* Editor / Preview */}
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

            {/* Backlinks panel */}
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
            className="flex-1 min-h-0 rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm overflow-hidden"
          >
            <ForceGraph
              nodes={graphData.nodes}
              edges={graphData.edges}
              onNodeClick={(id) => openEntry(id)}
            />
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

// --- Markdown renderer with [[backlink]] support ---

function BacklinkMarkdown({ content, onBacklinkClick }: { content: string; onBacklinkClick: (title: string) => void }) {
  // Replace [[Title]] with clickable links before rendering
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

// --- Force-directed graph visualization (pure SVG, no dependencies) ---

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
}

function ForceGraph({ nodes, edges, onNodeClick }: ForceGraphProps) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [simNodes, setSimNodes] = useState<SimNode[]>([]);
  const [dimensions, setDimensions] = useState({ width: 800, height: 500 });
  const [hoveredNode, setHoveredNode] = useState<number | null>(null);
  const animRef = useRef<number>(0);
  const simRef = useRef<SimNode[]>([]);

  // Observe container size
  useEffect(() => {
    if (!svgRef.current?.parentElement) return;
    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        setDimensions({
          width: entry.contentRect.width,
          height: entry.contentRect.height,
        });
      }
    });
    observer.observe(svgRef.current.parentElement);
    return () => observer.disconnect();
  }, []);

  // Initialize simulation
  useEffect(() => {
    if (nodes.length === 0) return;

    const cx = dimensions.width / 2;
    const cy = dimensions.height / 2;
    const initialNodes: SimNode[] = nodes.map((n, i) => ({
      ...n,
      x: cx + (Math.random() - 0.5) * 200,
      y: cy + (Math.random() - 0.5) * 200,
      vx: 0,
      vy: 0,
    }));
    simRef.current = initialNodes;

    const idToIdx = new Map<number, number>();
    nodes.forEach((n, i) => idToIdx.set(n.id, i));

    let iteration = 0;
    const maxIterations = 300;

    function tick() {
      const sn = simRef.current;
      if (iteration >= maxIterations) {
        setSimNodes([...sn]);
        return;
      }
      const alpha = 1 - iteration / maxIterations;
      const k = alpha * 0.1;

      // Center gravity
      for (const n of sn) {
        n.vx += (cx - n.x) * k * 0.01;
        n.vy += (cy - n.y) * k * 0.01;
      }

      // Repulsion between all nodes
      for (let i = 0; i < sn.length; i++) {
        for (let j = i + 1; j < sn.length; j++) {
          const dx = sn[j].x - sn[i].x;
          const dy = sn[j].y - sn[i].y;
          const dist = Math.max(Math.sqrt(dx * dx + dy * dy), 1);
          const force = (k * 2000) / (dist * dist);
          const fx = (dx / dist) * force;
          const fy = (dy / dist) * force;
          sn[i].vx -= fx;
          sn[i].vy -= fy;
          sn[j].vx += fx;
          sn[j].vy += fy;
        }
      }

      // Attraction along edges
      for (const edge of edges) {
        const si = idToIdx.get(edge.source);
        const ti = idToIdx.get(edge.target);
        if (si === undefined || ti === undefined) continue;
        const dx = sn[ti].x - sn[si].x;
        const dy = sn[ti].y - sn[si].y;
        const dist = Math.max(Math.sqrt(dx * dx + dy * dy), 1);
        const force = (dist - 120) * k * 0.05;
        const fx = (dx / dist) * force;
        const fy = (dy / dist) * force;
        sn[si].vx += fx;
        sn[si].vy += fy;
        sn[ti].vx -= fx;
        sn[ti].vy -= fy;
      }

      // Apply velocities with damping
      for (const n of sn) {
        n.vx *= 0.6;
        n.vy *= 0.6;
        n.x += n.vx;
        n.y += n.vy;
        // Clamp to bounds
        n.x = Math.max(40, Math.min(dimensions.width - 40, n.x));
        n.y = Math.max(40, Math.min(dimensions.height - 40, n.y));
      }

      iteration++;
      setSimNodes([...sn]);
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

  // Color by first tag
  const tagColors: Record<string, string> = {};
  const palette = [
    "#3b82f6", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6",
    "#ec4899", "#06b6d4", "#f97316", "#14b8a6", "#a855f7",
  ];
  let colorIdx = 0;
  nodes.forEach((n) => {
    const tag = n.tags[0];
    if (tag && !tagColors[tag]) {
      tagColors[tag] = palette[colorIdx % palette.length];
      colorIdx++;
    }
  });

  return (
    <svg ref={svgRef} width="100%" height="100%" className="cursor-grab">
      {/* Edges */}
      {edges.map((edge, i) => {
        const s = idToNode.get(edge.source);
        const t = idToNode.get(edge.target);
        if (!s || !t) return null;
        const isHighlighted = hoveredNode === edge.source || hoveredNode === edge.target;
        return (
          <line
            key={i}
            x1={s.x}
            y1={s.y}
            x2={t.x}
            y2={t.y}
            stroke={isHighlighted ? "#3b82f6" : "currentColor"}
            strokeOpacity={isHighlighted ? 0.6 : 0.1}
            strokeWidth={isHighlighted ? 2 : 1}
          />
        );
      })}
      {/* Nodes */}
      {simNodes.map((node) => {
        const r = Math.min(Math.max(node.size + 4, 6), 20);
        const color = tagColors[node.tags[0]] || "#6b7280";
        const isHovered = hoveredNode === node.id;
        return (
          <g
            key={node.id}
            transform={`translate(${node.x},${node.y})`}
            onClick={() => onNodeClick(node.id)}
            onMouseEnter={() => setHoveredNode(node.id)}
            onMouseLeave={() => setHoveredNode(null)}
            className="cursor-pointer"
          >
            <circle
              r={isHovered ? r + 3 : r}
              fill={color}
              fillOpacity={isHovered ? 0.8 : 0.3}
              stroke={color}
              strokeWidth={isHovered ? 2 : 1}
              strokeOpacity={isHovered ? 1 : 0.5}
            />
            <text
              y={r + 14}
              textAnchor="middle"
              fill="currentColor"
              fillOpacity={isHovered ? 0.9 : 0.5}
              fontSize={isHovered ? 12 : 10}
              fontWeight={isHovered ? 600 : 400}
            >
              {node.title.length > 20 ? node.title.substring(0, 18) + "..." : node.title}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

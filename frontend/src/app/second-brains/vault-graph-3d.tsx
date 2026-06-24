"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ForceGraph3D from "react-force-graph-3d";
import SpriteText from "three-spritetext";
import {
  ArrowLeftRight,
  ExternalLink,
  Hash,
  Loader2,
  Network,
  X,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import { getVaultGraph, getBrainFile } from "@/lib/api";
import type { VaultGraph, VaultGraphNode } from "@/lib/api";

// react-force-graph's prop surface is broad and loosely typed — cast once so the
// JSX below stays readable instead of fighting generics.
const FG = ForceGraph3D as unknown as (props: Record<string, unknown>) => JSX.Element;

const PALETTE = [
  "#60a5fa", "#34d399", "#f472b6", "#fbbf24", "#a78bfa", "#22d3ee",
  "#fb7185", "#4ade80", "#facc15", "#c084fc", "#38bdf8", "#f59e0b",
];
const ROOT_COLOR = "#94a3b8";

export default function VaultGraph3D({
  brainId,
  onOpenFile,
}: {
  brainId: number;
  onOpenFile: (path: string) => void;
}) {
  const fgRef = useRef<any>(null);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const [graph, setGraph] = useState<VaultGraph | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [size, setSize] = useState({ w: 800, h: 600 });

  const [selected, setSelected] = useState<VaultGraphNode | null>(null);
  const [detail, setDetail] = useState<{ content: string; loading: boolean }>({
    content: "",
    loading: false,
  });

  // Load the graph for this vault.
  useEffect(() => {
    let alive = true;
    setLoading(true);
    getVaultGraph(brainId)
      .then((g) => {
        if (alive) {
          setGraph(g);
          setErr(null);
        }
      })
      .catch((e) => {
        if (alive) setErr(e instanceof Error ? e.message : "Graph konnte nicht geladen werden");
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [brainId]);

  // Keep the canvas sized to its container.
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const update = () => setSize({ w: el.clientWidth, h: el.clientHeight });
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Stable folder → color map (assigned in first-seen order).
  const folderColor = useMemo(() => {
    const map = new Map<string, string>();
    let i = 0;
    for (const n of graph?.nodes ?? []) {
      const f = n.folder || "";
      if (map.has(f)) continue;
      map.set(f, f === "" ? ROOT_COLOR : PALETTE[i % PALETTE.length]);
      if (f !== "") i++;
    }
    return map;
  }, [graph]);

  // Undirected adjacency for the detail panel's "linked notes".
  const neighbors = useMemo(() => {
    const m = new Map<string, Set<string>>();
    const add = (a: string, b: string) => {
      if (!m.has(a)) m.set(a, new Set());
      m.get(a)!.add(b);
    };
    for (const e of graph?.edges ?? []) {
      add(e.source, e.target);
      add(e.target, e.source);
    }
    return m;
  }, [graph]);

  const nodeById = useMemo(() => {
    const m = new Map<string, VaultGraphNode>();
    for (const n of graph?.nodes ?? []) m.set(n.id, n);
    return m;
  }, [graph]);

  // Clone — react-force-graph mutates node/link objects (positions, refs).
  const data = useMemo(
    () => ({
      nodes: (graph?.nodes ?? []).map((n) => ({ ...n })),
      links: (graph?.edges ?? []).map((e) => ({ ...e })),
    }),
    [graph],
  );

  // Add a bloom glow and fit the view once the graph is mounted.
  useEffect(() => {
    if (!fgRef.current || loading || !graph || graph.nodes.length === 0) return;
    let cancelled = false;
    (async () => {
      try {
        // @ts-expect-error — three's example modules ship no bundled type declarations
        const { UnrealBloomPass } = await import("three/examples/jsm/postprocessing/UnrealBloomPass.js");
        const composer = fgRef.current?.postProcessingComposer?.();
        if (composer && !cancelled) {
          const bloom = new UnrealBloomPass();
          (bloom as any).strength = 1.1;
          (bloom as any).radius = 0.55;
          (bloom as any).threshold = 0.05;
          composer.addPass(bloom);
        }
      } catch {
        /* bloom is a nicety — graph renders fine without it */
      }
    })();
    const t = setTimeout(() => {
      try {
        fgRef.current?.zoomToFit?.(800, 60);
      } catch {
        /* noop */
      }
    }, 600);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  }, [loading, graph]);

  const focusNode = useCallback((node: any) => {
    const dist = 120;
    const hyp = Math.hypot(node.x || 1, node.y || 1, node.z || 1) || 1;
    const r = 1 + dist / hyp;
    try {
      fgRef.current?.cameraPosition(
        { x: (node.x || 0) * r, y: (node.y || 0) * r, z: (node.z || 0) * r },
        node,
        1200,
      );
    } catch {
      /* noop */
    }
  }, []);

  const handleNodeClick = useCallback(
    (node: any) => {
      setSelected(node as VaultGraphNode);
      focusNode(node);
      setDetail({ content: "", loading: true });
      getBrainFile(brainId, node.path)
        .then((r) => setDetail({ content: r.content, loading: false }))
        .catch(() => setDetail({ content: "_Inhalt konnte nicht geladen werden._", loading: false }));
    },
    [brainId, focusNode],
  );

  const nodeThreeObject = useCallback((node: any) => {
    const sprite: any = new SpriteText(node.name);
    sprite.color = "rgba(235,240,255,0.92)";
    sprite.textHeight = Math.min(8, 3 + node.degree * 0.5);
    sprite.fontFace = "Inter, system-ui, sans-serif";
    sprite.position.set(0, -(4 + Math.cbrt(1 + node.degree) * 2), 0);
    return sprite;
  }, []);

  const selectedNeighbors = selected ? Array.from(neighbors.get(selected.id) ?? []) : [];

  return (
    <div ref={wrapRef} className="relative h-full w-full overflow-hidden bg-[#06060c]">
      {loading ? (
        <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
          <Loader2 className="mr-2 h-5 w-5 animate-spin" /> Graph wird geladen…
        </div>
      ) : err ? (
        <div className="flex h-full items-center justify-center text-sm text-red-400">{err}</div>
      ) : !graph || graph.nodes.length === 0 ? (
        <div className="flex h-full flex-col items-center justify-center gap-2 text-sm text-muted-foreground/60">
          <Network className="h-8 w-8 opacity-40" />
          Noch keine Notizen im Vault.
        </div>
      ) : (
        <>
          <FG
            ref={fgRef}
            width={size.w}
            height={size.h}
            graphData={data}
            backgroundColor="#06060c"
            showNavInfo={false}
            nodeLabel={(n: any) =>
              `${n.name}${n.folder ? ` · ${n.folder}` : ""} · ${n.degree} ↔`
            }
            nodeColor={(n: any) => folderColor.get(n.folder || "") || ROOT_COLOR}
            nodeVal={(n: any) => 1 + n.degree * 1.4}
            nodeRelSize={4}
            nodeOpacity={0.92}
            nodeResolution={16}
            nodeThreeObjectExtend={true}
            nodeThreeObject={nodeThreeObject}
            linkColor={() => "rgba(140,160,220,0.22)"}
            linkWidth={0.6}
            linkOpacity={0.5}
            linkDirectionalParticles={1}
            linkDirectionalParticleWidth={1.4}
            linkDirectionalParticleSpeed={0.006}
            onNodeClick={handleNodeClick}
            onBackgroundClick={() => setSelected(null)}
          />

          {/* Stats */}
          <div className="pointer-events-none absolute left-3 top-3 rounded-lg border border-white/10 bg-black/40 px-3 py-1.5 text-[11px] text-white/60 backdrop-blur-sm">
            {graph.nodes.length} Notizen · {graph.edges.length} Verknüpfungen
            {graph.truncated && " · (gekürzt)"}
          </div>

          {/* Folder legend */}
          {folderColor.size > 1 && (
            <div className="pointer-events-none absolute bottom-3 left-3 max-w-[45%] rounded-lg border border-white/10 bg-black/40 px-3 py-2 backdrop-blur-sm">
              <div className="mb-1 text-[10px] uppercase tracking-wide text-white/40">Ordner</div>
              <div className="flex flex-wrap gap-x-3 gap-y-1">
                {Array.from(folderColor.entries()).map(([f, c]) => (
                  <span key={f} className="flex items-center gap-1 text-[10px] text-white/70">
                    <span className="h-2 w-2 rounded-full" style={{ background: c }} />
                    {f || "(Wurzel)"}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Detail panel */}
          {selected && (
            <div className="absolute right-0 top-0 flex h-full w-80 flex-col border-l border-white/10 bg-[#0b0b14]/95 backdrop-blur-md">
              <div className="flex items-start justify-between gap-2 border-b border-white/10 px-4 py-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-1.5 text-sm font-semibold text-white">
                    <span
                      className="h-2.5 w-2.5 shrink-0 rounded-full"
                      style={{ background: folderColor.get(selected.folder || "") || ROOT_COLOR }}
                    />
                    <span className="truncate">{selected.name}</span>
                  </div>
                  <div className="mt-0.5 truncate font-mono text-[10px] text-white/40">{selected.path}</div>
                </div>
                <button onClick={() => setSelected(null)} className="shrink-0 text-white/40 hover:text-white">
                  <X className="h-4 w-4" />
                </button>
              </div>

              <div className="flex items-center gap-3 border-b border-white/10 px-4 py-2 text-[11px] text-white/60">
                <span className="flex items-center gap-1">
                  <ArrowLeftRight className="h-3 w-3" /> {selected.in}← / →{selected.out}
                </span>
                {selected.folder && <span className="rounded bg-white/5 px-1.5 py-0.5">{selected.folder}</span>}
              </div>

              {selected.tags.length > 0 && (
                <div className="flex flex-wrap gap-1 border-b border-white/10 px-4 py-2">
                  {selected.tags.map((t) => (
                    <span
                      key={t}
                      className="flex items-center gap-0.5 rounded bg-emerald-500/10 px-1.5 py-0.5 text-[10px] text-emerald-300"
                    >
                      <Hash className="h-2.5 w-2.5" />
                      {t}
                    </span>
                  ))}
                </div>
              )}

              {selectedNeighbors.length > 0 && (
                <div className="border-b border-white/10 px-4 py-2">
                  <div className="mb-1 text-[10px] uppercase tracking-wide text-white/40">
                    Verlinkte Notizen ({selectedNeighbors.length})
                  </div>
                  <div className="flex flex-col gap-0.5">
                    {selectedNeighbors.slice(0, 14).map((id) => {
                      const n = nodeById.get(id);
                      if (!n) return null;
                      return (
                        <button
                          key={id}
                          onClick={() => handleNodeClick(n)}
                          className="flex items-center gap-1.5 truncate rounded px-1.5 py-0.5 text-left text-[11px] text-white/70 hover:bg-white/5"
                        >
                          <span
                            className="h-1.5 w-1.5 shrink-0 rounded-full"
                            style={{ background: folderColor.get(n.folder || "") || ROOT_COLOR }}
                          />
                          <span className="truncate">{n.name}</span>
                        </button>
                      );
                    })}
                  </div>
                </div>
              )}

              <div className="prose prose-invert prose-sm flex-1 overflow-auto px-4 py-3 text-[13px]">
                {detail.loading ? (
                  <div className="flex items-center gap-2 text-white/40">
                    <Loader2 className="h-4 w-4 animate-spin" /> lädt…
                  </div>
                ) : (
                  <ReactMarkdown>{detail.content.slice(0, 4000) || "_leer_"}</ReactMarkdown>
                )}
              </div>

              <div className="border-t border-white/10 p-3">
                <button
                  onClick={() => onOpenFile(selected.path)}
                  className="flex w-full items-center justify-center gap-1.5 rounded-lg bg-primary px-3 py-2 text-xs font-medium text-primary-foreground hover:opacity-90"
                >
                  <ExternalLink className="h-3.5 w-3.5" /> Im Editor öffnen
                </button>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

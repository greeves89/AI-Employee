"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { WheelEvent as ReactWheelEvent } from "react";
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

// WebGL probe. react-force-graph-3d renders via three.js/WebGL; in locked-down
// environments (clinic VDI, GPU-blacklisted or policy-disabled browsers) context
// creation fails and the render loop crashes with "reading 'tick'" on a black
// canvas. We detect that up front and fall back to a dependency-free 2D graph.
let _webglSupported: boolean | null = null;
function webglSupported(): boolean {
  if (_webglSupported !== null) return _webglSupported;
  if (typeof document === "undefined") return true; // SSR: decided again on client
  try {
    const c = document.createElement("canvas");
    const gl =
      c.getContext("webgl2") ||
      c.getContext("webgl") ||
      c.getContext("experimental-webgl");
    _webglSupported = !!gl;
  } catch {
    _webglSupported = false;
  }
  return _webglSupported;
}

export default function VaultGraph3D({
  brainId,
  onOpenFile,
  externalGraph,
  onNodeSelect,
}: {
  brainId?: number;
  onOpenFile?: (path: string) => void;
  // Reuse this renderer for a non-vault graph (e.g. the Knowledge base): pass the
  // graph directly instead of fetching a vault, and handle node clicks yourself.
  externalGraph?: VaultGraph;
  onNodeSelect?: (node: VaultGraphNode) => void;
}) {
  const fgRef = useRef<any>(null);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const [graph, setGraph] = useState<VaultGraph | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [size, setSize] = useState({ w: 800, h: 600 });
  // Decide the renderer once, on the client: 3D (WebGL) if available, else 2D.
  const [webgl] = useState(() => webglSupported());
  // WebGL contexts leak/exhaust after repeated opens (three.js) and can be lost
  // at runtime (GPU reset, tab backgrounded). When that happens we switch to the
  // 2D renderer live instead of leaving a black, crashing canvas.
  const [ctxLost, setCtxLost] = useState(false);
  const use3D = webgl && !ctxLost;

  const [selected, setSelected] = useState<VaultGraphNode | null>(null);
  const [detail, setDetail] = useState<{ content: string; loading: boolean }>({
    content: "",
    loading: false,
  });

  // Load the graph for this vault.
  useEffect(() => {
    let alive = true;
    // Caller supplied the graph directly (Knowledge base) → don't fetch a vault.
    if (externalGraph) {
      setGraph(externalGraph);
      setErr(null);
      setLoading(false);
      return () => { alive = false; };
    }
    if (brainId == null) {
      setLoading(false);
      return () => { alive = false; };
    }
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
  }, [brainId, externalGraph]);

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

  // Detect a lost/failed WebGL context on the three.js canvas and fall back to
  // 2D live. Also pause the render loop on unmount to release the context sooner
  // (repeated opens otherwise exhaust the browser's WebGL context budget).
  useEffect(() => {
    if (!use3D || loading || !graph || graph.nodes.length === 0) return;
    const wrap = wrapRef.current;
    if (!wrap) return;
    let canvas: HTMLCanvasElement | null = null;
    const onLost = (e: Event) => {
      e.preventDefault();
      setCtxLost(true);
    };
    // The canvas is created by react-force-graph after mount — grab it next tick.
    const t = setTimeout(() => {
      canvas = wrap.querySelector("canvas");
      canvas?.addEventListener("webglcontextlost", onLost, false);
    }, 0);
    return () => {
      clearTimeout(t);
      canvas?.removeEventListener("webglcontextlost", onLost, false);
      try {
        fgRef.current?.pauseAnimation?.();
      } catch {
        /* best-effort */
      }
    };
  }, [use3D, loading, graph]);

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
    // Spread nodes apart: much stronger charge repulsion + longer links than the
    // d3 defaults (-30 / 30), so bubbles breathe instead of clumping.
    try {
      fgRef.current.d3Force?.("charge")?.strength(-220).distanceMax(900);
      fgRef.current.d3Force?.("link")?.distance(90);
      fgRef.current.d3ReheatSimulation?.();
    } catch {
      /* force tuning is best-effort */
    }
    (async () => {
      try {
        // @ts-expect-error — three's example modules ship no bundled type declarations
        const { UnrealBloomPass } = await import("three/examples/jsm/postprocessing/UnrealBloomPass.js");
        const composer = fgRef.current?.postProcessingComposer?.();
        if (composer && !cancelled) {
          const bloom = new UnrealBloomPass();
          // Subtle glow: only the brightest cores bloom (high threshold), gentle
          // strength — otherwise every sphere blows out into white mush.
          (bloom as any).strength = 0.45;
          (bloom as any).radius = 0.4;
          (bloom as any).threshold = 0.3;
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
      // Non-vault caller (Knowledge base) handles the click itself (opens the entry).
      if (onNodeSelect) {
        onNodeSelect(node as VaultGraphNode);
        return;
      }
      if (brainId == null) return;
      setDetail({ content: "", loading: true });
      getBrainFile(brainId, node.path)
        .then((r) => setDetail({ content: r.content, loading: false }))
        .catch(() => setDetail({ content: "_Inhalt konnte nicht geladen werden._", loading: false }));
    },
    [brainId, focusNode, onNodeSelect],
  );

  const nodeThreeObject = useCallback((node: any) => {
    const sprite: any = new SpriteText(node.name);
    sprite.color = "rgba(226,232,240,0.95)";
    // Dark chip behind the text keeps labels crisp instead of dissolving in the glow.
    sprite.backgroundColor = "rgba(6,6,14,0.6)";
    sprite.borderRadius = 2;
    sprite.padding = 1.6;
    sprite.textHeight = Math.min(6, 2.6 + node.degree * 0.4);
    sprite.fontFace = "Inter, system-ui, sans-serif";
    // Sit the label clear BELOW the sphere surface (radius-aware) + a comfortable
    // gap — matches the nodeVal/nodeRelSize sizing so big bubbles don't overlap.
    const sphereRadius = 4 * Math.cbrt(1 + node.degree * 1.4);
    sprite.position.set(0, -(sphereRadius + 7), 0);
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
          {use3D ? (
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
          ) : (
            <VaultGraph2D
              nodes={data.nodes as any}
              links={data.links as any}
              width={size.w}
              height={size.h}
              colorOf={(f) => folderColor.get(f || "") || ROOT_COLOR}
              selectedId={selected?.id ?? null}
              neighbors={neighbors}
              onNodeClick={handleNodeClick}
              onBackgroundClick={() => setSelected(null)}
            />
          )}

          {/* Stats */}
          <div className="pointer-events-none absolute left-3 top-3 rounded-lg border border-white/10 bg-black/40 px-3 py-1.5 text-[11px] text-white/60 backdrop-blur-sm">
            {graph.nodes.length} Notizen · {graph.edges.length} Verknüpfungen
            {graph.truncated && " · (gekürzt)"}
            {!use3D && " · 2D-Ansicht"}
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
          {selected && !onNodeSelect && (
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
                  onClick={() => onOpenFile?.(selected.path)}
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

// --------------------------------------------------------------------------- //
// 2D fallback (no WebGL). Hand-rolled force layout rendered as SVG — same
// pattern as the knowledge-graph view. Feeds the SAME onNodeClick/detail panel
// as the 3D graph, so behaviour is identical minus the third dimension.
// --------------------------------------------------------------------------- //

interface Sim2DNode {
  id: string;
  name: string;
  folder: string;
  degree: number;
  path: string;
  x: number;
  y: number;
  vx: number;
  vy: number;
}

function VaultGraph2D({
  nodes,
  links,
  width,
  height,
  colorOf,
  selectedId,
  neighbors,
  onNodeClick,
  onBackgroundClick,
}: {
  nodes: VaultGraphNode[];
  links: { source: string; target: string }[];
  width: number;
  height: number;
  colorOf: (folder: string) => string;
  selectedId: string | null;
  neighbors: Map<string, Set<string>>;
  onNodeClick: (n: VaultGraphNode) => void;
  onBackgroundClick: () => void;
}) {
  const [sim, setSim] = useState<Sim2DNode[]>([]);
  const [tf, setTf] = useState({ x: 0, y: 0, scale: 1 });
  const animRef = useRef<number>(0);
  const simRef = useRef<Sim2DNode[]>([]);
  const svgRef = useRef<SVGSVGElement>(null);
  const panning = useRef(false);
  const panStart = useRef({ x: 0, y: 0, tx: 0, ty: 0 });

  // Run the layout whenever data or canvas size changes, then settle.
  useEffect(() => {
    cancelAnimationFrame(animRef.current);
    if (nodes.length === 0 || width === 0 || height === 0) return;

    const idToIdx = new Map<string, number>();
    nodes.forEach((n, i) => idToIdx.set(n.id, i));

    const cx = width / 2;
    const cy = height / 2;
    const spread = Math.min(width, height) * 0.35;
    const angle = (2 * Math.PI) / nodes.length;

    const sn: Sim2DNode[] = nodes.map((n, i) => ({
      id: n.id,
      name: n.name,
      folder: n.folder,
      degree: n.degree,
      path: n.path,
      x: cx + spread * Math.cos(i * angle) + (Math.random() - 0.5) * 40,
      y: cy + spread * Math.sin(i * angle) + (Math.random() - 0.5) * 40,
      vx: 0,
      vy: 0,
    }));
    simRef.current = sn;

    const L = links
      .map((l) => ({ s: idToIdx.get(l.source), t: idToIdx.get(l.target) }))
      .filter((l): l is { s: number; t: number } => l.s !== undefined && l.t !== undefined);

    let iter = 0;
    const MAX_ITER = 400;
    const REPULSION = 2200;
    const LINK_DIST = 62;
    const DAMPING = 0.5;
    const GRAVITY = 0.02;

    function tick() {
      const s = simRef.current;
      if (iter >= MAX_ITER) {
        setSim([...s]);
        return;
      }
      const alpha = Math.max(0.005, 1 - iter / MAX_ITER);
      const k = alpha * 0.2;

      for (const n of s) {
        n.vx += (cx - n.x) * k * GRAVITY;
        n.vy += (cy - n.y) * k * GRAVITY;
      }
      for (let i = 0; i < s.length; i++) {
        for (let j = i + 1; j < s.length; j++) {
          const dx = s[j].x - s[i].x || 0.01;
          const dy = s[j].y - s[i].y || 0.01;
          const d2 = dx * dx + dy * dy;
          const d = Math.sqrt(d2) || 0.01;
          const f = (REPULSION * k) / d2;
          const fx = (dx / d) * f;
          const fy = (dy / d) * f;
          s[i].vx -= fx;
          s[i].vy -= fy;
          s[j].vx += fx;
          s[j].vy += fy;
        }
      }
      for (const l of L) {
        const a = s[l.s];
        const b = s[l.t];
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        const d = Math.hypot(dx, dy) || 0.01;
        const f = (d - LINK_DIST) * k * 0.5;
        const fx = (dx / d) * f;
        const fy = (dy / d) * f;
        a.vx += fx;
        a.vy += fy;
        b.vx -= fx;
        b.vy -= fy;
      }
      for (const n of s) {
        n.vx *= DAMPING;
        n.vy *= DAMPING;
        n.x += n.vx;
        n.y += n.vy;
      }
      iter++;
      if (iter % 3 === 0) setSim([...s]);
      animRef.current = requestAnimationFrame(tick);
    }
    animRef.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(animRef.current);
  }, [nodes, links, width, height]);

  // Fit the settled layout into view once.
  useEffect(() => {
    if (sim.length === 0 || width === 0 || height === 0) return;
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const n of sim) {
      minX = Math.min(minX, n.x);
      minY = Math.min(minY, n.y);
      maxX = Math.max(maxX, n.x);
      maxY = Math.max(maxY, n.y);
    }
    const gw = maxX - minX || 1;
    const gh = maxY - minY || 1;
    const scale = Math.min(2, Math.max(0.2, 0.85 * Math.min(width / gw, height / gh)));
    const x = width / 2 - ((minX + maxX) / 2) * scale;
    const y = height / 2 - ((minY + maxY) / 2) * scale;
    setTf({ x, y, scale });
    // Only refit when the graph identity changes, not on every settle frame.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sim.length, width, height]);

  const posById = useMemo(() => {
    const m = new Map<string, Sim2DNode>();
    for (const n of sim) m.set(n.id, n);
    return m;
  }, [sim]);

  const onWheel = useCallback((e: ReactWheelEvent<SVGSVGElement>) => {
    e.preventDefault();
    setTf((t) => {
      const factor = e.deltaY < 0 ? 1.12 : 1 / 1.12;
      const rect = svgRef.current?.getBoundingClientRect();
      const mx = rect ? e.clientX - rect.left : 0;
      const my = rect ? e.clientY - rect.top : 0;
      const scale = Math.min(4, Math.max(0.15, t.scale * factor));
      // zoom toward the cursor
      return {
        scale,
        x: mx - ((mx - t.x) / t.scale) * scale,
        y: my - ((my - t.y) / t.scale) * scale,
      };
    });
  }, []);

  const highlight = useMemo(() => {
    if (!selectedId) return null;
    const set = new Set<string>([selectedId]);
    Array.from(neighbors.get(selectedId) ?? []).forEach((id) => set.add(id));
    return set;
  }, [selectedId, neighbors]);

  return (
    <svg
      ref={svgRef}
      width={width}
      height={height}
      className="absolute inset-0 cursor-grab active:cursor-grabbing"
      onWheel={onWheel}
      onMouseDown={(e) => {
        panning.current = true;
        panStart.current = { x: e.clientX, y: e.clientY, tx: tf.x, ty: tf.y };
      }}
      onMouseMove={(e) => {
        if (!panning.current) return;
        setTf((t) => ({
          ...t,
          x: panStart.current.tx + (e.clientX - panStart.current.x),
          y: panStart.current.ty + (e.clientY - panStart.current.y),
        }));
      }}
      onMouseUp={() => (panning.current = false)}
      onMouseLeave={() => (panning.current = false)}
      onClick={onBackgroundClick}
    >
      <g transform={`translate(${tf.x},${tf.y}) scale(${tf.scale})`}>
        {links.map((l, i) => {
          const a = posById.get(l.source);
          const b = posById.get(l.target);
          if (!a || !b) return null;
          const active = highlight ? highlight.has(l.source) && highlight.has(l.target) : false;
          return (
            <line
              key={i}
              x1={a.x}
              y1={a.y}
              x2={b.x}
              y2={b.y}
              stroke={active ? "rgba(96,165,250,0.7)" : "rgba(140,160,220,0.22)"}
              strokeWidth={active ? 1.4 : 0.6}
            />
          );
        })}
        {sim.map((n) => {
          const r = 4 + n.degree * 1.4;
          const dimmed = highlight ? !highlight.has(n.id) : false;
          return (
            <g
              key={n.id}
              transform={`translate(${n.x},${n.y})`}
              className="cursor-pointer"
              opacity={dimmed ? 0.25 : 1}
              onClick={(e) => {
                e.stopPropagation();
                onNodeClick(n as unknown as VaultGraphNode);
              }}
            >
              <circle
                r={r}
                fill={colorOf(n.folder)}
                stroke={n.id === selectedId ? "#fff" : "rgba(6,6,14,0.6)"}
                strokeWidth={n.id === selectedId ? 1.6 : 0.8}
              />
              <text
                x={0}
                y={r + 8}
                textAnchor="middle"
                fontSize={Math.min(9, 5 + n.degree * 0.5)}
                fill="rgba(226,232,240,0.9)"
                style={{ pointerEvents: "none", userSelect: "none" }}
              >
                {n.name.length > 22 ? n.name.slice(0, 21) + "…" : n.name}
              </text>
            </g>
          );
        })}
      </g>
    </svg>
  );
}

"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import {
  ReactFlow, Background, Controls, MiniMap, Handle, Position,
  useNodesState, useEdgesState, addEdge,
  type Node, type Edge, type Connection, type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import {
  ArrowLeft, Save, Play, Loader2, Bot, GitBranch, Clock, Plus, Trash2, X, CheckCircle2, XCircle,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useToast } from "@/components/ui/dialog-provider";
import * as api from "@/lib/api";
import type { Agent } from "@/lib/types";

// ── Node data shape ─────────────────────────────────────────────────────────
type StepData = {
  kind: "agent_task" | "condition" | "wait";
  title?: string;
  prompt?: string;
  agent_id?: string | null;
  seconds?: number;
  check?: { step: string; op: string; value?: string };
  active?: boolean;   // highlighted during a run
  stepTitles?: Record<string, string>;   // id -> display label, injected for rendering only
};

const OPS = [
  { v: "not_empty", l: "ist nicht leer" },
  { v: "contains", l: "enthält" },
  { v: "equals", l: "ist gleich" },
  { v: "not_equals", l: "ist ungleich" },
  { v: "is_empty", l: "ist leer" },
];

// ── Custom nodes ────────────────────────────────────────────────────────────
function NodeShell({ selected, active, color, icon, title, children }: { selected?: boolean; active?: boolean; color: string; icon: React.ReactNode; title: string; children?: React.ReactNode }) {
  return (
    <div className={cn(
      "min-w-[190px] max-w-[240px] rounded-xl border bg-card px-3 py-2.5 shadow-md transition-all",
      selected ? "border-primary ring-2 ring-primary/30" : "border-foreground/[0.12]",
      active && "ring-2 ring-amber-400 border-amber-400",
    )}>
      <div className="flex items-center gap-2">
        <span className={cn("flex h-6 w-6 items-center justify-center rounded-md", color)}>{icon}</span>
        <span className="truncate text-[13px] font-semibold">{title}</span>
      </div>
      {children}
    </div>
  );
}

function AgentTaskNode({ data, selected }: NodeProps) {
  const d = data as StepData;
  return (
    <>
      <Handle type="target" position={Position.Top} className="!bg-primary" />
      <NodeShell selected={selected} active={d.active} color="bg-blue-500/15 text-blue-400" icon={<Bot className="h-3.5 w-3.5" />} title={d.title || "Agenten-Aufgabe"}>
        <p className="mt-1 line-clamp-2 text-[11px] text-muted-foreground/70">{d.prompt || "kein Prompt"}</p>
      </NodeShell>
      <Handle type="source" position={Position.Bottom} className="!bg-primary" />
    </>
  );
}

function ConditionNode({ data, selected }: NodeProps) {
  const d = data as StepData;
  const c = d.check;
  return (
    <>
      <Handle type="target" position={Position.Top} className="!bg-amber-400" />
      <NodeShell selected={selected} active={d.active} color="bg-amber-500/15 text-amber-400" icon={<GitBranch className="h-3.5 w-3.5" />} title={d.title || "Bedingung"}>
        <p className="mt-1 text-[11px] text-muted-foreground/70">
          {c && c.step
            ? `${d.stepTitles?.[c.step] ?? c.step} ${OPS.find((o) => o.v === c.op)?.l ?? c.op}${c.value ? ` „${c.value}"` : ""}`
            : "keine Bedingung"}
        </p>
        <div className="mt-1 flex justify-between text-[10px]"><span className="text-emerald-400">ja ↙</span><span className="text-red-400">nein ↘</span></div>
      </NodeShell>
      <Handle id="true" type="source" position={Position.Bottom} style={{ left: "25%" }} className="!bg-emerald-400" />
      <Handle id="false" type="source" position={Position.Bottom} style={{ left: "75%" }} className="!bg-red-400" />
    </>
  );
}

function WaitNode({ data, selected }: NodeProps) {
  const d = data as StepData;
  return (
    <>
      <Handle type="target" position={Position.Top} className="!bg-zinc-400" />
      <NodeShell selected={selected} active={d.active} color="bg-zinc-500/20 text-zinc-300" icon={<Clock className="h-3.5 w-3.5" />} title={d.title || "Warten"}>
        <p className="mt-1 text-[11px] text-muted-foreground/70">{d.seconds ?? 0}s warten</p>
      </NodeShell>
      <Handle type="source" position={Position.Bottom} className="!bg-zinc-400" />
    </>
  );
}

const nodeTypes = { agent_task: AgentTaskNode, condition: ConditionNode, wait: WaitNode };

// ── definition <-> graph mapping ────────────────────────────────────────────
function defToGraph(def: api.WorkflowDefinition): { nodes: Node[]; edges: Edge[] } {
  const nodes: Node[] = [];
  const edges: Edge[] = [];
  const steps = def.steps || {};
  Object.entries(steps).forEach(([id, s], i) => {
    nodes.push({
      id,
      type: s.type,
      position: s._pos ?? { x: 240, y: 100 + i * 130 },
      data: { kind: s.type, title: s.title, prompt: s.prompt, agent_id: s.agent_id, seconds: s.seconds, check: s.check } as StepData,
    });
    if (s.type === "condition") {
      if (s.true) edges.push({ id: `${id}-t`, source: id, target: s.true, sourceHandle: "true", label: "ja", animated: false });
      if (s.false) edges.push({ id: `${id}-f`, source: id, target: s.false, sourceHandle: "false", label: "nein" });
    } else if (s.next) {
      edges.push({ id: `${id}-n`, source: id, target: s.next });
    }
  });
  return { nodes, edges };
}

function graphToDef(nodes: Node[], edges: Edge[], prevStart: string | null): api.WorkflowDefinition {
  const steps: Record<string, api.WorkflowStep> = {};
  for (const n of nodes) {
    const d = n.data as StepData;
    const out = edges.filter((e) => e.source === n.id);
    const step: api.WorkflowStep = { type: d.kind, title: d.title, _pos: n.position };
    if (d.kind === "agent_task") {
      step.prompt = d.prompt ?? "";
      step.agent_id = d.agent_id || null;
      step.next = out[0]?.target ?? null;
    } else if (d.kind === "wait") {
      step.seconds = d.seconds ?? 0;
      step.next = out[0]?.target ?? null;
    } else if (d.kind === "condition") {
      step.check = d.check ?? { step: "", op: "not_empty" };
      step.true = out.find((e) => e.sourceHandle === "true")?.target ?? null;
      step.false = out.find((e) => e.sourceHandle === "false")?.target ?? null;
    }
    steps[n.id] = step;
  }
  // start = node without an incoming edge (fallback: previous start if still present, else first)
  const targets = new Set(edges.map((e) => e.target));
  const roots = nodes.filter((n) => !targets.has(n.id)).map((n) => n.id);
  const start = roots[0] ?? (prevStart && steps[prevStart] ? prevStart : nodes[0]?.id ?? null);
  return { start, steps };
}

let _idCounter = 0;
const newId = () => `n${Date.now().toString(36)}${_idCounter++}`;

export default function WorkflowEditorPage() {
  const params = useParams();
  const router = useRouter();
  const wfId = params.id as string;
  const toast = useToast();

  const [wf, setWf] = useState<api.Workflow | null>(null);
  const [name, setName] = useState("");
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [saving, setSaving] = useState(false);
  const [running, setRunning] = useState(false);
  const [run, setRun] = useState<api.WorkflowRun | null>(null);
  const startRef = useRef<string | null>(null);
  const runPoll = useRef<ReturnType<typeof setInterval> | undefined>(undefined);

  useEffect(() => {
    api.getAgents("all").then((d) => setAgents(d.agents)).catch(() => {});
    api.getWorkflow(wfId).then((w) => {
      setWf(w); setName(w.name); startRef.current = w.definition?.start ?? null;
      const g = defToGraph(w.definition);
      setNodes(g.nodes); setEdges(g.edges);
    }).catch(() => toast.error("Workflow nicht gefunden"));
    return () => clearInterval(runPoll.current);
  }, [wfId, setNodes, setEdges, toast]);

  const onConnect = useCallback((c: Connection) => setEdges((eds) => {
    // a source handle may only have one outgoing edge (per branch) — replace it
    const filtered = eds.filter((e) => !(e.source === c.source && e.sourceHandle === c.sourceHandle));
    return addEdge({ ...c, label: c.sourceHandle === "true" ? "ja" : c.sourceHandle === "false" ? "nein" : undefined }, filtered);
  }), [setEdges]);

  const addNode = (kind: StepData["kind"]) => {
    const id = newId();
    const titles = { agent_task: "Agenten-Aufgabe", condition: "Bedingung", wait: "Warten" };
    setNodes((ns) => [...ns, {
      id, type: kind,
      position: { x: 120 + Math.random() * 200, y: 120 + Math.random() * 200 },
      data: { kind, title: titles[kind], ...(kind === "agent_task" ? { prompt: "" } : kind === "wait" ? { seconds: 60 } : { check: { step: "", op: "not_empty" } }) } as StepData,
    }]);
    setSelectedId(id);
  };

  const patchNode = (id: string, patch: Partial<StepData>) =>
    setNodes((ns) => ns.map((n) => n.id === id ? { ...n, data: { ...(n.data as StepData), ...patch } } : n));

  const deleteNode = (id: string) => {
    setNodes((ns) => ns.filter((n) => n.id !== id));
    setEdges((es) => es.filter((e) => e.source !== id && e.target !== id));
    setSelectedId(null);
  };

  const save = useCallback(async () => {
    setSaving(true);
    try {
      const def = graphToDef(nodes, edges, startRef.current);
      const updated = await api.updateWorkflow(wfId, { name, definition: def, enabled: wf?.enabled ?? true });
      startRef.current = def.start;
      setWf(updated);
      toast.success("Gespeichert.");
    } catch (e) {
      toast.error("Speichern fehlgeschlagen", e instanceof Error ? e.message : undefined);
    } finally {
      setSaving(false);
    }
  }, [nodes, edges, name, wfId, wf, toast]);

  const runNow = useCallback(async () => {
    setRunning(true);
    try {
      // save first so the run uses the current canvas
      const def = graphToDef(nodes, edges, startRef.current);
      await api.updateWorkflow(wfId, { name, definition: def, enabled: true });
      startRef.current = def.start;
      const r = await api.runWorkflow(wfId);
      setRun(r);
      setSelectedId(null); // switch the side panel from node-config to the run status
      clearInterval(runPoll.current);
      runPoll.current = setInterval(async () => {
        try {
          const rr = await api.getWorkflowRun(r.id);
          setRun(rr);
          if (rr.status !== "running") { clearInterval(runPoll.current); setRunning(false); }
        } catch { /* ignore */ }
      }, 3000);
    } catch (e) {
      toast.error("Start fehlgeschlagen", e instanceof Error ? e.message : undefined);
      setRunning(false);
    }
  }, [nodes, edges, name, wfId, toast]);

  // highlight the currently-running step
  useEffect(() => {
    const cur = run?.status === "running" ? run.current_step : null;
    setNodes((ns) => ns.map((n) => ({ ...n, data: { ...(n.data as StepData), active: n.id === cur } })));
  }, [run, setNodes]);

  const selected = useMemo(() => nodes.find((n) => n.id === selectedId), [nodes, selectedId]);
  // Auto-generated ids (e.g. "nmscwadin1") aren't meaningful on their own — resolve them to the
  // node's title everywhere they're displayed (condition dropdown + on-canvas condition summary).
  const stepTitles = useMemo(() => {
    const m: Record<string, string> = {};
    nodes.forEach((n) => { m[n.id] = (n.data as StepData).title || n.id; });
    return m;
  }, [nodes]);
  const renderNodes = useMemo(
    () => nodes.map((n) => ({ ...n, data: { ...(n.data as StepData), stepTitles } })),
    [nodes, stepTitles],
  );
  const stepOptions = nodes.map((n) => ({ id: n.id, label: stepTitles[n.id] !== n.id ? `${stepTitles[n.id]} (${n.id})` : n.id }));
  const readOnly = wf?.role === "viewer";

  return (
    <div className="flex h-[calc(100dvh)] flex-col">
      {/* Toolbar */}
      <div className="flex items-center gap-3 border-b border-foreground/[0.06] px-4 py-2.5">
        <button onClick={() => router.push("/workflows")} className="rounded-lg p-1.5 text-muted-foreground hover:bg-foreground/[0.06]"><ArrowLeft className="h-4 w-4" /></button>
        <input value={name} onChange={(e) => setName(e.target.value)} className="rounded-lg bg-transparent px-2 py-1 text-sm font-semibold outline-none focus:bg-foreground/[0.04]" />
        <div className="ml-auto flex items-center gap-2">
          {readOnly ? (
            <span className="rounded-lg bg-sky-500/10 px-3 py-1.5 text-[12px] text-sky-400">Nur Ansehen — geteilt</span>
          ) : (
            <>
              <div className="mr-1 flex items-center gap-1">
                <PaletteBtn icon={<Bot className="h-3.5 w-3.5" />} label="Aufgabe" onClick={() => addNode("agent_task")} />
                <PaletteBtn icon={<GitBranch className="h-3.5 w-3.5" />} label="Bedingung" onClick={() => addNode("condition")} />
                <PaletteBtn icon={<Clock className="h-3.5 w-3.5" />} label="Warten" onClick={() => addNode("wait")} />
              </div>
              <button onClick={save} disabled={saving} className="inline-flex items-center gap-1.5 rounded-lg bg-foreground/[0.06] px-3 py-1.5 text-sm hover:bg-foreground/[0.1] disabled:opacity-50">
                {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />} Speichern
              </button>
              <button onClick={runNow} disabled={running} className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50">
                {running ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />} Ausführen
              </button>
            </>
          )}
        </div>
      </div>

      <div className="flex min-h-0 flex-1">
        <div className="min-w-0 flex-1">
          <ReactFlow
            nodes={renderNodes} edges={edges}
            onNodesChange={onNodesChange} onEdgesChange={onEdgesChange} onConnect={onConnect}
            nodeTypes={nodeTypes}
            onNodeClick={(_, n) => setSelectedId(n.id)}
            onPaneClick={() => setSelectedId(null)}
            fitView
            proOptions={{ hideAttribution: true }}
            colorMode="dark"
          >
            <Background />
            <Controls />
            <MiniMap pannable zoomable className="!bg-card" />
          </ReactFlow>
        </div>

        {/* Config / run panel */}
        <div className="w-80 shrink-0 overflow-y-auto border-l border-foreground/[0.06] p-4">
          {selected ? (
            <NodeConfig node={selected} agents={agents} stepOptions={stepOptions.filter((s) => s.id !== selected.id)} onChange={(p) => patchNode(selected.id, p)} onDelete={() => deleteNode(selected.id)} onClose={() => setSelectedId(null)} />
          ) : run ? (
            <RunPanel run={run} stepTitles={stepTitles} />
          ) : (
            <div className="text-sm text-muted-foreground/60">
              <p className="mb-2 font-medium text-foreground">Workflow bauen</p>
              <p className="text-xs leading-relaxed">Füge oben Bausteine hinzu (Aufgabe, Bedingung, Warten), verbinde sie per Ziehen zwischen den Punkten, und klicke einen Baustein an, um ihn zu konfigurieren. „Ausführen" startet einen Lauf und zeigt den Fortschritt.</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function PaletteBtn({ icon, label, onClick }: { icon: React.ReactNode; label: string; onClick: () => void }) {
  return (
    <button onClick={onClick} className="inline-flex items-center gap-1 rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-2 py-1.5 text-[11px] hover:bg-foreground/[0.06]">
      <Plus className="h-3 w-3" />{icon}{label}
    </button>
  );
}

function NodeConfig({ node, agents, stepOptions, onChange, onDelete, onClose }: { node: Node; agents: Agent[]; stepOptions: { id: string; label: string }[]; onChange: (p: Partial<StepData>) => void; onDelete: () => void; onClose: () => void }) {
  const d = node.data as StepData;
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <span className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground/60">
          {d.kind === "agent_task" ? "Agenten-Aufgabe" : d.kind === "condition" ? "Bedingung" : "Warten"}
        </span>
        <button onClick={onClose} className="text-muted-foreground/40 hover:text-foreground"><X className="h-4 w-4" /></button>
      </div>
      <Field label="Titel"><input value={d.title ?? ""} onChange={(e) => onChange({ title: e.target.value })} className={inp} /></Field>

      {d.kind === "agent_task" && (
        <>
          <Field label="Agent">
            <select value={d.agent_id ?? ""} onChange={(e) => onChange({ agent_id: e.target.value || null })} className={inp}>
              <option value="">Auto-zuweisen</option>
              {agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
            </select>
          </Field>
          <Field label="Prompt (nutze {{schritt_id}} für Ergebnisse)">
            <textarea value={d.prompt ?? ""} onChange={(e) => onChange({ prompt: e.target.value })} className={cn(inp, "h-28 resize-none")} />
          </Field>
        </>
      )}

      {d.kind === "wait" && (
        <Field label="Sekunden"><input type="number" min={0} value={d.seconds ?? 0} onChange={(e) => onChange({ seconds: parseInt(e.target.value || "0", 10) })} className={inp} /></Field>
      )}

      {d.kind === "condition" && (
        <>
          <Field label="Prüfe Ergebnis von Schritt">
            <select value={d.check?.step ?? ""} onChange={(e) => onChange({ check: { ...(d.check ?? { op: "not_empty" }), step: e.target.value } })} className={inp}>
              <option value="">— wählen —</option>
              {stepOptions.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
            </select>
          </Field>
          <Field label="Operator">
            <select value={d.check?.op ?? "not_empty"} onChange={(e) => onChange({ check: { step: d.check?.step ?? "", op: e.target.value, value: d.check?.value } })} className={inp}>
              {OPS.map((o) => <option key={o.v} value={o.v}>{o.l}</option>)}
            </select>
          </Field>
          {["contains", "equals", "not_equals"].includes(d.check?.op ?? "") && (
            <Field label="Wert"><input value={d.check?.value ?? ""} onChange={(e) => onChange({ check: { step: d.check?.step ?? "", op: d.check?.op ?? "contains", value: e.target.value } })} className={inp} /></Field>
          )}
          <p className="text-[11px] text-muted-foreground/50">Grüner Punkt = „ja"-Zweig, roter = „nein". Ziehe von ihnen zum nächsten Baustein.</p>
        </>
      )}

      <button onClick={onDelete} className="mt-2 inline-flex items-center gap-1.5 rounded-lg border border-red-500/20 bg-red-500/5 px-3 py-1.5 text-[12px] text-red-400 hover:bg-red-500/10">
        <Trash2 className="h-3.5 w-3.5" /> Baustein löschen
      </button>
    </div>
  );
}

function RunPanel({ run, stepTitles }: { run: api.WorkflowRun; stepTitles: Record<string, string> }) {
  const icon = run.status === "completed" ? <CheckCircle2 className="h-4 w-4 text-emerald-400" /> : run.status === "failed" ? <XCircle className="h-4 w-4 text-red-400" /> : <Loader2 className="h-4 w-4 animate-spin text-blue-400" />;
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 text-sm font-medium">{icon}
        {run.status === "completed" ? "Fertig" : run.status === "failed" ? "Fehlgeschlagen" : "Läuft…"}
      </div>
      <div className="text-[12px] text-muted-foreground/70">
        Schritte erledigt: {run.steps_done}{run.current_step ? ` · aktuell: ${stepTitles[run.current_step] ?? run.current_step}` : ""}
      </div>
      {run.error && <div className="rounded-lg border border-red-500/20 bg-red-500/5 p-2 text-[12px] text-red-400">{run.error}</div>}
      {Object.keys(run.context || {}).length > 0 && (
        <div className="space-y-1.5">
          <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground/60">Ergebnisse</p>
          {Object.entries(run.context).map(([sid, v]) => (
            <div key={sid} className="rounded-lg border border-foreground/[0.06] bg-foreground/[0.02] p-2">
              <p className="text-[11px] font-medium text-foreground/80">{stepTitles[sid] ?? sid}</p>
              <p className="mt-0.5 line-clamp-4 text-[11px] text-muted-foreground/70">{v.result || "—"}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

const inp = "w-full rounded-lg border border-foreground/[0.1] bg-foreground/[0.03] px-2.5 py-1.5 text-[13px] outline-none focus:border-primary/40";
function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="block"><span className="mb-1 block text-[11px] text-muted-foreground/60">{label}</span>{children}</label>;
}

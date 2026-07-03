"use client";

/**
 * Kiosk — "AI EMPLOYEE · Mission Control" (tabbed)
 *
 * Local-only fullscreen display for the on-Pi 7" touchscreen (1024x600).
 * Tabs: Übersicht · Agenten (tiles → full detail) · Tasks · System · Einstellungen.
 * Reachable only from the device (Caddy 404s /kiosk for tunnel traffic); no auth.
 *
 * Same-origin, no-auth kiosk API:
 *   GET  /api/v1/kiosk/overview
 *   GET  /api/v1/kiosk/agents/{id}
 *   GET|PUT /api/v1/kiosk/settings
 *   POST /api/v1/kiosk/chat/{id}   ·   GET /api/v1/kiosk/chat/{id}/history?session_id=
 */

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import {
  Activity, Bot, Cpu, Gauge, HardDrive, MemoryStick, Zap, Euro, Clock, Thermometer,
  ListTodo, CircleDot, Send, ArrowLeft, Wifi, WifiOff, LayoutDashboard, Users, Server,
  Settings as SettingsIcon, Coins, Hash, ShieldCheck, Boxes, Save, RefreshCw, Mic,
} from "lucide-react";
import { VoiceSessionModal } from "@/components/agents/voice-session";

type AgentInfo = {
  id: string; name: string; state: string; model: string;
  current_task: string | null; has_container: boolean;
};
type Overview = {
  ts: string;
  agents: AgentInfo[]; agents_working: number; agents_total: number;
  tasks: { running: number; pending: number; done_today: number; recent: { title: string; status: string; agent_id: string | null }[] };
  ai_spend: { cost_usd_today: number; tokens_in_today: number; tokens_out_today: number };
  pi: {
    temp_c: number | null; cpu_percent: number | null; load: number[] | null;
    mem_used_mb: number | null; mem_total_mb: number | null;
    disk_used_gb: number | null; disk_total_gb: number | null; uptime_s: number | null; stale: boolean;
  };
  power: { watts: number | null; today_kwh: number | null; today_cost_eur: number | null; month_cost_eur: number | null; price_eur_kwh: number };
};
type AgentDetail = {
  id: string; name: string; state: string; model: string; mode: string; role: string | null;
  has_container: boolean; autonomy_level: string | null; budget_usd: number | null; created_at: string | null;
  current_task: string | null; tasks_by_status: Record<string, number>; tasks_total: number;
  cost_usd_total: number; tokens_in_total: number; tokens_out_total: number;
  recent_tasks: { title: string; status: string; cost_usd: number | null; ts: string | null }[];
};
type ChatMsg = { role: string; content: string; message_id: string; ts: string | null };
type Tab = "overview" | "agents" | "tasks" | "system" | "settings";

const nf = (n: number, d = 0) => n.toLocaleString("de-DE", { minimumFractionDigits: d, maximumFractionDigits: d });
const lsGet = (k: string, d: number) => { if (typeof window === "undefined") return d; const v = Number(localStorage.getItem(k)); return Number.isFinite(v) && v > 0 ? v : d; };

function fmtUptime(s: number | null): string {
  if (s == null) return "—";
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`; if (h > 0) return `${h}h ${m}m`; return `${m}m`;
}
function tempColor(t: number | null): string {
  if (t == null) return "text-slate-400";
  if (t >= 75) return "text-rose-400"; if (t >= 60) return "text-amber-400"; return "text-emerald-400";
}
function stateColor(s: string): { dot: string; text: string; glow: string } {
  switch (s) {
    case "working": return { dot: "bg-emerald-400", text: "text-emerald-300", glow: "shadow-[0_0_10px_2px_rgba(52,211,153,0.6)]" };
    case "running": case "completed": return { dot: "bg-cyan-400", text: "text-cyan-300", glow: "shadow-[0_0_8px_1px_rgba(34,211,238,0.5)]" };
    case "error": case "failed": return { dot: "bg-rose-500", text: "text-rose-300", glow: "shadow-[0_0_10px_2px_rgba(244,63,94,0.6)]" };
    case "idle": case "pending": case "queued": return { dot: "bg-slate-500", text: "text-slate-300", glow: "" };
    case "stopped": case "cancelled": return { dot: "bg-slate-700", text: "text-slate-500", glow: "" };
    default: return { dot: "bg-violet-400", text: "text-violet-300", glow: "" };
  }
}
const RANK: Record<string, number> = { error: 5, working: 4, running: 3, idle: 1, stopped: 0 };
function rank(s: string): number { return RANK[s] ?? 2; }

export default function KioskPage() {
  const [data, setData] = useState<Overview | null>(null);
  const [online, setOnline] = useState(false);
  const [now, setNow] = useState<Date | null>(null);
  const [tab, setTab] = useState<Tab>("overview");
  const [detailId, setDetailId] = useState<string | null>(null);
  const [chatAgent, setChatAgent] = useState<AgentInfo | null>(null);
  const [voiceAgent, setVoiceAgent] = useState<AgentInfo | null>(null);
  const [idle, setIdle] = useState(false);
  const lastActivity = useRef<number>(Date.now());

  useEffect(() => { const t = setInterval(() => setNow(new Date()), 1000); setNow(new Date()); return () => clearInterval(t); }, []);

  useEffect(() => {
    const wake = () => { lastActivity.current = Date.now(); if (idle) setIdle(false); };
    const evs = ["pointerdown", "keydown", "touchstart", "mousemove"];
    evs.forEach((e) => window.addEventListener(e, wake, { passive: true }));
    const idleMs = lsGet("kiosk_idle_ms", 90000);
    const t = setInterval(() => { if (!chatAgent && !voiceAgent && Date.now() - lastActivity.current > idleMs) setIdle(true); }, 2000);
    return () => { evs.forEach((e) => window.removeEventListener(e, wake)); clearInterval(t); };
  }, [idle, chatAgent, voiceAgent]);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const r = await fetch("/api/v1/kiosk/overview", { cache: "no-store" });
        if (!r.ok) throw new Error();
        const j = (await r.json()) as Overview;
        if (alive) { setData(j); setOnline(true); }
      } catch { if (alive) setOnline(false); }
    };
    load();
    const t = setInterval(load, (idle ? 4 : 1) * lsGet("kiosk_poll_ms", 2500));
    return () => { alive = false; clearInterval(t); };
  }, [idle]);

  // Deep-linking: /kiosk?tab=agents or ?chat=<agentId> (also handy for debugging)
  useEffect(() => {
    if (typeof window === "undefined") return;
    const t = new URLSearchParams(window.location.search).get("tab") as Tab | null;
    if (t) setTab(t);
  }, []);
  useEffect(() => {
    if (typeof window === "undefined" || !data) return;
    const cid = new URLSearchParams(window.location.search).get("chat");
    if (cid && !chatAgent) { const a = data.agents.find((x) => x.id === cid); if (a) setChatAgent(a); }
  }, [data, chatAgent]);

  if (chatAgent) return <ChatOverlay agent={chatAgent} onBack={() => { lastActivity.current = Date.now(); setChatAgent(null); }} />;
  if (idle) return <Screensaver now={now} power={data?.power} temp={data?.pi.temp_c ?? null} working={data?.agents_working ?? 0} />;

  return (
    <div className="kiosk-root h-screen w-screen overflow-hidden kiosk-bg text-slate-100 select-none flex flex-col">
      <KioskStyles />
      {voiceAgent && (
        <VoiceSessionModal
          agentId={voiceAgent.id}
          agentName={voiceAgent.name}
          onClose={() => { lastActivity.current = Date.now(); setVoiceAgent(null); }}
          getTicket={async () => {
            const r = await fetch(`/api/v1/kiosk/ws-ticket/${voiceAgent.id}`, { method: "POST" });
            if (!r.ok) throw new Error("ticket failed");
            return (await r.json()).ticket as string;
          }}
        />
      )}
      <header className="flex items-center justify-between px-4 h-12 border-b border-white/5 bg-gradient-to-r from-[#0a1020] to-[#0a0f1a] shrink-0">
        <div className="flex items-center gap-2">
          <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-cyan-400 to-violet-500 grid place-items-center kiosk-glow"><Bot className="w-4 h-4 text-white" /></div>
          <div className="text-[13px] font-semibold tracking-wide">AI EMPLOYEE</div>
          <div className="text-[9px] uppercase tracking-[0.2em] text-cyan-400/60 hidden sm:block">Mission Control</div>
        </div>
        <div className="flex items-center gap-3">
          <div className={`flex items-center gap-1 text-xs ${online ? "text-emerald-300" : "text-rose-300"}`}>{online ? <Wifi className="w-3.5 h-3.5" /> : <WifiOff className="w-3.5 h-3.5" />}</div>
          <div className={`flex items-center gap-1 ${tempColor(data?.pi.temp_c ?? null)}`}><Thermometer className="w-4 h-4" /><span className="font-semibold tabular-nums">{data?.pi.temp_c != null ? `${nf(data.pi.temp_c, 1)}°` : "—"}</span></div>
          <div className="flex items-center gap-1 text-emerald-300"><Zap className="w-4 h-4" /><span className="font-semibold tabular-nums">{data?.power.watts != null ? `${nf(data.power.watts, 1)}W` : "—"}</span></div>
          <div className="text-lg font-semibold tabular-nums">{now ? now.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" }) : "--:--"}</div>
        </div>
      </header>

      <main className="flex-1 min-h-0 overflow-hidden p-3">
        {tab === "overview" && <OverviewView data={data} onOpenAgent={(id) => { setDetailId(id); setTab("agents"); }} />}
        {tab === "agents" && (detailId
          ? <AgentDetailView id={detailId} onBack={() => setDetailId(null)} onChat={(a) => setChatAgent(a)} onVoice={(a) => setVoiceAgent(a)} />
          : <AgentsView data={data} onOpen={(id) => setDetailId(id)} />)}
        {tab === "tasks" && <TasksView data={data} />}
        {tab === "system" && <SystemView data={data} />}
        {tab === "settings" && <SettingsView />}
      </main>

      <nav className="h-16 shrink-0 border-t border-white/5 bg-[#080b14] grid grid-cols-5">
        {([
          ["overview", "Übersicht", <LayoutDashboard key="i" className="w-5 h-5" />],
          ["agents", "Agenten", <Users key="i" className="w-5 h-5" />],
          ["tasks", "Tasks", <ListTodo key="i" className="w-5 h-5" />],
          ["system", "System", <Server key="i" className="w-5 h-5" />],
          ["settings", "Einstellung", <SettingsIcon key="i" className="w-5 h-5" />],
        ] as [Tab, string, ReactNode][]).map(([id, label, icon]) => (
          <button key={id} onClick={() => { setTab(id); if (id !== "agents") setDetailId(null); }}
            className={`flex flex-col items-center justify-center gap-0.5 transition ${tab === id ? "text-cyan-300 bg-cyan-500/10" : "text-slate-500 active:bg-white/5"}`}>
            {icon}<span className="text-[10px]">{label}</span>
          </button>
        ))}
      </nav>
    </div>
  );
}

/* ------------------------------- Übersicht ------------------------------ */

function OverviewView({ data, onOpenAgent }: { data: Overview | null; onOpenAgent: (id: string) => void }) {
  const pi = data?.pi; const power = data?.power;
  return (
    <div className="h-full grid grid-rows-[auto_1fr] gap-3">
      <div className="grid grid-cols-4 gap-3">
        <BigStat label="Agenten aktiv" value={`${data?.agents_working ?? 0}/${data?.agents_total ?? 0}`} accent="text-cyan-300" icon={<Bot className="w-4 h-4" />} />
        <BigStat label="Tasks laufen" value={data?.tasks.running ?? 0} accent="text-emerald-300" icon={<Activity className="w-4 h-4" />} pulse={(data?.tasks.running ?? 0) > 0} />
        <BigStat label="Heute fertig" value={data?.tasks.done_today ?? 0} accent="text-violet-300" icon={<CircleDot className="w-4 h-4" />} />
        <BigStat label="AI-Kosten heute" value={`$${nf(data?.ai_spend.cost_usd_today ?? 0, 2)}`} accent="text-amber-300" icon={<Euro className="w-4 h-4" />} />
      </div>
      <div className="grid grid-cols-[1fr_320px] gap-3 min-h-0">
        <Panel title="Aktivität" icon={<Activity className="w-4 h-4 text-violet-400" />}>
          <div className="space-y-1.5 overflow-y-auto h-full kiosk-scroll pr-1">
            {(data?.tasks.recent ?? []).length === 0 && <Empty text="Noch keine Tasks" />}
            {(data?.tasks.recent ?? []).map((t, i) => (
              <div key={i} className="flex items-center gap-2 text-sm rounded-lg bg-white/[0.02] px-2.5 py-2">
                <span className={`w-1.5 h-1.5 rounded-full ${stateColor(t.status).dot}`} />
                <span className="truncate flex-1">{t.title || "—"}</span>
                <span className={`text-[10px] uppercase ${stateColor(t.status).text}`}>{t.status}</span>
              </div>
            ))}
          </div>
        </Panel>
        <div className="flex flex-col gap-3 min-h-0">
          <Panel title="Die Möhre" icon={<Cpu className="w-4 h-4 text-emerald-400" />} compact>
            <div className="space-y-2">
              <Bar icon={<Gauge className="w-3.5 h-3.5" />} label="CPU" value={pi?.cpu_percent ?? null} unit="%" color="from-cyan-500 to-emerald-400" />
              <Bar icon={<MemoryStick className="w-3.5 h-3.5" />} label="RAM" value={pi?.mem_used_mb != null && pi?.mem_total_mb ? (pi.mem_used_mb / pi.mem_total_mb) * 100 : null} unit="%" color="from-violet-500 to-fuchsia-400" />
            </div>
          </Panel>
          <div className="flex-1 rounded-xl border border-white/5 bg-gradient-to-br from-emerald-500/10 to-cyan-500/5 p-3 flex flex-col items-center justify-center">
            <div className="text-[11px] uppercase tracking-wider text-slate-400 flex items-center gap-1"><Zap className="w-3.5 h-3.5 text-emerald-300" />Leistung</div>
            <div className="text-5xl font-bold tabular-nums text-emerald-300 kiosk-glow-text my-1">{power?.watts != null ? nf(power.watts, 1) : "—"}<span className="text-xl text-emerald-400/60">W</span></div>
            <div className="text-sm text-slate-300">{power?.today_cost_eur != null ? `${nf(power.today_cost_eur, 3)} € heute` : "—"} · {power?.month_cost_eur != null ? `${nf(power.month_cost_eur, 2)} €/Mon` : "—"}</div>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------- Agenten -------------------------------- */

function AgentsView({ data, onOpen }: { data: Overview | null; onOpen: (id: string) => void }) {
  const agents = (data?.agents ?? []).slice().sort((a, b) => rank(b.state) - rank(a.state));
  return (
    <div className="h-full overflow-y-auto kiosk-scroll">
      {!data && <Empty text="lädt…" />}
      {data && agents.length === 0 && <Empty text="Keine Agenten vorhanden" />}
      <div className="grid grid-cols-2 gap-3">
        {agents.map((a) => {
          const c = stateColor(a.state);
          return (
            <button key={a.id} onClick={() => onOpen(a.id)} className="text-left kiosk-card kiosk-card-hover active:scale-[0.99] transition p-4">
              <div className="flex items-center gap-3 mb-2">
                <div className={`w-14 h-14 rounded-2xl grid place-items-center bg-gradient-to-br from-cyan-500/25 to-violet-500/20 border border-white/10 ${c.glow}`}><span className="text-2xl font-bold">{a.name?.[0]?.toUpperCase() ?? "?"}</span></div>
                <div className="min-w-0 flex-1">
                  <div className="text-lg font-semibold truncate">{a.name}</div>
                  <div className="flex items-center gap-1.5 text-sm"><span className={`w-2.5 h-2.5 rounded-full ${c.dot} ${a.state === "working" ? "animate-pulse" : ""}`} /><span className={c.text}>{a.state}</span></div>
                </div>
              </div>
              <div className="text-sm text-slate-400 truncate">{a.model}</div>
              <div className="text-sm text-slate-500 truncate mt-0.5">{a.current_task || "bereit"}</div>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function AgentDetailView({ id, onBack, onChat, onVoice }: { id: string; onBack: () => void; onChat: (a: AgentInfo) => void; onVoice: (a: AgentInfo) => void }) {
  const [d, setD] = useState<AgentDetail | null>(null);
  useEffect(() => {
    let alive = true;
    const load = async () => { try { const r = await fetch(`/api/v1/kiosk/agents/${id}`, { cache: "no-store" }); if (r.ok && alive) setD(await r.json()); } catch { /* */ } };
    load(); const t = setInterval(load, 3000); return () => { alive = false; clearInterval(t); };
  }, [id]);
  const c = stateColor(d?.state ?? "");
  return (
    <div className="h-full flex flex-col gap-3 overflow-hidden">
      <div className="flex items-center gap-3">
        <button onClick={onBack} className="w-10 h-10 rounded-lg bg-white/5 active:scale-95 grid place-items-center"><ArrowLeft className="w-5 h-5" /></button>
        <div className={`w-11 h-11 rounded-xl grid place-items-center bg-gradient-to-br from-slate-700 to-slate-800 ${c.glow}`}><span className="text-lg font-bold">{d?.name?.[0]?.toUpperCase() ?? "?"}</span></div>
        <div className="flex-1 min-w-0">
          <div className="text-lg font-semibold truncate">{d?.name ?? "…"}</div>
          <div className="flex items-center gap-1.5 text-xs"><span className={`w-2 h-2 rounded-full ${c.dot}`} /><span className={c.text}>{d?.state}</span><span className="text-slate-500">· {d?.model}</span></div>
        </div>
        {d && <button onClick={() => onVoice({ id: d.id, name: d.name, state: d.state, model: d.model, current_task: d.current_task, has_container: d.has_container })}
          className="h-10 px-4 rounded-lg bg-gradient-to-br from-fuchsia-500 to-violet-500 flex items-center gap-2 active:scale-95 font-medium"><Mic className="w-4 h-4" />Sprechen</button>}
        {d && <button onClick={() => onChat({ id: d.id, name: d.name, state: d.state, model: d.model, current_task: d.current_task, has_container: d.has_container })}
          className="h-10 px-4 rounded-lg bg-gradient-to-br from-cyan-500 to-violet-500 flex items-center gap-2 active:scale-95 font-medium"><Send className="w-4 h-4" />Chat</button>}
      </div>

      <div className="flex-1 overflow-y-auto kiosk-scroll grid grid-cols-2 gap-3 auto-rows-min">
        <KV icon={<Boxes className="w-4 h-4" />} label="Modus" value={d?.mode ?? "—"} />
        <KV icon={<ShieldCheck className="w-4 h-4" />} label="Autonomie" value={d?.autonomy_level ?? "—"} />
        <KV icon={<Coins className="w-4 h-4" />} label="Kosten gesamt" value={d ? `$${nf(d.cost_usd_total, 2)}` : "—"} />
        <KV icon={<Hash className="w-4 h-4" />} label="Tokens" value={d ? nf(d.tokens_in_total + d.tokens_out_total) : "—"} />
        <KV icon={<ListTodo className="w-4 h-4" />} label="Tasks gesamt" value={d ? String(d.tasks_total) : "—"} />
        <KV icon={<Server className="w-4 h-4" />} label="Container" value={d ? (d.has_container ? "aktiv" : "keiner") : "—"} />
        {d?.role && <div className="col-span-2"><KV icon={<Users className="w-4 h-4" />} label="Rolle" value={d.role} /></div>}

        <div className="col-span-2 kiosk-card p-3">
          <div className="text-xs font-semibold uppercase tracking-wider text-slate-300 mb-2 flex items-center gap-1.5"><Activity className="w-4 h-4 text-violet-400" />Task-Verteilung</div>
          <div className="flex flex-wrap gap-2">
            {d && Object.entries(d.tasks_by_status).map(([s, n]) => (
              <span key={s} className={`px-2.5 py-1 rounded-full text-xs ${stateColor(s).text} bg-white/[0.04]`}>{s}: <b>{n}</b></span>
            ))}
            {d && Object.keys(d.tasks_by_status).length === 0 && <span className="text-slate-500 text-sm">Noch keine Tasks</span>}
          </div>
        </div>

        <div className="col-span-2 kiosk-card p-3">
          <div className="text-xs font-semibold uppercase tracking-wider text-slate-300 mb-2 flex items-center gap-1.5"><ListTodo className="w-4 h-4 text-cyan-400" />Letzte Tasks</div>
          <div className="space-y-1.5">
            {(d?.recent_tasks ?? []).length === 0 && <span className="text-slate-500 text-sm">—</span>}
            {(d?.recent_tasks ?? []).map((t, i) => (
              <div key={i} className="flex items-center gap-2 text-sm">
                <span className={`w-1.5 h-1.5 rounded-full ${stateColor(t.status).dot}`} />
                <span className="truncate flex-1">{t.title}</span>
                <span className="text-[11px] text-slate-500 tabular-nums">{t.cost_usd != null ? `$${nf(t.cost_usd, 3)}` : ""}</span>
                <span className={`text-[10px] uppercase ${stateColor(t.status).text}`}>{t.status}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

/* -------------------------------- Tasks --------------------------------- */

function TasksView({ data }: { data: Overview | null }) {
  return (
    <div className="h-full grid grid-rows-[auto_1fr] gap-3">
      <div className="grid grid-cols-3 gap-3">
        <BigStat label="Läuft" value={data?.tasks.running ?? 0} accent="text-emerald-300" icon={<Activity className="w-4 h-4" />} pulse={(data?.tasks.running ?? 0) > 0} />
        <BigStat label="Wartet" value={data?.tasks.pending ?? 0} accent="text-amber-300" icon={<ListTodo className="w-4 h-4" />} />
        <BigStat label="Heute fertig" value={data?.tasks.done_today ?? 0} accent="text-cyan-300" icon={<CircleDot className="w-4 h-4" />} />
      </div>
      <Panel title="Letzte Tasks" icon={<ListTodo className="w-4 h-4 text-cyan-400" />}>
        <div className="space-y-1.5 overflow-y-auto h-full kiosk-scroll pr-1">
          {(data?.tasks.recent ?? []).length === 0 && <Empty text="Noch keine Tasks" />}
          {(data?.tasks.recent ?? []).map((t, i) => (
            <div key={i} className="flex items-center gap-2 text-sm rounded-lg bg-white/[0.02] px-3 py-2.5">
              <span className={`w-2 h-2 rounded-full ${stateColor(t.status).dot}`} />
              <span className="truncate flex-1">{t.title || "—"}</span>
              <span className="text-[11px] text-slate-500">{t.agent_id?.slice(0, 8) ?? ""}</span>
              <span className={`text-[10px] uppercase ${stateColor(t.status).text}`}>{t.status}</span>
            </div>
          ))}
        </div>
      </Panel>
    </div>
  );
}

/* -------------------------------- System -------------------------------- */

function SystemView({ data }: { data: Overview | null }) {
  const pi = data?.pi; const power = data?.power;
  return (
    <div className="h-full grid grid-cols-2 gap-3">
      <Panel title="Die Möhre — Auslastung" icon={<Cpu className="w-4 h-4 text-emerald-400" />} right={pi?.stale ? "stale" : ""}>
        <div className="space-y-3">
          <Bar icon={<Gauge className="w-3.5 h-3.5" />} label="CPU" value={pi?.cpu_percent ?? null} unit="%" color="from-cyan-500 to-emerald-400" />
          <Bar icon={<MemoryStick className="w-3.5 h-3.5" />} label="RAM" value={pi?.mem_used_mb != null && pi?.mem_total_mb ? (pi.mem_used_mb / pi.mem_total_mb) * 100 : null} unit="%" color="from-violet-500 to-fuchsia-400" sub={pi?.mem_used_mb != null ? `${nf((pi.mem_used_mb || 0) / 1024, 1)}/${nf((pi.mem_total_mb || 0) / 1024, 1)} GB` : ""} />
          <Bar icon={<HardDrive className="w-3.5 h-3.5" />} label="Disk" value={pi?.disk_used_gb != null && pi?.disk_total_gb ? (pi.disk_used_gb / pi.disk_total_gb) * 100 : null} unit="%" color="from-amber-500 to-orange-400" sub={pi?.disk_used_gb != null ? `${nf(pi.disk_used_gb)}/${nf(pi.disk_total_gb || 0)} GB` : ""} />
          <div className="flex items-center justify-between text-sm pt-1"><span className="flex items-center gap-1.5 text-slate-300"><Thermometer className="w-4 h-4" />Temperatur</span><span className={`font-semibold tabular-nums ${tempColor(pi?.temp_c ?? null)}`}>{pi?.temp_c != null ? `${nf(pi.temp_c, 1)} °C` : "—"}</span></div>
          <div className="flex items-center justify-between text-sm"><span className="flex items-center gap-1.5 text-slate-300"><Clock className="w-4 h-4" />Uptime</span><span className="tabular-nums">{fmtUptime(pi?.uptime_s ?? null)}</span></div>
          <div className="flex items-center justify-between text-sm"><span className="text-slate-300">Load (1/5/15m)</span><span className="tabular-nums">{pi?.load ? pi.load.map((l) => nf(l, 2)).join("  ") : "—"}</span></div>
        </div>
      </Panel>
      <Panel title="Leistung & Stromkosten" icon={<Zap className="w-4 h-4 text-emerald-300" />}>
        <div className="h-full flex flex-col justify-center">
          <div className="text-center">
            <div className="text-6xl font-bold tabular-nums text-emerald-300 kiosk-glow-text">{power?.watts != null ? nf(power.watts, 1) : "—"}<span className="text-2xl text-emerald-400/60 ml-1">W</span></div>
            <div className="text-xs text-slate-400 mt-1">aktuelle Leistungsaufnahme (PMIC)</div>
          </div>
          <div className="grid grid-cols-2 gap-2 mt-4">
            <div className="rounded-lg bg-black/20 py-3 text-center"><div className="text-xl font-semibold tabular-nums">{power?.today_cost_eur != null ? `${nf(power.today_cost_eur, 3)} €` : "—"}</div><div className="text-[11px] text-slate-400">heute · {power?.today_kwh != null ? `${nf(power.today_kwh, 3)} kWh` : "—"}</div></div>
            <div className="rounded-lg bg-black/20 py-3 text-center"><div className="text-xl font-semibold tabular-nums">{power?.month_cost_eur != null ? `${nf(power.month_cost_eur, 2)} €` : "—"}</div><div className="text-[11px] text-slate-400">≈ pro Monat</div></div>
          </div>
          <div className="text-center text-xs text-slate-500 mt-3">Tarif {nf(power?.price_eur_kwh ?? 0, 2)} €/kWh · in Einstellungen änderbar</div>
        </div>
      </Panel>
    </div>
  );
}

/* ----------------------------- Einstellungen ---------------------------- */

function SettingsView() {
  const [tariff, setTariff] = useState("0.35");
  const [idleMin, setIdleMin] = useState(String(lsGet("kiosk_idle_ms", 90000) / 60000));
  const [pollS, setPollS] = useState(String(lsGet("kiosk_poll_ms", 2500) / 1000));
  const [saved, setSaved] = useState(false);
  useEffect(() => { (async () => { try { const r = await fetch("/api/v1/kiosk/settings", { cache: "no-store" }); if (r.ok) { const j = await r.json(); setTariff(String(j.electricity_price_eur_kwh)); } } catch { /* */ } })(); }, []);
  const save = async () => {
    try {
      await fetch("/api/v1/kiosk/settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ electricity_price_eur_kwh: Number(tariff) }) });
      localStorage.setItem("kiosk_idle_ms", String(Math.max(0.5, Number(idleMin)) * 60000));
      localStorage.setItem("kiosk_poll_ms", String(Math.max(1, Number(pollS)) * 1000));
      setSaved(true); setTimeout(() => setSaved(false), 1800);
    } catch { /* */ }
  };
  return (
    <div className="h-full overflow-y-auto kiosk-scroll max-w-2xl mx-auto space-y-3">
      <Panel title="Strom & Kosten" icon={<Euro className="w-4 h-4 text-amber-300" />}>
        <Field label="Strompreis (€/kWh)"><input type="number" step="0.01" value={tariff} onChange={(e) => setTariff(e.target.value)} className="kiosk-input" /></Field>
      </Panel>
      <Panel title="Energiesparen & Anzeige" icon={<Gauge className="w-4 h-4 text-cyan-300" />}>
        <Field label="Screensaver nach (Minuten Inaktivität)"><input type="number" step="0.5" value={idleMin} onChange={(e) => setIdleMin(e.target.value)} className="kiosk-input" /></Field>
        <Field label="Aktualisierung (Sekunden)"><input type="number" step="1" value={pollS} onChange={(e) => setPollS(e.target.value)} className="kiosk-input" /></Field>
        <div className="text-[11px] text-slate-500 mt-1">Das physische Display-Aus (Hardware) steuert swayidle auf dem Gerät (Touch weckt).</div>
      </Panel>
      <button onClick={save} className="w-full h-12 rounded-xl bg-gradient-to-br from-cyan-500 to-violet-500 font-medium flex items-center justify-center gap-2 active:scale-[0.99] transition">
        {saved ? <><ShieldCheck className="w-5 h-5" />Gespeichert</> : <><Save className="w-5 h-5" />Speichern</>}
      </button>
      <div className="text-center text-[11px] text-slate-600 flex items-center justify-center gap-1.5"><RefreshCw className="w-3 h-3" />AI Employee · Mission Control · Kiosk lokal (nicht extern erreichbar)</div>
    </div>
  );
}

/* -------------------------------- pieces -------------------------------- */

function Panel({ title, icon, right, compact, children }: { title: string; icon: ReactNode; right?: string; compact?: boolean; children: ReactNode }) {
  return (
    <div className={`kiosk-card ${compact ? "p-2.5" : "p-3"} flex flex-col min-h-0`}>
      <div className="flex items-center justify-between mb-2 shrink-0">
        <div className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-slate-300">{icon}{title}</div>
        {right ? <span className="text-[10px] text-amber-400/80">{right}</span> : null}
      </div>
      <div className="flex-1 min-h-0">{children}</div>
    </div>
  );
}
function BigStat({ label, value, accent, icon, pulse }: { label: string; value: number | string; accent: string; icon: ReactNode; pulse?: boolean }) {
  return (
    <div className="kiosk-card p-3 text-center relative overflow-hidden">
      <div className={`absolute top-2 right-2 ${accent} opacity-50`}>{icon}</div>
      <div className={`text-3xl font-bold tabular-nums ${accent} ${pulse ? "kiosk-glow-text" : ""}`}>{value}</div>
      <div className="text-[11px] text-slate-400 mt-0.5">{label}</div>
    </div>
  );
}
function Bar({ icon, label, value, unit, color, sub }: { icon: ReactNode; label: string; value: number | null; unit: string; color: string; sub?: string }) {
  const pct = value == null ? 0 : Math.max(0, Math.min(100, value));
  return (
    <div>
      <div className="flex items-center justify-between text-xs mb-1">
        <span className="flex items-center gap-1 text-slate-300">{icon}{label}</span>
        <span className="tabular-nums text-slate-200">{value == null ? "—" : `${nf(value, 0)}${unit}`}{sub ? <span className="text-slate-500 ml-1.5">{sub}</span> : null}</span>
      </div>
      <div className="h-2 rounded-full bg-white/5 overflow-hidden"><div className={`h-full rounded-full bg-gradient-to-r ${color} transition-all duration-500`} style={{ width: `${pct}%` }} /></div>
    </div>
  );
}
function KV({ icon, label, value }: { icon: ReactNode; label: string; value: string }) {
  return (
    <div className="kiosk-card p-3">
      <div className="flex items-center gap-1.5 text-[11px] text-slate-400">{icon}{label}</div>
      <div className="text-lg font-semibold truncate mt-0.5">{value}</div>
    </div>
  );
}
function Field({ label, children }: { label: string; children: ReactNode }) {
  return <label className="block mb-2"><span className="text-xs text-slate-400">{label}</span><div className="mt-1">{children}</div></label>;
}
function Empty({ text }: { text: string }) { return <div className="text-center text-slate-500 text-sm py-10">{text}</div>; }

/* --------------------------------- chat --------------------------------- */

function ChatOverlay({ agent, onBack }: { agent: AgentInfo; onBack: () => void }) {
  const [msgs, setMsgs] = useState<ChatMsg[]>([]);
  const [text, setText] = useState("");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const poll = useCallback(async (sid: string) => { try { const r = await fetch(`/api/v1/kiosk/chat/${agent.id}/history?session_id=${sid}`, { cache: "no-store" }); if (r.ok) setMsgs((await r.json()).messages || []); } catch { /* */ } }, [agent.id]);
  useEffect(() => { if (!sessionId) return; poll(sessionId); const t = setInterval(() => poll(sessionId), 1500); return () => clearInterval(t); }, [sessionId, poll]);
  useEffect(() => { scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" }); }, [msgs]);
  const send = async () => {
    const t = text.trim(); if (!t || sending) return;
    setSending(true); setText(""); setMsgs((m) => [...m, { role: "user", content: t, message_id: "local", ts: null }]);
    try { const r = await fetch(`/api/v1/kiosk/chat/${agent.id}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: t, session_id: sessionId }) }); if (r.ok) setSessionId((await r.json()).session_id); } catch { /* */ }
    setSending(false);
  };
  const c = stateColor(agent.state);
  return (
    <div className="kiosk-root h-screen w-screen overflow-hidden kiosk-bg text-slate-100 flex flex-col select-none">
      <KioskStyles />
      <header className="flex items-center gap-3 px-3 h-14 border-b border-white/5 bg-gradient-to-r from-[#0a1020] to-[#0a0f1a]">
        <button onClick={onBack} className="w-10 h-10 rounded-lg bg-white/5 active:scale-95 grid place-items-center"><ArrowLeft className="w-5 h-5" /></button>
        <div className={`w-9 h-9 rounded-lg grid place-items-center bg-gradient-to-br from-slate-700 to-slate-800 ${c.glow}`}><span className="text-sm font-bold">{agent.name?.[0]?.toUpperCase()}</span></div>
        <div className="flex-1 min-w-0"><div className="font-semibold truncate">{agent.name}</div><div className="text-[11px] text-slate-400 flex items-center gap-1"><span className={`w-1.5 h-1.5 rounded-full ${c.dot}`} />{agent.state} · {agent.model}</div></div>
      </header>
      <div ref={scrollRef} className="flex-1 overflow-y-auto p-3 space-y-2 kiosk-scroll">
        {msgs.length === 0 && <div className="text-center text-slate-500 text-sm py-10">Schreib {agent.name} eine Nachricht…</div>}
        {msgs.map((m, i) => (
          <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
            <div className={`max-w-[78%] rounded-2xl px-3.5 py-2 text-sm whitespace-pre-wrap break-words ${m.role === "user" ? "bg-gradient-to-br from-cyan-500 to-violet-500 text-white" : m.role === "error" ? "bg-rose-500/20 text-rose-200 border border-rose-500/30" : "bg-white/[0.06]"}`}>{m.content || (m.role === "assistant" ? "…" : "")}</div>
          </div>
        ))}
        {sending && <div className="flex justify-start"><div className="bg-white/[0.06] rounded-2xl px-4 py-2.5"><span className="kiosk-typing" /></div></div>}
      </div>
      <div className="px-3 pt-2 flex items-center gap-2 border-t border-white/5">
        <div className="flex-1 h-12 rounded-xl bg-white/[0.05] border border-white/10 px-4 flex items-center text-base overflow-x-auto whitespace-nowrap kiosk-scroll">
          {text ? <span>{text}<span className="kiosk-caret" /></span> : <span className="text-slate-500">Nachricht an den Agenten…</span>}
        </div>
        <button onClick={send} disabled={!text.trim() || sending} className="h-12 w-12 rounded-xl bg-gradient-to-br from-cyan-500 to-violet-500 grid place-items-center disabled:opacity-40 active:scale-95 shrink-0"><Send className="w-5 h-5 text-white" /></button>
      </div>
      <OnScreenKeyboard onKey={(c) => setText((t) => t + c)} onBackspace={() => setText((t) => t.slice(0, -1))} onSpace={() => setText((t) => t + " ")} onEnter={send} />
    </div>
  );
}

/* ------------------------ on-screen keyboard (touch) -------------------- */

const KB_ROWS = [
  ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"],
  ["q", "w", "e", "r", "t", "z", "u", "i", "o", "p"],
  ["a", "s", "d", "f", "g", "h", "j", "k", "l", "ä"],
  ["y", "x", "c", "v", "b", "n", "m", "ö", "ü"],
];

function OnScreenKeyboard({ onKey, onBackspace, onSpace, onEnter }: { onKey: (c: string) => void; onBackspace: () => void; onSpace: () => void; onEnter: () => void }) {
  const [shift, setShift] = useState(false);
  const [sym, setSym] = useState(false);
  const symRows = [["!", "?", ".", ",", ":", ";", "-", "_", "/", "@"], ["+", "*", "#", "(", ")", "\"", "'", "=", "%", "&"], ["ß", "€", "$", "<", ">", "[", "]", "{", "}", "|"]];
  const rows = sym ? symRows : KB_ROWS;
  const press = (k: string) => onKey(shift && !sym ? k.toUpperCase() : k);
  return (
    <div className="bg-[#0a0f1a] border-t border-white/10 p-1.5 space-y-1.5 select-none shrink-0">
      {rows.map((row, ri) => (
        <div key={ri} className="flex gap-1.5 justify-center">
          {!sym && ri === 3 && <KbKey label="⇧" wide active={shift} onClick={() => setShift((s) => !s)} />}
          {row.map((k) => <KbKey key={k} label={shift && !sym ? k.toUpperCase() : k} onClick={() => press(k)} />)}
          {!sym && ri === 3 && <KbKey label="⌫" wide onClick={onBackspace} />}
          {sym && ri === 2 && <KbKey label="⌫" wide onClick={onBackspace} />}
        </div>
      ))}
      <div className="flex gap-1.5 justify-center">
        <KbKey label={sym ? "abc" : "?123"} wide onClick={() => setSym((s) => !s)} />
        <KbKey label="Leerzeichen" grow onClick={onSpace} />
        <KbKey label="Senden" grow accent onClick={onEnter} />
      </div>
    </div>
  );
}

function KbKey({ label, onClick, wide, grow, active, accent }: { label: string; onClick: () => void; wide?: boolean; grow?: boolean; active?: boolean; accent?: boolean }) {
  return (
    <button
      onPointerDown={(e) => { e.preventDefault(); onClick(); }}
      className={`h-12 rounded-lg text-lg font-medium active:scale-95 transition ${grow ? "flex-1" : wide ? "px-4 min-w-[52px]" : "flex-1 max-w-[10%]"} ${accent ? "bg-gradient-to-br from-cyan-500 to-violet-500 text-white" : active ? "bg-cyan-500/30 text-cyan-200" : "bg-white/[0.08] text-slate-100 active:bg-white/[0.15]"}`}
    >
      {label}
    </button>
  );
}

/* ------------------------------ screensaver ----------------------------- */

function Screensaver({ now, power, temp, working }: { now: Date | null; power: Overview["power"] | undefined; temp: number | null; working: number }) {
  return (
    <div className="kiosk-root h-screen w-screen overflow-hidden bg-black text-slate-200 grid place-items-center select-none">
      <KioskStyles />
      <div className="text-center">
        <div className="text-8xl font-bold tabular-nums tracking-tight kiosk-dim">{now ? now.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" }) : "--:--"}</div>
        <div className="text-slate-500 mt-2">{now ? now.toLocaleDateString("de-DE", { weekday: "long", day: "2-digit", month: "long" }) : ""}</div>
        <div className="mt-8 flex items-center justify-center gap-6 text-slate-600">
          <span className="flex items-center gap-2"><Zap className="w-4 h-4" />{power?.watts != null ? `${nf(power.watts, 1)} W` : "—"}</span>
          <span className="flex items-center gap-2"><Thermometer className="w-4 h-4" />{temp != null ? `${nf(temp, 1)}°` : "—"}</span>
          <span className="flex items-center gap-2"><Bot className="w-4 h-4" />{working} aktiv</span>
        </div>
        <div className="text-[11px] text-slate-700 mt-10">Tippen zum Aufwecken</div>
      </div>
    </div>
  );
}

/* ------------------------------- styles --------------------------------- */

function KioskStyles() {
  return (
    <style>{`
      .kiosk-root { font-feature-settings: "tnum"; -webkit-tap-highlight-color: transparent; }
      .kiosk-bg {
        background:
          radial-gradient(1100px 560px at 12% -12%, rgba(56,189,248,0.12), transparent 60%),
          radial-gradient(1000px 520px at 100% -8%, rgba(139,92,246,0.12), transparent 55%),
          radial-gradient(900px 620px at 50% 118%, rgba(16,185,129,0.09), transparent 60%),
          #05070d;
      }
      .kiosk-card {
        background: linear-gradient(180deg, rgba(255,255,255,0.055), rgba(255,255,255,0.02));
        border: 1px solid rgba(255,255,255,0.09);
        border-radius: 16px;
        box-shadow: inset 0 1px 0 rgba(255,255,255,0.05), 0 10px 28px -16px rgba(0,0,0,0.7);
      }
      .kiosk-card-hover:hover { background: linear-gradient(180deg, rgba(255,255,255,0.08), rgba(255,255,255,0.03)); border-color: rgba(255,255,255,0.16); }
      .kiosk-glow { box-shadow: 0 0 16px 2px rgba(56,189,248,0.35); }
      .kiosk-glow-text { text-shadow: 0 0 18px rgba(52,211,153,0.55); }
      .kiosk-dim { animation: kioskDim 6s ease-in-out infinite; }
      @keyframes kioskDim { 0%,100% { opacity: .55 } 50% { opacity: .8 } }
      .kiosk-scroll::-webkit-scrollbar { width: 4px; }
      .kiosk-scroll::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.1); border-radius: 4px; }
      .kiosk-input { width:100%; height:44px; border-radius:12px; background:rgba(255,255,255,0.05); border:1px solid rgba(255,255,255,0.1); padding:0 14px; font-size:16px; color:#fff; outline:none; }
      .kiosk-input:focus { border-color: rgba(34,211,238,0.5); }
      .kiosk-caret { display:inline-block; width:2px; height:1.1em; background:#22d3ee; margin-left:2px; vertical-align:text-bottom; animation: kioskBlink 1s steps(2) infinite; }
      @keyframes kioskBlink { 50% { opacity: 0 } }
      .kiosk-typing { display:inline-block; width:28px; height:8px; background: radial-gradient(circle 3px at 4px 4px, #64748b 90%, transparent) 0 0/12px 100% repeat-x; animation: kioskType 1s steps(3) infinite; }
      @keyframes kioskType { to { background-position: 12px 0 } }
    `}</style>
  );
}

"use client";

/**
 * Kiosk — "AI EMPLOYEE · Mission Control"
 *
 * Local-only fullscreen status display for the on-Pi 7" touchscreen (1024x600).
 * Renders live agents, tasks, AI spend, Pi utilisation and real power draw /
 * electricity cost, and lets you chat with an agent. Reachable only from the
 * device (Caddy 404s /kiosk for tunnel traffic); the page itself needs no auth.
 *
 * All data comes from the same-origin, no-auth kiosk API:
 *   GET  /api/v1/kiosk/overview
 *   POST /api/v1/kiosk/chat/{agentId}
 *   GET  /api/v1/kiosk/chat/{agentId}/history?session_id=...
 */

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import {
  Activity, Bot, Cpu, Gauge, HardDrive, MemoryStick, Zap, Euro,
  Clock, Thermometer, ListTodo, CircleDot, Send, ArrowLeft, Wifi, WifiOff,
} from "lucide-react";

type AgentInfo = {
  id: string; name: string; state: string; model: string;
  current_task: string | null; has_container: boolean;
};
type Overview = {
  ts: string;
  agents: AgentInfo[];
  agents_working: number;
  agents_total: number;
  tasks: { running: number; pending: number; done_today: number; recent: { title: string; status: string; agent_id: string | null }[] };
  ai_spend: { cost_usd_today: number; tokens_in_today: number; tokens_out_today: number };
  pi: {
    temp_c: number | null; cpu_percent: number | null; load: number[] | null;
    mem_used_mb: number | null; mem_total_mb: number | null;
    disk_used_gb: number | null; disk_total_gb: number | null;
    uptime_s: number | null; stale: boolean;
  };
  power: { watts: number | null; today_kwh: number | null; today_cost_eur: number | null; month_cost_eur: number | null; price_eur_kwh: number };
};
type ChatMsg = { role: string; content: string; message_id: string; ts: string | null };

const POLL_MS = 2500;
const IDLE_MS = 90_000; // dim to screensaver after 90s of no touch

const nf = (n: number, d = 0) => n.toLocaleString("de-DE", { minimumFractionDigits: d, maximumFractionDigits: d });

function fmtUptime(s: number | null): string {
  if (s == null) return "—";
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}
function tempColor(t: number | null): string {
  if (t == null) return "text-slate-400";
  if (t >= 75) return "text-rose-400";
  if (t >= 60) return "text-amber-400";
  return "text-emerald-400";
}
function stateColor(s: string): { dot: string; text: string; glow: string } {
  switch (s) {
    case "working": return { dot: "bg-emerald-400", text: "text-emerald-300", glow: "shadow-[0_0_10px_2px_rgba(52,211,153,0.6)]" };
    case "running": return { dot: "bg-cyan-400", text: "text-cyan-300", glow: "shadow-[0_0_8px_1px_rgba(34,211,238,0.5)]" };
    case "error": return { dot: "bg-rose-500", text: "text-rose-300", glow: "shadow-[0_0_10px_2px_rgba(244,63,94,0.6)]" };
    case "idle": return { dot: "bg-slate-500", text: "text-slate-300", glow: "" };
    case "stopped": return { dot: "bg-slate-700", text: "text-slate-500", glow: "" };
    default: return { dot: "bg-violet-400", text: "text-violet-300", glow: "" };
  }
}

export default function KioskPage() {
  const [data, setData] = useState<Overview | null>(null);
  const [online, setOnline] = useState(false);
  const [now, setNow] = useState<Date | null>(null);
  const [chatAgent, setChatAgent] = useState<AgentInfo | null>(null);
  const [idle, setIdle] = useState(false);
  const lastActivity = useRef<number>(Date.now());

  // live clock
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000);
    setNow(new Date());
    return () => clearInterval(t);
  }, []);

  // activity tracking → wake from screensaver
  useEffect(() => {
    const wake = () => { lastActivity.current = Date.now(); if (idle) setIdle(false); };
    const evs = ["pointerdown", "keydown", "touchstart", "mousemove"];
    evs.forEach((e) => window.addEventListener(e, wake, { passive: true }));
    const t = setInterval(() => {
      if (!chatAgent && Date.now() - lastActivity.current > IDLE_MS) setIdle(true);
    }, 2000);
    return () => { evs.forEach((e) => window.removeEventListener(e, wake)); clearInterval(t); };
  }, [idle, chatAgent]);

  // poll overview (slower while idle to save power)
  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const r = await fetch("/api/v1/kiosk/overview", { cache: "no-store" });
        if (!r.ok) throw new Error(String(r.status));
        const j = (await r.json()) as Overview;
        if (alive) { setData(j); setOnline(true); }
      } catch { if (alive) setOnline(false); }
    };
    load();
    const t = setInterval(load, idle ? POLL_MS * 4 : POLL_MS);
    return () => { alive = false; clearInterval(t); };
  }, [idle]);

  if (chatAgent) {
    return <ChatOverlay agent={chatAgent} onBack={() => { lastActivity.current = Date.now(); setChatAgent(null); }} />;
  }
  if (idle) {
    return <Screensaver now={now} power={data?.power} temp={data?.pi.temp_c ?? null} working={data?.agents_working ?? 0} />;
  }

  const pi = data?.pi;
  const power = data?.power;

  return (
    <div className="kiosk-root h-screen w-screen overflow-hidden bg-[#05070d] text-slate-100 select-none flex flex-col">
      <KioskStyles />

      {/* Top bar */}
      <header className="flex items-center justify-between px-4 h-14 border-b border-white/5 bg-gradient-to-r from-[#0a1020] to-[#0a0f1a]">
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-cyan-400 to-violet-500 grid place-items-center kiosk-glow">
            <Bot className="w-5 h-5 text-white" />
          </div>
          <div className="leading-tight">
            <div className="text-[15px] font-semibold tracking-wide">AI EMPLOYEE</div>
            <div className="text-[10px] uppercase tracking-[0.25em] text-cyan-400/70">Mission Control</div>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <Pill icon={online ? <Wifi className="w-3.5 h-3.5" /> : <WifiOff className="w-3.5 h-3.5" />} label={online ? "Online" : "Offline"} ok={online} />
          <div className={`flex items-center gap-1.5 ${tempColor(pi?.temp_c ?? null)}`}>
            <Thermometer className="w-4 h-4" />
            <span className="text-lg font-semibold tabular-nums">{pi?.temp_c != null ? `${nf(pi.temp_c, 1)}°` : "—"}</span>
          </div>
          <div className="text-right leading-tight">
            <div className="text-xl font-semibold tabular-nums">{now ? now.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" }) : "--:--"}</div>
            <div className="text-[10px] text-slate-400">{now ? now.toLocaleDateString("de-DE", { weekday: "short", day: "2-digit", month: "2-digit" }) : ""}</div>
          </div>
        </div>
      </header>

      {/* Main grid */}
      <div className="flex-1 grid grid-cols-[300px_1fr_300px] gap-3 p-3 min-h-0">

        {/* LEFT — Agents */}
        <section className="min-h-0 flex flex-col rounded-xl border border-white/5 bg-white/[0.02] p-3">
          <SectionTitle icon={<Bot className="w-4 h-4 text-cyan-400" />} title="Agenten" right={`${data?.agents_working ?? 0}/${data?.agents_total ?? 0} aktiv`} />
          <div className="flex-1 overflow-y-auto space-y-2 pr-1 kiosk-scroll">
            {(data?.agents ?? []).length === 0 && <Empty text="Keine Agenten" />}
            {(data?.agents ?? [])
              .slice()
              .sort((a, b) => (rank(b.state) - rank(a.state)))
              .map((a) => <AgentCard key={a.id} a={a} onChat={() => setChatAgent(a)} />)}
          </div>
        </section>

        {/* CENTER — Tasks + AI spend */}
        <section className="min-h-0 flex flex-col gap-3">
          <div className="grid grid-cols-3 gap-3">
            <BigStat label="Läuft" value={data?.tasks.running ?? 0} accent="text-emerald-300" icon={<Activity className="w-4 h-4" />} pulse={(data?.tasks.running ?? 0) > 0} />
            <BigStat label="Wartet" value={data?.tasks.pending ?? 0} accent="text-amber-300" icon={<ListTodo className="w-4 h-4" />} />
            <BigStat label="Heute fertig" value={data?.tasks.done_today ?? 0} accent="text-cyan-300" icon={<CircleDot className="w-4 h-4" />} />
          </div>

          <div className="flex-1 min-h-0 rounded-xl border border-white/5 bg-white/[0.02] p-3 flex flex-col">
            <SectionTitle icon={<Activity className="w-4 h-4 text-violet-400" />} title="Aktivität" />
            <div className="flex-1 overflow-y-auto space-y-1.5 pr-1 kiosk-scroll">
              {(data?.tasks.recent ?? []).length === 0 && <Empty text="Noch keine Tasks" />}
              {(data?.tasks.recent ?? []).map((t, i) => (
                <div key={i} className="flex items-center gap-2 text-sm rounded-lg bg-white/[0.02] px-2.5 py-2">
                  <span className={`w-1.5 h-1.5 rounded-full ${stateColor(t.status).dot}`} />
                  <span className="truncate flex-1 text-slate-200">{t.title || "—"}</span>
                  <span className={`text-[10px] uppercase tracking-wide ${stateColor(t.status).text}`}>{t.status}</span>
                </div>
              ))}
            </div>
          </div>

          <div className="rounded-xl border border-white/5 bg-gradient-to-r from-violet-500/10 to-cyan-500/10 p-3 flex items-center justify-between">
            <div className="flex items-center gap-2 text-slate-300"><Euro className="w-4 h-4 text-violet-300" /><span className="text-sm">AI-Kosten heute</span></div>
            <div className="text-right">
              <div className="text-2xl font-bold tabular-nums text-white">${nf(data?.ai_spend.cost_usd_today ?? 0, 2)}</div>
              <div className="text-[10px] text-slate-400">{nf((data?.ai_spend.tokens_in_today ?? 0) + (data?.ai_spend.tokens_out_today ?? 0))} Tokens</div>
            </div>
          </div>
        </section>

        {/* RIGHT — Pi + Power */}
        <section className="min-h-0 flex flex-col gap-3">
          <div className="rounded-xl border border-white/5 bg-white/[0.02] p-3">
            <SectionTitle icon={<Cpu className="w-4 h-4 text-emerald-400" />} title="Die Möhre" right={pi?.stale ? "stale" : ""} />
            <div className="space-y-2.5 mt-1">
              <Bar icon={<Gauge className="w-3.5 h-3.5" />} label="CPU" value={pi?.cpu_percent ?? null} max={100} unit="%" color="from-cyan-500 to-emerald-400" />
              <Bar icon={<MemoryStick className="w-3.5 h-3.5" />} label="RAM"
                value={pi?.mem_used_mb != null && pi?.mem_total_mb ? (pi.mem_used_mb / pi.mem_total_mb) * 100 : null}
                max={100} unit="%" color="from-violet-500 to-fuchsia-400"
                sub={pi?.mem_used_mb != null ? `${nf((pi.mem_used_mb || 0) / 1024, 1)}/${nf((pi.mem_total_mb || 0) / 1024, 1)} GB` : ""} />
              <Bar icon={<HardDrive className="w-3.5 h-3.5" />} label="Disk"
                value={pi?.disk_used_gb != null && pi?.disk_total_gb ? (pi.disk_used_gb / pi.disk_total_gb) * 100 : null}
                max={100} unit="%" color="from-amber-500 to-orange-400"
                sub={pi?.disk_used_gb != null ? `${nf(pi.disk_used_gb)}/${nf(pi.disk_total_gb || 0)} GB` : ""} />
              <div className="flex items-center justify-between text-xs text-slate-400 pt-1">
                <span className="flex items-center gap-1"><Clock className="w-3.5 h-3.5" /> Uptime</span>
                <span className="tabular-nums text-slate-200">{fmtUptime(pi?.uptime_s ?? null)}</span>
              </div>
              <div className="flex items-center justify-between text-xs text-slate-400">
                <span>Load</span>
                <span className="tabular-nums text-slate-200">{pi?.load ? pi.load.map((l) => nf(l, 2)).join("  ") : "—"}</span>
              </div>
            </div>
          </div>

          {/* Power / electricity */}
          <div className="flex-1 rounded-xl border border-white/5 bg-gradient-to-br from-emerald-500/10 to-cyan-500/5 p-3 flex flex-col justify-between">
            <SectionTitle icon={<Zap className="w-4 h-4 text-emerald-300" />} title="Leistung" />
            <div className="text-center py-1">
              <div className="text-5xl font-bold tabular-nums text-emerald-300 kiosk-glow-text">
                {power?.watts != null ? nf(power.watts, 1) : "—"}<span className="text-xl text-emerald-400/60 ml-1">W</span>
              </div>
            </div>
            <div className="grid grid-cols-2 gap-2 text-center">
              <div className="rounded-lg bg-black/20 py-2">
                <div className="text-lg font-semibold tabular-nums text-white">{power?.today_cost_eur != null ? `${nf(power.today_cost_eur, 3)} €` : "—"}</div>
                <div className="text-[10px] text-slate-400">heute · {power?.today_kwh != null ? `${nf(power.today_kwh, 3)} kWh` : "—"}</div>
              </div>
              <div className="rounded-lg bg-black/20 py-2">
                <div className="text-lg font-semibold tabular-nums text-white">{power?.month_cost_eur != null ? `${nf(power.month_cost_eur, 2)} €` : "—"}</div>
                <div className="text-[10px] text-slate-400">≈ / Monat</div>
              </div>
            </div>
            <div className="text-[10px] text-slate-500 text-center mt-1">Tarif {nf(power?.price_eur_kwh ?? 0, 2)} €/kWh</div>
          </div>
        </section>
      </div>
    </div>
  );
}

/* ------------------------------- pieces --------------------------------- */

const RANK: Record<string, number> = { error: 5, working: 4, running: 3, idle: 1, stopped: 0 };
function rank(s: string): number { return RANK[s] ?? 2; }

function SectionTitle({ icon, title, right }: { icon: ReactNode; title: string; right?: string }) {
  return (
    <div className="flex items-center justify-between mb-2">
      <div className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-slate-300">{icon}{title}</div>
      {right ? <span className="text-[10px] text-slate-400">{right}</span> : null}
    </div>
  );
}
function Empty({ text }: { text: string }) {
  return <div className="text-center text-slate-500 text-sm py-8">{text}</div>;
}
function Pill({ icon, label, ok }: { icon: ReactNode; label: string; ok: boolean }) {
  return (
    <div className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs ${ok ? "bg-emerald-500/15 text-emerald-300" : "bg-rose-500/15 text-rose-300"}`}>
      {icon}<span>{label}</span>
    </div>
  );
}
function AgentCard({ a, onChat }: { a: AgentInfo; onChat: () => void }) {
  const c = stateColor(a.state);
  return (
    <button onClick={onChat} className="w-full text-left rounded-lg border border-white/5 bg-white/[0.02] hover:bg-white/[0.05] active:scale-[0.99] transition p-2.5 flex items-center gap-2.5">
      <div className={`w-8 h-8 rounded-lg grid place-items-center bg-gradient-to-br from-slate-700 to-slate-800 ${c.glow}`}>
        <span className="text-sm font-bold text-white">{a.name?.[0]?.toUpperCase() ?? "?"}</span>
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          <span className={`w-2 h-2 rounded-full ${c.dot} ${a.state === "working" ? "animate-pulse" : ""}`} />
          <span className="text-sm font-medium truncate">{a.name}</span>
        </div>
        <div className="text-[11px] text-slate-400 truncate">{a.current_task || a.model || a.state}</div>
      </div>
      <Send className="w-3.5 h-3.5 text-slate-500 shrink-0" />
    </button>
  );
}
function BigStat({ label, value, accent, icon, pulse }: { label: string; value: number; accent: string; icon: ReactNode; pulse?: boolean }) {
  return (
    <div className="rounded-xl border border-white/5 bg-white/[0.02] p-3 text-center relative overflow-hidden">
      <div className={`absolute top-2 right-2 ${accent} opacity-60`}>{icon}</div>
      <div className={`text-4xl font-bold tabular-nums ${accent} ${pulse ? "kiosk-glow-text" : ""}`}>{value}</div>
      <div className="text-[11px] text-slate-400 mt-0.5">{label}</div>
    </div>
  );
}
function Bar({ icon, label, value, max, unit, color, sub }: { icon: ReactNode; label: string; value: number | null; max: number; unit: string; color: string; sub?: string }) {
  const pct = value == null ? 0 : Math.max(0, Math.min(100, (value / max) * 100));
  return (
    <div>
      <div className="flex items-center justify-between text-xs mb-1">
        <span className="flex items-center gap-1 text-slate-300">{icon}{label}</span>
        <span className="tabular-nums text-slate-200">{value == null ? "—" : `${nf(value, 0)}${unit}`}{sub ? <span className="text-slate-500 ml-1.5">{sub}</span> : null}</span>
      </div>
      <div className="h-2 rounded-full bg-white/5 overflow-hidden">
        <div className={`h-full rounded-full bg-gradient-to-r ${color} transition-all duration-500`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

/* ------------------------------- chat ----------------------------------- */

function ChatOverlay({ agent, onBack }: { agent: AgentInfo; onBack: () => void }) {
  const [msgs, setMsgs] = useState<ChatMsg[]>([]);
  const [text, setText] = useState("");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  const poll = useCallback(async (sid: string) => {
    try {
      const r = await fetch(`/api/v1/kiosk/chat/${agent.id}/history?session_id=${sid}`, { cache: "no-store" });
      if (r.ok) { const j = await r.json(); setMsgs(j.messages || []); }
    } catch { /* ignore */ }
  }, [agent.id]);

  useEffect(() => {
    if (!sessionId) return;
    poll(sessionId);
    const t = setInterval(() => poll(sessionId), 1500);
    return () => clearInterval(t);
  }, [sessionId, poll]);

  useEffect(() => { scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" }); }, [msgs]);

  const send = async () => {
    const t = text.trim();
    if (!t || sending) return;
    setSending(true);
    setText("");
    // optimistic
    setMsgs((m) => [...m, { role: "user", content: t, message_id: "local", ts: null }]);
    try {
      const r = await fetch(`/api/v1/kiosk/chat/${agent.id}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: t, session_id: sessionId }),
      });
      if (r.ok) { const j = await r.json(); setSessionId(j.session_id); }
    } catch { /* ignore */ }
    setSending(false);
  };

  const c = stateColor(agent.state);
  return (
    <div className="kiosk-root h-screen w-screen overflow-hidden bg-[#05070d] text-slate-100 flex flex-col select-none">
      <KioskStyles />
      <header className="flex items-center gap-3 px-3 h-14 border-b border-white/5 bg-gradient-to-r from-[#0a1020] to-[#0a0f1a]">
        <button onClick={onBack} className="w-10 h-10 rounded-lg bg-white/5 hover:bg-white/10 active:scale-95 grid place-items-center transition">
          <ArrowLeft className="w-5 h-5" />
        </button>
        <div className={`w-9 h-9 rounded-lg grid place-items-center bg-gradient-to-br from-slate-700 to-slate-800 ${c.glow}`}>
          <span className="text-sm font-bold">{agent.name?.[0]?.toUpperCase()}</span>
        </div>
        <div className="flex-1 min-w-0">
          <div className="font-semibold truncate">{agent.name}</div>
          <div className="text-[11px] text-slate-400 flex items-center gap-1"><span className={`w-1.5 h-1.5 rounded-full ${c.dot}`} />{agent.state} · {agent.model}</div>
        </div>
      </header>

      <div ref={scrollRef} className="flex-1 overflow-y-auto p-3 space-y-2 kiosk-scroll">
        {msgs.length === 0 && <div className="text-center text-slate-500 text-sm py-10">Schreib {agent.name} eine Nachricht…</div>}
        {msgs.map((m, i) => (
          <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
            <div className={`max-w-[78%] rounded-2xl px-3.5 py-2 text-sm whitespace-pre-wrap break-words ${
              m.role === "user" ? "bg-gradient-to-br from-cyan-500 to-violet-500 text-white"
              : m.role === "error" ? "bg-rose-500/20 text-rose-200 border border-rose-500/30"
              : "bg-white/[0.06] text-slate-100"}`}>
              {m.content || (m.role === "assistant" ? "…" : "")}
            </div>
          </div>
        ))}
        {sending && <div className="flex justify-start"><div className="bg-white/[0.06] rounded-2xl px-4 py-2.5"><span className="kiosk-typing" /></div></div>}
      </div>

      <div className="p-3 border-t border-white/5 flex items-center gap-2">
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") send(); }}
          placeholder="Nachricht an den Agenten…"
          className="flex-1 h-12 rounded-xl bg-white/[0.05] border border-white/10 px-4 text-base outline-none focus:border-cyan-400/50"
        />
        <button onClick={send} disabled={!text.trim() || sending}
          className="h-12 w-12 rounded-xl bg-gradient-to-br from-cyan-500 to-violet-500 grid place-items-center disabled:opacity-40 active:scale-95 transition">
          <Send className="w-5 h-5 text-white" />
        </button>
      </div>
    </div>
  );
}

/* ---------------------------- screensaver ------------------------------- */

function Screensaver({ now, power, temp, working }: { now: Date | null; power: Overview["power"] | undefined; temp: number | null; working: number }) {
  return (
    <div className="kiosk-root h-screen w-screen overflow-hidden bg-black text-slate-200 grid place-items-center select-none">
      <KioskStyles />
      <div className="text-center">
        <div className="text-8xl font-bold tabular-nums tracking-tight kiosk-dim">
          {now ? now.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" }) : "--:--"}
        </div>
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

/* ------------------------------ styles ---------------------------------- */

function KioskStyles() {
  return (
    <style>{`
      .kiosk-root { font-feature-settings: "tnum"; -webkit-tap-highlight-color: transparent; cursor: none; }
      .kiosk-glow { box-shadow: 0 0 16px 2px rgba(56,189,248,0.35); }
      .kiosk-glow-text { text-shadow: 0 0 18px rgba(52,211,153,0.55); }
      .kiosk-dim { animation: kioskDim 6s ease-in-out infinite; }
      @keyframes kioskDim { 0%,100% { opacity: .55 } 50% { opacity: .8 } }
      .kiosk-scroll::-webkit-scrollbar { width: 4px; }
      .kiosk-scroll::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.1); border-radius: 4px; }
      .kiosk-typing { display:inline-block; width:28px; height:8px; background:
        radial-gradient(circle 3px at 4px 4px, #64748b 90%, transparent) 0 0/12px 100% repeat-x;
        animation: kioskType 1s steps(3) infinite; }
      @keyframes kioskType { to { background-position: 12px 0 } }
    `}</style>
  );
}

"use client";

import { useState, useEffect, useCallback } from "react";
import {
  BarChart3, Clock, DollarSign, CheckCircle2,
  Bot, Sparkles, RefreshCw, Loader2, ChevronUp, ChevronDown,
  Award, X, AlertTriangle, MessageSquare,
} from "lucide-react";
import {
  ResponsiveContainer, AreaChart, Area,
  XAxis, YAxis, Tooltip, CartesianGrid, ComposedChart, Bar,
} from "recharts";
import * as Dialog from "@radix-ui/react-dialog";
import { motion, AnimatePresence } from "framer-motion";
import { Header } from "@/components/layout/header";
import { cn } from "@/lib/utils";
import { getAnalyticsOverview, getAnalyticsSkills, getAnalyticsAgents, getAnalyticsAgentDetail } from "@/lib/api";

/* eslint-disable @typescript-eslint/no-explicit-any */

// ── helpers ────────────────────────────────────────────────────────────────

function fmtSeconds(s: number | null | undefined): string {
  if (!s) return "—";
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.round(s / 60)}min`;
  return `${(s / 3600).toFixed(1)}h`;
}

function fmtMs(ms: number | null | undefined): string {
  if (!ms) return "—";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function fmtCost(usd: number | null | undefined): string {
  if (!usd) return "$0.00";
  return `$${usd.toFixed(4)}`;
}

function Stars({ value }: { value: number | null | undefined }) {
  if (!value) return <span className="text-muted-foreground">—</span>;
  const full = Math.round(value);
  return (
    <span className="text-amber-400 text-sm">
      {"★".repeat(full)}{"☆".repeat(5 - full)}
      <span className="ml-1 text-xs text-muted-foreground">{value.toFixed(1)}</span>
    </span>
  );
}

function StatCard({
  label, value, sub, icon: Icon, color = "blue",
}: {
  label: string; value: string; sub?: string; icon: any; color?: string;
}) {
  const colors: Record<string, string> = {
    blue: "text-blue-400 bg-blue-500/10",
    emerald: "text-emerald-400 bg-emerald-500/10",
    amber: "text-amber-400 bg-amber-500/10",
    purple: "text-purple-400 bg-purple-500/10",
    cyan: "text-cyan-400 bg-cyan-500/10",
  };
  return (
    <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-[11px] font-medium text-muted-foreground/70">{label}</p>
          <p className="mt-1.5 text-2xl font-semibold tracking-tight">{value}</p>
          {sub && <p className="mt-0.5 text-[11px] text-muted-foreground">{sub}</p>}
        </div>
        <div className={cn("flex h-10 w-10 shrink-0 items-center justify-center rounded-xl", colors[color])}>
          <Icon className="h-5 w-5" />
        </div>
      </div>
    </div>
  );
}

function ChartTooltipCustom({ active, payload, label }: any) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-lg border border-border bg-card/95 px-3 py-2 text-xs shadow-xl backdrop-blur-sm">
      <p className="font-medium mb-1">{label}</p>
      {payload.map((p: any) => (
        <p key={p.name} style={{ color: p.color }}>
          {p.name}: {typeof p.value === "number" ? p.value.toFixed(2) : p.value}
        </p>
      ))}
    </div>
  );
}

// ── Agent Detail Modal ───────────────────────────────────────────────────────

function AgentDetailModal({ agentId, days, onClose }: { agentId: string; days: number; onClose: () => void }) {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getAnalyticsAgentDetail(agentId, days).then(setData).catch(console.error).finally(() => setLoading(false));
  }, [agentId, days]);

  return (
    <Dialog.Portal>
      <Dialog.Overlay className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm" />
      <Dialog.Content className="fixed inset-0 z-50 flex items-center justify-center p-4">
        <motion.div
          initial={{ opacity: 0, scale: 0.95, y: 8 }}
          animate={{ opacity: 1, scale: 1, y: 0 }}
          exit={{ opacity: 0, scale: 0.95, y: 8 }}
          transition={{ duration: 0.18 }}
          className="relative w-full max-w-3xl max-h-[90vh] overflow-y-auto rounded-2xl border border-foreground/[0.08] bg-card/95 backdrop-blur-md shadow-2xl p-6"
        >
          <button
            onClick={onClose}
            className="absolute top-4 right-4 rounded-lg p-1.5 text-muted-foreground hover:text-foreground hover:bg-foreground/[0.06] transition-colors"
          >
            <X className="h-4 w-4" />
          </button>

          {loading ? (
            <div className="flex items-center justify-center py-16">
              <Loader2 className="h-7 w-7 animate-spin text-muted-foreground" />
            </div>
          ) : !data ? (
            <div className="py-16 text-center text-sm text-muted-foreground">Keine Daten verfügbar.</div>
          ) : (
            <>
              {/* Header */}
              <div className="mb-5">
                <div className="flex items-center gap-3">
                  <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary/10">
                    <Bot className="h-5 w-5 text-primary" />
                  </div>
                  <div>
                    <Dialog.Title className="text-base font-semibold">{data.agent.name}</Dialog.Title>
                    {data.agent.role && <p className="text-[11px] text-muted-foreground">{data.agent.role}</p>}
                  </div>
                  <span className={cn(
                    "ml-auto inline-flex items-center rounded-full border px-2.5 py-1 text-[11px] font-medium",
                    data.agent.state === "running" ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/20" :
                    data.agent.state === "idle" ? "bg-blue-500/10 text-blue-400 border-blue-500/20" :
                    "bg-foreground/[0.05] text-muted-foreground border-foreground/[0.08]"
                  )}>
                    {data.agent.state}
                  </span>
                </div>
              </div>

              {/* Summary stats */}
              <div className="grid grid-cols-3 gap-3 mb-5">
                {[
                  { label: "Tasks", value: String(data.summary.total_tasks) },
                  { label: "Erfolgsquote", value: `${data.summary.success_rate_pct}%` },
                  { label: "Fehlgeschlagen", value: String(data.summary.failed) },
                  { label: "Ø Dauer", value: fmtMs(data.summary.avg_duration_ms) },
                  { label: "Gesamtkosten", value: fmtCost(data.summary.total_cost_usd) },
                  { label: "Ø Turns", value: String(data.summary.avg_turns) },
                ].map((s) => (
                  <div key={s.label} className="rounded-xl border border-foreground/[0.06] bg-foreground/[0.02] p-3">
                    <p className="text-[10px] font-medium text-muted-foreground/70">{s.label}</p>
                    <p className="mt-0.5 text-lg font-semibold">{s.value}</p>
                  </div>
                ))}
              </div>

              {/* Daily volume mini chart */}
              {data.daily?.length > 0 && (
                <div className="mb-5">
                  <p className="text-[11px] font-medium text-muted-foreground/70 mb-2">Task-Volumen ({days}d)</p>
                  <ResponsiveContainer width="100%" height={120}>
                    <ComposedChart data={data.daily}>
                      <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                      <XAxis dataKey="date" tick={{ fontSize: 9 }} stroke="rgba(255,255,255,0.2)" />
                      <YAxis tick={{ fontSize: 9 }} stroke="rgba(255,255,255,0.2)" />
                      <Tooltip content={<ChartTooltipCustom />} />
                      <Bar dataKey="completed" name="Erfolgreich" stackId="a" fill="#10b981" radius={[0, 0, 2, 2]} />
                      <Bar dataKey="failed" name="Fehler" stackId="a" fill="#ef4444" radius={[2, 2, 0, 0]} />
                    </ComposedChart>
                  </ResponsiveContainer>
                </div>
              )}

              {/* Recent errors */}
              {data.recent_errors?.length > 0 && (
                <div className="mb-5">
                  <div className="flex items-center gap-1.5 mb-2">
                    <AlertTriangle className="h-3.5 w-3.5 text-red-400" />
                    <p className="text-[11px] font-medium text-muted-foreground/70">Letzte Fehler</p>
                  </div>
                  <div className="space-y-1.5">
                    {data.recent_errors.map((e: any, i: number) => (
                      <div key={i} className="rounded-lg border border-red-500/10 bg-red-500/5 px-3 py-2 text-[11px]">
                        <p className="font-medium text-red-300">{e.title || "Unbekannt"}</p>
                        {e.error && <p className="text-muted-foreground mt-0.5 truncate">{e.error}</p>}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Ratings */}
              {data.ratings?.length > 0 && (
                <div>
                  <div className="flex items-center gap-1.5 mb-2">
                    <MessageSquare className="h-3.5 w-3.5 text-amber-400" />
                    <p className="text-[11px] font-medium text-muted-foreground/70">Bewertungen</p>
                  </div>
                  <div className="space-y-1.5">
                    {data.ratings.map((r: any) => (
                      <div key={r.id} className="rounded-lg border border-foreground/[0.06] bg-foreground/[0.02] px-3 py-2 text-[11px]">
                        <div className="flex items-center justify-between">
                          <Stars value={r.rating} />
                          <span className="text-muted-foreground">{r.created_at ? r.created_at.slice(0, 10) : ""}</span>
                        </div>
                        {r.comment && <p className="mt-1 text-muted-foreground">{r.comment}</p>}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {data.ratings?.length === 0 && data.recent_errors?.length === 0 && (
                <p className="text-center text-sm text-muted-foreground py-4">Noch keine Bewertungen oder Fehler.</p>
              )}
            </>
          )}
        </motion.div>
      </Dialog.Content>
    </Dialog.Portal>
  );
}

// ── main page ───────────────────────────────────────────────────────────────

type SortKey = "usage_count" | "avg_rating" | "time_saved_per_use_seconds" | "roi_factor" | "total_time_saved_seconds";

export default function AnalyticsPage() {
  const [days, setDays] = useState(30);
  const [overview, setOverview] = useState<any>(null);
  const [skillsData, setSkillsData] = useState<any>(null);
  const [agentsData, setAgentsData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [sortKey, setSortKey] = useState<SortKey>("total_time_saved_seconds");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [ov, sk, ag] = await Promise.all([
        getAnalyticsOverview(days),
        getAnalyticsSkills(days),
        getAnalyticsAgents(days),
      ]);
      setOverview(ov);
      setSkillsData(sk);
      setAgentsData(ag);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }, [days]);

  useEffect(() => { load(); }, [load]);

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else { setSortKey(key); setSortDir("desc"); }
  };

  const sortedSkills = skillsData?.skills
    ? [...skillsData.skills].sort((a: any, b: any) => {
        const av = a[sortKey] ?? -Infinity;
        const bv = b[sortKey] ?? -Infinity;
        return sortDir === "desc" ? bv - av : av - bv;
      })
    : [];

  const SortIcon = ({ k }: { k: SortKey }) => {
    if (sortKey !== k) return null;
    return sortDir === "desc"
      ? <ChevronDown className="h-3 w-3 inline ml-0.5" />
      : <ChevronUp className="h-3 w-3 inline ml-0.5" />;
  };

  return (
    <div className="flex flex-col h-screen overflow-hidden">
      <Header title="Analytics" />
      <main className="flex-1 overflow-y-auto p-6 space-y-6">

        {/* Toolbar */}
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            {[7, 14, 30, 90].map((d) => (
              <button
                key={d}
                onClick={() => setDays(d)}
                className={cn(
                  "rounded-xl px-3 py-1.5 text-xs font-medium transition-all",
                  days === d
                    ? "bg-primary text-primary-foreground shadow-lg shadow-primary/20"
                    : "text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04]"
                )}
              >
                {d}d
              </button>
            ))}
          </div>
          <button
            onClick={load}
            disabled={loading}
            className="flex items-center gap-2 rounded-xl px-4 py-2 text-sm text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04] transition-all"
          >
            <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />
            Aktualisieren
          </button>
        </div>

        {loading && !overview ? (
          <div className="flex items-center justify-center py-24">
            <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
          </div>
        ) : (
          <>
            {/* ── Overview cards ── */}
            <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-4">
              <StatCard
                label="Gesamt Tasks"
                value={String(overview?.total_tasks ?? 0)}
                sub={`${overview?.completed_tasks ?? 0} abgeschlossen`}
                icon={BarChart3}
                color="blue"
              />
              <StatCard
                label="Erfolgsquote"
                value={`${overview?.success_rate_pct ?? 0}%`}
                icon={CheckCircle2}
                color="emerald"
              />
              <StatCard
                label="Gesamtkosten"
                value={fmtCost(overview?.total_cost_usd)}
                sub={`Ø ${fmtMs(overview?.avg_duration_ms)} / Task`}
                icon={DollarSign}
                color="amber"
              />
              <StatCard
                label="Zeitersparnis"
                value={fmtSeconds(overview?.total_time_saved_seconds)}
                sub="durch Skills"
                icon={Clock}
                color="cyan"
              />
              <StatCard
                label="Aktive Agents"
                value={String(overview?.active_agents ?? 0)}
                icon={Bot}
                color="purple"
              />
              <StatCard
                label="Ø Bewertung"
                value={overview?.avg_task_rating ? `${overview.avg_task_rating.toFixed(1)}/5` : "—"}
                icon={Award}
                color="amber"
              />
            </div>

            {/* ── Task volume chart ── */}
            {overview?.daily_tasks?.length > 0 && (
              <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-5">
                <h2 className="text-sm font-semibold mb-4">Task-Volumen ({days}d)</h2>
                <ResponsiveContainer width="100%" height={180}>
                  <AreaChart data={overview.daily_tasks}>
                    <defs>
                      <linearGradient id="taskGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.3} />
                        <stop offset="95%" stopColor="#3b82f6" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                    <XAxis dataKey="date" tick={{ fontSize: 11 }} stroke="rgba(255,255,255,0.2)" />
                    <YAxis tick={{ fontSize: 11 }} stroke="rgba(255,255,255,0.2)" />
                    <Tooltip content={<ChartTooltipCustom />} />
                    <Area type="monotone" dataKey="count" name="Tasks" stroke="#3b82f6" fill="url(#taskGrad)" strokeWidth={2} />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            )}

            {/* ── Skills table ── */}
            <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-5">
              <div className="flex items-center gap-2 mb-4">
                <Sparkles className="h-4 w-4 text-primary" />
                <h2 className="text-sm font-semibold">Skill Analytics — Zeitersparnis & Qualität</h2>
                <span className="ml-auto text-[11px] text-muted-foreground">{sortedSkills.length} Skills</span>
              </div>

              {sortedSkills.length === 0 ? (
                <div className="py-12 text-center text-sm text-muted-foreground">
                  Noch keine Skill-Nutzung im gewählten Zeitraum.
                </div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-foreground/[0.06] text-[11px] font-medium text-muted-foreground/70">
                        <th className="text-left pb-2 pr-4">Skill</th>
                        <th
                          className="text-right pb-2 px-3 cursor-pointer hover:text-foreground whitespace-nowrap"
                          onClick={() => toggleSort("usage_count")}
                        >
                          Nutzungen <SortIcon k="usage_count" />
                        </th>
                        <th className="text-right pb-2 px-3 whitespace-nowrap">Manuell</th>
                        <th className="text-right pb-2 px-3 whitespace-nowrap">Agent</th>
                        <th
                          className="text-right pb-2 px-3 cursor-pointer hover:text-foreground whitespace-nowrap"
                          onClick={() => toggleSort("time_saved_per_use_seconds")}
                        >
                          Ersparnis/Use <SortIcon k="time_saved_per_use_seconds" />
                        </th>
                        <th
                          className="text-right pb-2 px-3 cursor-pointer hover:text-foreground whitespace-nowrap"
                          onClick={() => toggleSort("total_time_saved_seconds")}
                        >
                          Gesamt gespart <SortIcon k="total_time_saved_seconds" />
                        </th>
                        <th
                          className="text-right pb-2 px-3 cursor-pointer hover:text-foreground whitespace-nowrap"
                          onClick={() => toggleSort("roi_factor")}
                        >
                          ROI <SortIcon k="roi_factor" />
                        </th>
                        <th
                          className="text-right pb-2 px-3 cursor-pointer hover:text-foreground whitespace-nowrap"
                          onClick={() => toggleSort("avg_rating")}
                        >
                          Bewertung <SortIcon k="avg_rating" />
                        </th>
                        <th className="text-right pb-2 pl-3 whitespace-nowrap">Agent / User</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-foreground/[0.04]">
                      {sortedSkills.map((s: any) => (
                        <tr key={s.id} className="hover:bg-foreground/[0.02] transition-colors">
                          <td className="py-2.5 pr-4">
                            <div className="font-medium text-[13px]">{s.name}</div>
                            <div className="text-[11px] text-muted-foreground truncate max-w-[200px]">{s.description}</div>
                          </td>
                          <td className="py-2.5 px-3 text-right text-[12px] text-muted-foreground">
                            <span className={s.usage_count > 0 ? "text-foreground font-medium" : ""}>{s.usage_count ?? 0}</span>
                            {s.period_uses > 0 && s.period_uses < s.usage_count && (
                              <div className="text-[10px] text-muted-foreground/50">{s.period_uses} ({days}d)</div>
                            )}
                          </td>
                          <td className="py-2.5 px-3 text-right text-[12px]">
                            {s.manual_duration_seconds
                              ? <span className="text-amber-400">{fmtSeconds(s.manual_duration_seconds)}</span>
                              : <span className="text-muted-foreground/40">—</span>}
                          </td>
                          <td className="py-2.5 px-3 text-right text-[12px] text-blue-400">
                            {fmtSeconds(s.avg_agent_duration_seconds)}
                          </td>
                          <td className="py-2.5 px-3 text-right text-[12px]">
                            {s.time_saved_per_use_seconds
                              ? <span className="text-emerald-400 font-medium">{fmtSeconds(s.time_saved_per_use_seconds)}</span>
                              : <span className="text-muted-foreground/40">—</span>}
                          </td>
                          <td className="py-2.5 px-3 text-right text-[12px]">
                            {s.total_time_saved_seconds > 0
                              ? <span className="text-emerald-400 font-semibold">{fmtSeconds(s.total_time_saved_seconds)}</span>
                              : <span className="text-muted-foreground/40">0s</span>}
                          </td>
                          <td className="py-2.5 px-3 text-right text-[12px]">
                            {s.roi_factor
                              ? (
                                <span className={cn(
                                  "inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium",
                                  s.roi_factor >= 5 ? "bg-emerald-500/10 text-emerald-400" :
                                  s.roi_factor >= 2 ? "bg-blue-500/10 text-blue-400" :
                                  "bg-amber-500/10 text-amber-400"
                                )}>
                                  {s.roi_factor}×
                                </span>
                              )
                              : <span className="text-muted-foreground/40">—</span>}
                          </td>
                          <td className="py-2.5 px-3 text-right">
                            <Stars value={s.avg_rating} />
                          </td>
                          <td className="py-2.5 pl-3 text-right text-[11px] text-muted-foreground whitespace-nowrap">
                            {s.avg_agent_self_rating ? `${s.avg_agent_self_rating.toFixed(1)}` : "—"} / {s.avg_user_rating ? `${s.avg_user_rating.toFixed(1)}` : "—"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

            {/* ── Agent performance table ── */}
            <Dialog.Root open={!!selectedAgentId} onOpenChange={(open) => { if (!open) setSelectedAgentId(null); }}>
              <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-5">
                <div className="flex items-center gap-2 mb-4">
                  <Bot className="h-4 w-4 text-primary" />
                  <h2 className="text-sm font-semibold">Agent Performance</h2>
                  <span className="ml-auto text-[11px] text-muted-foreground">Klicken für Details</span>
                </div>
                {agentsData?.agents?.length > 0 ? (
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="border-b border-foreground/[0.06] text-[11px] font-medium text-muted-foreground/70">
                          <th className="text-left pb-2 pr-4">Agent</th>
                          <th className="text-right pb-2 px-3">Tasks</th>
                          <th className="text-right pb-2 px-3">Erfolg</th>
                          <th className="text-right pb-2 px-3">Ø Dauer</th>
                          <th className="text-right pb-2 px-3">Kosten</th>
                          <th className="text-right pb-2 pl-3">Bewertung</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-foreground/[0.04]">
                        {agentsData.agents.filter((a: any) => a.total_tasks > 0).map((a: any) => (
                          <Dialog.Trigger asChild key={a.id}>
                            <tr
                              className="hover:bg-foreground/[0.04] transition-colors cursor-pointer"
                              onClick={() => setSelectedAgentId(a.id)}
                            >
                              <td className="py-2.5 pr-4">
                                <div className="font-medium text-[13px]">{a.name}</div>
                                {a.role && <div className="text-[11px] text-muted-foreground">{a.role}</div>}
                              </td>
                              <td className="py-2.5 px-3 text-right text-[12px]">{a.total_tasks}</td>
                              <td className="py-2.5 px-3 text-right text-[12px]">
                                <span className={cn(
                                  "font-medium",
                                  a.success_rate_pct >= 80 ? "text-emerald-400" :
                                  a.success_rate_pct >= 60 ? "text-amber-400" : "text-red-400"
                                )}>
                                  {a.success_rate_pct}%
                                </span>
                              </td>
                              <td className="py-2.5 px-3 text-right text-[12px] text-muted-foreground">{fmtMs(a.avg_duration_ms)}</td>
                              <td className="py-2.5 px-3 text-right text-[12px] text-muted-foreground">{fmtCost(a.total_cost_usd)}</td>
                              <td className="py-2.5 pl-3 text-right">
                                <Stars value={a.avg_rating} />
                              </td>
                            </tr>
                          </Dialog.Trigger>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <div className="py-12 text-center text-sm text-muted-foreground">
                    Noch keine Agent-Daten im gewählten Zeitraum.
                  </div>
                )}
              </div>

              <AnimatePresence>
                {selectedAgentId && (
                  <AgentDetailModal
                    agentId={selectedAgentId}
                    days={days}
                    onClose={() => setSelectedAgentId(null)}
                  />
                )}
              </AnimatePresence>
            </Dialog.Root>
          </>
        )}
      </main>
    </div>
  );
}

"use client";

import { useState, useEffect, useCallback } from "react";
import {
  BarChart3, Clock, TrendingUp, DollarSign, CheckCircle2,
  Bot, Sparkles, RefreshCw, Timer, Loader2, ChevronUp, ChevronDown,
  Award, Zap,
} from "lucide-react";
import {
  ResponsiveContainer, AreaChart, Area, BarChart, Bar,
  XAxis, YAxis, Tooltip, CartesianGrid, LineChart, Line,
} from "recharts";
import { Header } from "@/components/layout/header";
import { cn } from "@/lib/utils";
import { getAnalyticsOverview, getAnalyticsSkills, getAnalyticsAgents } from "@/lib/api";

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
                          <td className="py-2.5 px-3 text-right text-[12px] text-muted-foreground">{s.period_uses}</td>
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
            <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-5">
              <div className="flex items-center gap-2 mb-4">
                <Bot className="h-4 w-4 text-primary" />
                <h2 className="text-sm font-semibold">Agent Performance</h2>
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
                        <tr key={a.id} className="hover:bg-foreground/[0.02] transition-colors">
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
          </>
        )}
      </main>
    </div>
  );
}

"use client";

import { useState, useEffect } from "react";
import {
  HeartPulse, CheckCircle2, XCircle, AlertTriangle,
  Play, Loader2, Clock, Zap, TrendingUp, RefreshCw,
  Star, DollarSign, Timer, Bot, Activity, AlertOctagon,
} from "lucide-react";
import {
  ResponsiveContainer, AreaChart, Area, BarChart, Bar, LineChart, Line,
  XAxis, YAxis, Tooltip, CartesianGrid,
} from "recharts";
import { Header } from "@/components/layout/header";
import { cn } from "@/lib/utils";
import * as api from "@/lib/api";
import type { HealthDashboard, TestRun, ImprovementReport, AutoMetrics } from "@/lib/api";

function StatusBadge({ status }: { status: string }) {
  const colors: Record<string, string> = {
    healthy: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
    passing: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
    passed: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
    warning: "bg-amber-500/10 text-amber-400 border-amber-500/20",
    degraded: "bg-amber-500/10 text-amber-400 border-amber-500/20",
    skipped: "bg-gray-500/10 text-gray-400 border-gray-500/20",
    error: "bg-red-500/10 text-red-400 border-red-500/20",
    failed: "bg-red-500/10 text-red-400 border-red-500/20",
    critical: "bg-red-500/10 text-red-400 border-red-500/20",
    running: "bg-blue-500/10 text-blue-400 border-blue-500/20",
  };
  return (
    <span className={cn(
      "inline-flex items-center rounded-full border px-2.5 py-1 text-[11px] font-medium",
      colors[status?.toLowerCase()] || "bg-gray-500/10 text-gray-400 border-gray-500/20"
    )}>
      {status}
    </span>
  );
}

function StatusIcon({ status }: { status: string }) {
  const s = status?.toLowerCase();
  if (s === "healthy" || s === "passed" || s === "passing") return <CheckCircle2 className="w-5 h-5 text-emerald-400" />;
  if (s === "warning" || s === "degraded") return <AlertTriangle className="w-5 h-5 text-amber-400" />;
  return <XCircle className="w-5 h-5 text-red-400" />;
}

/* eslint-disable @typescript-eslint/no-explicit-any */
function ChartTooltip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-lg border border-foreground/[0.08] bg-card px-3 py-2 shadow-xl text-xs">
      <p className="text-muted-foreground/60 mb-1">{label}</p>
      {payload.map((p: any, i: number) => (
        <p key={i} style={{ color: p.color }} className="font-medium">
          {p.name}: {typeof p.value === "number" ? p.value.toFixed(2) : p.value}
        </p>
      ))}
    </div>
  );
}
/* eslint-enable @typescript-eslint/no-explicit-any */

export default function HealthPage() {
  const [dashboard, setDashboard] = useState<HealthDashboard | null>(null);
  const [latestRun, setLatestRun] = useState<TestRun | null>(null);
  const [reports, setReports] = useState<ImprovementReport[]>([]);
  const [autoMetrics, setAutoMetrics] = useState<AutoMetrics | null>(null);
  const [days, setDays] = useState(7);
  const [loading, setLoading] = useState(true);
  const [triggering, setTriggering] = useState(false);
  const [testProgress, setTestProgress] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [testRuns, setTestRuns] = useState<TestRun[]>([]);
  const [showHistory, setShowHistory] = useState(false);
  const [expandedRun, setExpandedRun] = useState<string | null>(null);

  const loadTestRunHistory = async () => {
    try {
      const data = await api.getTestRuns();
      setTestRuns(data.runs || []);
    } catch {
      // ignore
    }
  };

  async function load() {
    try {
      setLoading(true);
      setError(null);
      const [d, r, m] = await Promise.allSettled([
        api.getHealthDashboard(),
        api.getLatestTestRun(),
        api.getAutoMetrics(days),
      ]);
      if (d.status === "fulfilled") {
        setDashboard(d.value);
        // Load improvement reports for each agent
        if (d.value.agents?.length) {
          const reportResults = await Promise.allSettled(
            d.value.agents.map((a) => api.getImprovementReport(a.id))
          );
          setReports(
            reportResults
              .filter((r): r is PromiseFulfilledResult<ImprovementReport> => r.status === "fulfilled")
              .map((r) => r.value)
              .filter((r) => r.total_ratings > 0)
          );
        }
      } else {
        setError("Dashboard nicht erreichbar");
      }
      if (r.status === "fulfilled") setLatestRun(r.value);
      if (m.status === "fulfilled") setAutoMetrics(m.value);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, [days]);  // eslint-disable-line react-hooks/exhaustive-deps

  async function handleTriggerTest() {
    setTriggering(true);
    setTestProgress(0);
    setError(null);
    // Estimated timeout (seconds)
    const TIMEOUT_S = 90;
    // Progress interval (visual tick every 500ms)
    const progressInterval = setInterval(() => {
      setTestProgress((p) => Math.min(p + 100 / (TIMEOUT_S * 2), 95));
    }, 500);
    try {
      await api.triggerTestRun();
      const startTime = Date.now();
      const previousId = latestRun?.id;
      let foundNewRun = false;
      while (Date.now() - startTime < TIMEOUT_S * 1000) {
        await new Promise((r) => setTimeout(r, 3000));
        try {
          const newRun = await api.getLatestTestRun();
          if (newRun && newRun.id !== previousId && newRun.status !== "running") {
            setLatestRun(newRun);
            foundNewRun = true;
            setTestProgress(100);
            const dash = await api.getHealthDashboard();
            setDashboard(dash);
            break;
          }
        } catch {
          // continue polling
        }
      }
      if (!foundNewRun) {
        setError("Test läuft noch im Hintergrund — aktualisiere gleich");
      }
    } catch {
      setError("Test konnte nicht gestartet werden");
    } finally {
      clearInterval(progressInterval);
      setTimeout(() => {
        setTriggering(false);
        setTestProgress(0);
      }, 800);
    }
  }

  const overallStatus = dashboard?.status || "unknown";

  // Aggregate rating data for combined chart
  const combinedRatingData = reports.length > 0
    ? reports[0].rating_trend.map((_, i) => {
        const point: Record<string, number | string> = { period: `#${i + 1}` };
        for (const report of reports) {
          if (report.rating_trend[i] !== undefined) {
            point[report.agent_name] = report.rating_trend[i];
          }
        }
        return point;
      })
    : [];

  // Colors for agent lines
  const AGENT_COLORS = ["#34d399", "#60a5fa", "#f472b6", "#fbbf24", "#a78bfa", "#fb923c"];

  return (
    <div className="min-h-screen">
      <Header
        title="Health & Performance"
        subtitle="System-Gesundheit, Self-Tests und Verbesserungsvorschlaege"
      />

      <div className="p-6 space-y-6">
        {/* Overall Status */}
        <div className={cn(
          "rounded-xl border p-6 backdrop-blur-sm",
          overallStatus === "healthy" ? "border-emerald-500/20 bg-emerald-500/5" :
          overallStatus === "degraded" ? "border-amber-500/20 bg-amber-500/5" :
          "border-red-500/20 bg-red-500/5"
        )}>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-4">
              <div className={cn(
                "w-14 h-14 rounded-2xl flex items-center justify-center",
                overallStatus === "healthy" ? "bg-emerald-500/10" :
                overallStatus === "degraded" ? "bg-amber-500/10" :
                "bg-red-500/10"
              )}>
                <HeartPulse className={cn(
                  "w-7 h-7",
                  overallStatus === "healthy" ? "text-emerald-400" :
                  overallStatus === "degraded" ? "text-amber-400" :
                  "text-red-400"
                )} />
              </div>
              <div>
                <h2 className="text-2xl font-semibold">
                  {overallStatus === "healthy" ? "System Gesund" :
                   overallStatus === "degraded" ? "Eingeschraenkt" :
                   loading ? "Laden..." : "Probleme erkannt"}
                </h2>
                <p className="text-sm text-muted-foreground">
                  {dashboard?.agents?.length || 0} Agents registriert
                </p>
              </div>
            </div>
            <div className="flex gap-2">
              <button
                onClick={load}
                className="rounded-xl px-4 py-2 text-sm text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04] flex items-center gap-2"
              >
                <RefreshCw className="w-4 h-4" />
                Aktualisieren
              </button>
              <button
                onClick={handleTriggerTest}
                disabled={triggering}
                className="rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground shadow-lg shadow-primary/20 flex items-center gap-2 disabled:opacity-60 disabled:cursor-not-allowed"
              >
                {triggering ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
                {triggering ? "Läuft..." : "Self-Test starten"}
              </button>
            </div>
          </div>
        </div>

        {error && (
          <div className="rounded-xl border border-red-500/20 bg-red-500/5 p-4 text-sm text-red-400">
            {error}
          </div>
        )}

        {/* Auto-Metrics KPI Summary */}
        {autoMetrics && autoMetrics.total_tasks > 0 && (
          <>
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold text-muted-foreground">Automatische Metriken</h2>
              <div className="flex gap-1 p-1 rounded-lg bg-foreground/[0.03]">
                {[7, 14, 30].map((d) => (
                  <button
                    key={d}
                    onClick={() => setDays(d)}
                    className={cn(
                      "px-3 py-1 text-xs font-medium rounded-md transition-all",
                      days === d ? "bg-background shadow-sm text-foreground" : "text-muted-foreground hover:text-foreground"
                    )}
                  >
                    {d}d
                  </button>
                ))}
              </div>
            </div>

            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-4">
                <div className="flex items-center gap-2 mb-2">
                  <Activity className="w-4 h-4 text-blue-400" />
                  <span className="text-[11px] font-medium text-muted-foreground/70 uppercase tracking-wider">Tasks</span>
                </div>
                <p className="text-2xl font-bold">{autoMetrics.total_tasks}</p>
                <p className="text-[10px] text-muted-foreground/60 mt-1">letzte {days} Tage</p>
              </div>
              <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-4">
                <div className="flex items-center gap-2 mb-2">
                  <CheckCircle2 className="w-4 h-4 text-emerald-400" />
                  <span className="text-[11px] font-medium text-muted-foreground/70 uppercase tracking-wider">Erfolgsquote</span>
                </div>
                <p className="text-2xl font-bold text-emerald-400">{autoMetrics.success_rate}%</p>
                <div className="mt-2 h-1 rounded-full bg-foreground/[0.06] overflow-hidden">
                  <div className="h-full bg-emerald-400" style={{ width: `${autoMetrics.success_rate}%` }} />
                </div>
              </div>
              <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-4">
                <div className="flex items-center gap-2 mb-2">
                  <DollarSign className="w-4 h-4 text-amber-400" />
                  <span className="text-[11px] font-medium text-muted-foreground/70 uppercase tracking-wider">Kosten</span>
                </div>
                <p className="text-2xl font-bold">${autoMetrics.total_cost_usd.toFixed(2)}</p>
                <p className="text-[10px] text-muted-foreground/60 mt-1">
                  ${autoMetrics.total_tasks > 0 ? (autoMetrics.total_cost_usd / autoMetrics.total_tasks).toFixed(3) : "0"}/Task
                </p>
              </div>
              <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-4">
                <div className="flex items-center gap-2 mb-2">
                  <Bot className="w-4 h-4 text-violet-400" />
                  <span className="text-[11px] font-medium text-muted-foreground/70 uppercase tracking-wider">Aktive Agents</span>
                </div>
                <p className="text-2xl font-bold">{autoMetrics.agents.length}</p>
                <p className="text-[10px] text-muted-foreground/60 mt-1">mit Tasks</p>
              </div>
            </div>

            {/* Charts grid */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              {/* Daily tasks + success rate */}
              <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-5">
                <h3 className="text-lg font-semibold mb-1 flex items-center gap-2">
                  <Activity className="w-5 h-5 text-blue-400" />
                  Tasks pro Tag
                </h3>
                <p className="text-[11px] text-muted-foreground/60 mb-4">Total vs. erfolgreich</p>
                <div className="h-52">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={(() => {
                      const allDays = new Set<string>();
                      autoMetrics.agents.forEach((a) => a.daily.forEach((d) => allDays.add(d.date)));
                      return Array.from(allDays).sort().map((date) => {
                        const total = autoMetrics.agents.reduce((s, a) => s + (a.daily.find((d) => d.date === date)?.total || 0), 0);
                        const succeeded = autoMetrics.agents.reduce((s, a) => s + (a.daily.find((d) => d.date === date)?.succeeded || 0), 0);
                        return {
                          date: date.slice(5),
                          Gesamt: total,
                          Erfolgreich: succeeded,
                          Fehler: total - succeeded,
                        };
                      });
                    })()}>
                      <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                      <XAxis dataKey="date" tick={{ fontSize: 10, fill: "#666" }} />
                      <YAxis tick={{ fontSize: 10, fill: "#666" }} />
                      <Tooltip content={<ChartTooltip />} />
                      <Bar dataKey="Erfolgreich" stackId="a" fill="#34d399" radius={[0, 0, 0, 0]} />
                      <Bar dataKey="Fehler" stackId="a" fill="#f87171" radius={[4, 4, 0, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </div>

              {/* Daily cost trend */}
              <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-5">
                <h3 className="text-lg font-semibold mb-1 flex items-center gap-2">
                  <DollarSign className="w-5 h-5 text-amber-400" />
                  Kosten pro Tag
                </h3>
                <p className="text-[11px] text-muted-foreground/60 mb-4">USD je Tag</p>
                <div className="h-52">
                  <ResponsiveContainer width="100%" height="100%">
                    <AreaChart data={(() => {
                      const allDays = new Set<string>();
                      autoMetrics.agents.forEach((a) => a.daily.forEach((d) => allDays.add(d.date)));
                      return Array.from(allDays).sort().map((date) => {
                        const cost = autoMetrics.agents.reduce((s, a) => s + (a.daily.find((d) => d.date === date)?.cost || 0), 0);
                        return { date: date.slice(5), Kosten: Math.round(cost * 10000) / 10000 };
                      });
                    })()}>
                      <defs>
                        <linearGradient id="costGrad" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%" stopColor="#fbbf24" stopOpacity={0.4} />
                          <stop offset="95%" stopColor="#fbbf24" stopOpacity={0} />
                        </linearGradient>
                      </defs>
                      <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                      <XAxis dataKey="date" tick={{ fontSize: 10, fill: "#666" }} />
                      <YAxis tick={{ fontSize: 10, fill: "#666" }} />
                      <Tooltip content={<ChartTooltip />} />
                      <Area type="monotone" dataKey="Kosten" stroke="#fbbf24" fill="url(#costGrad)" strokeWidth={2} />
                    </AreaChart>
                  </ResponsiveContainer>
                </div>
              </div>

              {/* Success rate trend */}
              <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-5">
                <h3 className="text-lg font-semibold mb-1 flex items-center gap-2">
                  <CheckCircle2 className="w-5 h-5 text-emerald-400" />
                  Erfolgsquote-Verlauf
                </h3>
                <p className="text-[11px] text-muted-foreground/60 mb-4">% pro Tag</p>
                <div className="h-52">
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={(() => {
                      const allDays = new Set<string>();
                      autoMetrics.agents.forEach((a) => a.daily.forEach((d) => allDays.add(d.date)));
                      return Array.from(allDays).sort().map((date) => {
                        const total = autoMetrics.agents.reduce((s, a) => s + (a.daily.find((d) => d.date === date)?.total || 0), 0);
                        const succeeded = autoMetrics.agents.reduce((s, a) => s + (a.daily.find((d) => d.date === date)?.succeeded || 0), 0);
                        return {
                          date: date.slice(5),
                          Erfolgsquote: total > 0 ? Math.round((succeeded / total) * 1000) / 10 : 0,
                        };
                      });
                    })()}>
                      <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                      <XAxis dataKey="date" tick={{ fontSize: 10, fill: "#666" }} />
                      <YAxis domain={[0, 100]} tick={{ fontSize: 10, fill: "#666" }} />
                      <Tooltip content={<ChartTooltip />} />
                      <Line type="monotone" dataKey="Erfolgsquote" stroke="#34d399" strokeWidth={2} dot={{ r: 3 }} />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              </div>

              {/* Avg duration trend */}
              <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-5">
                <h3 className="text-lg font-semibold mb-1 flex items-center gap-2">
                  <Timer className="w-5 h-5 text-blue-400" />
                  Dauer pro Tag
                </h3>
                <p className="text-[11px] text-muted-foreground/60 mb-4">Durchschnitt in Sekunden</p>
                <div className="h-52">
                  <ResponsiveContainer width="100%" height="100%">
                    <AreaChart data={(() => {
                      const allDays = new Set<string>();
                      autoMetrics.agents.forEach((a) => a.daily.forEach((d) => allDays.add(d.date)));
                      return Array.from(allDays).sort().map((date) => {
                        const durations = autoMetrics.agents
                          .map((a) => a.daily.find((d) => d.date === date)?.avg_duration_ms || 0)
                          .filter((d) => d > 0);
                        const avg = durations.length ? durations.reduce((s, d) => s + d, 0) / durations.length : 0;
                        return { date: date.slice(5), Dauer: Math.round(avg / 1000) };
                      });
                    })()}>
                      <defs>
                        <linearGradient id="durGrad" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%" stopColor="#60a5fa" stopOpacity={0.4} />
                          <stop offset="95%" stopColor="#60a5fa" stopOpacity={0} />
                        </linearGradient>
                      </defs>
                      <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                      <XAxis dataKey="date" tick={{ fontSize: 10, fill: "#666" }} />
                      <YAxis tick={{ fontSize: 10, fill: "#666" }} />
                      <Tooltip content={<ChartTooltip />} />
                      <Area type="monotone" dataKey="Dauer" stroke="#60a5fa" fill="url(#durGrad)" strokeWidth={2} />
                    </AreaChart>
                  </ResponsiveContainer>
                </div>
              </div>
            </div>

            {/* Per-Agent breakdown */}
            <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-5">
              <h3 className="text-lg font-semibold mb-4 flex items-center gap-2">
                <Bot className="w-5 h-5 text-violet-400" />
                Agent-Vergleich
              </h3>
              <div className="space-y-3">
                {autoMetrics.agents.map((a, i) => {
                  const color = AGENT_COLORS[i % AGENT_COLORS.length];
                  return (
                    <div key={a.agent_id} className="p-3 rounded-lg bg-foreground/[0.02] border border-foreground/[0.04]">
                      <div className="flex items-center justify-between mb-2">
                        <div className="flex items-center gap-2">
                          <div className="w-2 h-2 rounded-full" style={{ backgroundColor: color }} />
                          <span className="text-sm font-medium">{a.agent_name}</span>
                        </div>
                        <div className="flex items-center gap-4 text-xs">
                          <span className="text-muted-foreground/60">{a.total_tasks} Tasks</span>
                          <span className={cn(
                            "font-medium",
                            a.success_rate >= 80 ? "text-emerald-400" :
                            a.success_rate >= 50 ? "text-amber-400" : "text-red-400"
                          )}>
                            {a.success_rate}% Erfolg
                          </span>
                          <span className="text-amber-400">${(a.total_cost_usd || 0).toFixed(2)}</span>
                          {a.avg_duration_ms && (
                            <span className="text-blue-400">{Math.round(a.avg_duration_ms / 1000)}s</span>
                          )}
                        </div>
                      </div>
                      <div className="h-1.5 rounded-full bg-foreground/[0.06] overflow-hidden flex">
                        <div
                          className="h-full bg-emerald-400"
                          style={{ width: `${(a.succeeded / a.total_tasks) * 100}%` }}
                        />
                        <div
                          className="h-full bg-red-400"
                          style={{ width: `${(a.failed / a.total_tasks) * 100}%` }}
                        />
                      </div>
                      {a.top_errors.length > 0 && (
                        <details className="mt-2">
                          <summary className="text-[10px] text-red-400/80 cursor-pointer flex items-center gap-1 hover:text-red-400">
                            <AlertOctagon className="w-3 h-3" />
                            {a.top_errors.length} Fehler-Pattern
                          </summary>
                          <div className="mt-1.5 space-y-1 pl-4">
                            {a.top_errors.map((err, idx) => (
                              <div key={idx} className="text-[10px] text-muted-foreground/70 flex items-start gap-2">
                                <span className="text-red-400/60 font-mono">{err.count}x</span>
                                <span className="truncate flex-1">{err.error}</span>
                              </div>
                            ))}
                          </div>
                        </details>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          </>
        )}

        {/* Performance Charts */}
        {reports.length > 0 && (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* Rating Trend Chart */}
            <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-5">
              <h3 className="text-lg font-semibold mb-1 flex items-center gap-2">
                <Star className="w-5 h-5 text-amber-400" />
                Rating-Verlauf
              </h3>
              <p className="text-[11px] text-muted-foreground/60 mb-4">Durchschnitt pro 5 Tasks</p>
              <div className="h-52">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={combinedRatingData}>
                    <defs>
                      {reports.map((r, i) => (
                        <linearGradient key={r.agent_id} id={`grad-${r.agent_id}`} x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%" stopColor={AGENT_COLORS[i % AGENT_COLORS.length]} stopOpacity={0.3} />
                          <stop offset="95%" stopColor={AGENT_COLORS[i % AGENT_COLORS.length]} stopOpacity={0} />
                        </linearGradient>
                      ))}
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                    <XAxis dataKey="period" tick={{ fontSize: 10, fill: "#666" }} />
                    <YAxis domain={[0, 5]} tick={{ fontSize: 10, fill: "#666" }} />
                    <Tooltip content={<ChartTooltip />} />
                    {reports.map((r, i) => (
                      <Area
                        key={r.agent_id}
                        type="monotone"
                        dataKey={r.agent_name}
                        stroke={AGENT_COLORS[i % AGENT_COLORS.length]}
                        fill={`url(#grad-${r.agent_id})`}
                        strokeWidth={2}
                      />
                    ))}
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            </div>

            {/* Agent Performance Cards */}
            <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-5">
              <h3 className="text-lg font-semibold mb-4 flex items-center gap-2">
                <Bot className="w-5 h-5 text-primary" />
                Agent Performance
              </h3>
              <div className="space-y-3">
                {reports.map((report, i) => (
                  <div key={report.agent_id} className="p-3 rounded-lg bg-foreground/[0.02] border border-foreground/[0.04]">
                    <div className="flex items-center justify-between mb-2">
                      <div className="flex items-center gap-2">
                        <div className="w-2 h-2 rounded-full" style={{ backgroundColor: AGENT_COLORS[i % AGENT_COLORS.length] }} />
                        <span className="text-sm font-medium">{report.agent_name}</span>
                      </div>
                      <span className="text-xs text-muted-foreground/60">{report.total_ratings} Bewertungen</span>
                    </div>
                    <div className="grid grid-cols-3 gap-2">
                      <div className="flex items-center gap-1.5">
                        <Star className="w-3 h-3 text-amber-400" />
                        <span className="text-xs font-medium">{report.average_rating?.toFixed(1) || "-"}/5</span>
                      </div>
                      <div className="flex items-center gap-1.5">
                        <DollarSign className="w-3 h-3 text-emerald-400" />
                        <span className="text-xs font-medium">
                          {report.cost_trend.filter(Boolean).length > 0
                            ? `$${(report.cost_trend.filter((c): c is number => c !== null).slice(-1)[0] || 0).toFixed(3)}`
                            : "-"
                          }
                        </span>
                      </div>
                      <div className="flex items-center gap-1.5">
                        <Timer className="w-3 h-3 text-blue-400" />
                        <span className="text-xs font-medium">
                          {report.duration_trend.filter(Boolean).length > 0
                            ? `${Math.round((report.duration_trend.filter((d): d is number => d !== null).slice(-1)[0] || 0) / 1000)}s`
                            : "-"
                          }
                        </span>
                      </div>
                    </div>
                    {/* Mini rating bar */}
                    <div className="mt-2 h-1.5 rounded-full bg-foreground/[0.06] overflow-hidden">
                      <div
                        className="h-full rounded-full transition-all"
                        style={{
                          width: `${((report.average_rating || 0) / 5) * 100}%`,
                          backgroundColor: AGENT_COLORS[i % AGENT_COLORS.length],
                        }}
                      />
                    </div>
                    {report.top_issues.length > 0 && (
                      <div className="mt-2 text-[10px] text-red-400/80">
                        Probleme: {report.top_issues.slice(0, 2).join(", ")}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>

            {/* Cost Trend Chart */}
            {reports.some((r) => r.cost_trend.some(Boolean)) && (
              <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-5">
                <h3 className="text-lg font-semibold mb-1 flex items-center gap-2">
                  <DollarSign className="w-5 h-5 text-emerald-400" />
                  Kosten-Verlauf
                </h3>
                <p className="text-[11px] text-muted-foreground/60 mb-4">Durchschnittliche Task-Kosten (USD)</p>
                <div className="h-52">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={reports[0].cost_trend.map((_, i) => {
                      const point: Record<string, number | string | null> = { period: `#${i + 1}` };
                      for (const report of reports) {
                        point[report.agent_name] = report.cost_trend[i];
                      }
                      return point;
                    })}>
                      <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                      <XAxis dataKey="period" tick={{ fontSize: 10, fill: "#666" }} />
                      <YAxis tick={{ fontSize: 10, fill: "#666" }} />
                      <Tooltip content={<ChartTooltip />} />
                      {reports.map((r, i) => (
                        <Bar
                          key={r.agent_id}
                          dataKey={r.agent_name}
                          fill={AGENT_COLORS[i % AGENT_COLORS.length]}
                          radius={[4, 4, 0, 0]}
                          opacity={0.8}
                        />
                      ))}
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </div>
            )}

            {/* Duration Trend Chart */}
            {reports.some((r) => r.duration_trend.some(Boolean)) && (
              <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-5">
                <h3 className="text-lg font-semibold mb-1 flex items-center gap-2">
                  <Timer className="w-5 h-5 text-blue-400" />
                  Dauer-Verlauf
                </h3>
                <p className="text-[11px] text-muted-foreground/60 mb-4">Durchschnittliche Task-Dauer (Sekunden)</p>
                <div className="h-52">
                  <ResponsiveContainer width="100%" height="100%">
                    <AreaChart data={reports[0].duration_trend.map((_, i) => {
                      const point: Record<string, number | string | null> = { period: `#${i + 1}` };
                      for (const report of reports) {
                        const val = report.duration_trend[i];
                        point[report.agent_name] = val !== null ? Math.round(val / 1000) : null;
                      }
                      return point;
                    })}>
                      <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                      <XAxis dataKey="period" tick={{ fontSize: 10, fill: "#666" }} />
                      <YAxis tick={{ fontSize: 10, fill: "#666" }} />
                      <Tooltip content={<ChartTooltip />} />
                      {reports.map((r, i) => (
                        <Area
                          key={r.agent_id}
                          type="monotone"
                          dataKey={r.agent_name}
                          stroke={AGENT_COLORS[i % AGENT_COLORS.length]}
                          fill={AGENT_COLORS[i % AGENT_COLORS.length]}
                          fillOpacity={0.15}
                          strokeWidth={2}
                        />
                      ))}
                    </AreaChart>
                  </ResponsiveContainer>
                </div>
              </div>
            )}
          </div>
        )}

        {/* No data hint */}
        {!loading && reports.length === 0 && (
          <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-8 text-center">
            <Star className="w-8 h-8 text-amber-400/40 mx-auto mb-3" />
            <p className="text-sm text-muted-foreground mb-1">Noch keine Performance-Daten</p>
            <p className="text-[11px] text-muted-foreground/60">
              Bewerte abgeschlossene Tasks via Telegram (1-5 Sterne), um Charts zu sehen.
            </p>
          </div>
        )}

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* Agent Status */}
          <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-5">
            <h3 className="text-lg font-semibold mb-4 flex items-center gap-2">
              <Zap className="w-5 h-5 text-primary" />
              Agent Status
            </h3>
            {loading ? (
              <div className="flex items-center justify-center py-8">
                <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
              </div>
            ) : dashboard?.agents?.length ? (
              <div className="space-y-3">
                {dashboard.agents.map((agent) => (
                  <div key={agent.id} className="flex items-center justify-between p-3 rounded-lg bg-foreground/[0.02] border border-foreground/[0.04]">
                    <div className="flex items-center gap-3">
                      <StatusIcon status={agent.health || agent.state} />
                      <div>
                        <p className="text-sm font-medium">{agent.name}</p>
                        <p className="text-[11px] text-muted-foreground/70">{agent.id}</p>
                      </div>
                    </div>
                    <StatusBadge status={agent.health || agent.state} />
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-sm text-muted-foreground text-center py-4">Keine Agents gefunden</p>
            )}
          </div>

          {/* Latest Test Run */}
          <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-5">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-lg font-semibold flex items-center gap-2">
                <CheckCircle2 className="w-5 h-5 text-primary" />
                Letzter Self-Test
              </h3>
              <button
                onClick={() => { setShowHistory(true); loadTestRunHistory(); }}
                className="text-[11px] text-muted-foreground hover:text-foreground underline underline-offset-2"
              >
                Alle Runs anzeigen
              </button>
            </div>
            {loading ? (
              <div className="flex items-center justify-center py-8">
                <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
              </div>
            ) : latestRun ? (
              <div className="space-y-4">
                <div className="flex items-center justify-between">
                  <StatusBadge status={latestRun.status} />
                  <span className="text-[11px] text-muted-foreground/70 flex items-center gap-1">
                    <Clock className="w-3 h-3" />
                    {new Date(latestRun.created_at).toLocaleString("de-DE")}
                  </span>
                </div>
                <div className="grid grid-cols-3 gap-3">
                  <div className="text-center p-3 rounded-lg bg-emerald-500/5 border border-emerald-500/10">
                    <p className="text-2xl font-bold text-emerald-400">{latestRun.passed}</p>
                    <p className="text-[11px] text-muted-foreground/70">Bestanden</p>
                  </div>
                  <div className="text-center p-3 rounded-lg bg-red-500/5 border border-red-500/10">
                    <p className="text-2xl font-bold text-red-400">{latestRun.failed}</p>
                    <p className="text-[11px] text-muted-foreground/70">Fehlgeschlagen</p>
                  </div>
                  <div className="text-center p-3 rounded-lg bg-gray-500/5 border border-gray-500/10">
                    <p className="text-2xl font-bold text-gray-400">{latestRun.skipped}</p>
                    <p className="text-[11px] text-muted-foreground/70">Uebersprungen</p>
                  </div>
                </div>
                {latestRun.results?.length > 0 && (
                  <div className="space-y-2 max-h-60 overflow-y-auto">
                    {latestRun.results.map((r, i) => (
                      <div key={i} className="flex items-center justify-between p-2 rounded-lg bg-foreground/[0.02] text-sm">
                        <div className="flex items-center gap-2">
                          {r.status === "passed" ? <CheckCircle2 className="w-4 h-4 text-emerald-400" /> :
                           r.status === "failed" ? <XCircle className="w-4 h-4 text-red-400" /> :
                           <AlertTriangle className="w-4 h-4 text-gray-400" />}
                          <span>{r.name}</span>
                        </div>
                        {r.message && <span className="text-[11px] text-muted-foreground/70 truncate max-w-[200px]">{r.message}</span>}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ) : (
              <p className="text-sm text-muted-foreground text-center py-4">Noch keine Tests gelaufen</p>
            )}
          </div>
        </div>

        {/* Improvement Suggestions */}
        {dashboard?.improvements && dashboard.improvements.length > 0 && (
          <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-5">
            <h3 className="text-lg font-semibold mb-4 flex items-center gap-2">
              <TrendingUp className="w-5 h-5 text-primary" />
              Verbesserungsvorschlaege
            </h3>
            <div className="space-y-3">
              {dashboard.improvements.map((imp, i) => (
                <div key={i} className="flex items-start gap-3 p-3 rounded-lg bg-foreground/[0.02] border border-foreground/[0.04]">
                  <div className={cn(
                    "w-2 h-2 rounded-full mt-2 shrink-0",
                    imp.priority === "high" ? "bg-red-400" :
                    imp.priority === "medium" ? "bg-amber-400" :
                    "bg-blue-400"
                  )} />
                  <div>
                    <p className="text-sm">{imp.suggestion}</p>
                    <p className="text-[11px] text-muted-foreground/70 mt-1">Agent: {imp.agent_id}</p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Test Run History Modal */}
      {showHistory && (
        <div
          className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm flex items-center justify-center p-4"
          onClick={() => setShowHistory(false)}
        >
          <div
            className="w-full max-w-4xl max-h-[85vh] rounded-2xl border border-foreground/[0.08] bg-card shadow-2xl flex flex-col"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between px-6 py-4 border-b border-foreground/[0.06]">
              <div>
                <h2 className="text-lg font-semibold">Self-Test Historie</h2>
                <p className="text-[11px] text-muted-foreground/70">Alle bisherigen Test-Durchläufe</p>
              </div>
              <button
                onClick={() => setShowHistory(false)}
                className="rounded-lg p-2 text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04]"
              >
                <XCircle className="w-5 h-5" />
              </button>
            </div>
            <div className="flex-1 overflow-y-auto p-4 space-y-2">
              {testRuns.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
                  <Loader2 className="w-6 h-6 animate-spin mb-2" />
                  <p className="text-sm">Laden...</p>
                </div>
              ) : (
                testRuns.map((run) => {
                  const isExpanded = expandedRun === String(run.id);
                  return (
                    <div key={run.id} className="rounded-lg border border-foreground/[0.06] bg-foreground/[0.02]">
                      <button
                        onClick={() => setExpandedRun(isExpanded ? null : String(run.id))}
                        className="w-full flex items-center justify-between p-3 text-left hover:bg-foreground/[0.02] transition-all"
                      >
                        <div className="flex items-center gap-3">
                          <StatusBadge status={run.status} />
                          <div>
                            <p className="text-sm font-medium">
                              {run.passed}/{run.passed + run.failed + run.skipped} passed
                            </p>
                            <p className="text-[10px] text-muted-foreground/60">
                              {new Date(run.created_at).toLocaleString("de-DE")}
                              {run.duration_ms && ` · ${run.duration_ms}ms`}
                            </p>
                          </div>
                        </div>
                        <div className="flex items-center gap-3 text-xs">
                          <span className="text-emerald-400">✓ {run.passed}</span>
                          {run.failed > 0 && <span className="text-red-400">✗ {run.failed}</span>}
                          {run.skipped > 0 && <span className="text-muted-foreground">⊘ {run.skipped}</span>}
                        </div>
                      </button>
                      {isExpanded && run.results && run.results.length > 0 && (
                        <div className="border-t border-foreground/[0.06] p-3 space-y-1.5 max-h-80 overflow-y-auto">
                          {run.results.map((r, i) => (
                            <div key={i} className="flex items-start gap-2 p-2 rounded bg-foreground/[0.02] text-xs">
                              {r.status === "passed" ? <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400 shrink-0 mt-0.5" /> :
                               r.status === "failed" ? <XCircle className="w-3.5 h-3.5 text-red-400 shrink-0 mt-0.5" /> :
                               <AlertTriangle className="w-3.5 h-3.5 text-gray-400 shrink-0 mt-0.5" />}
                              <div className="flex-1 min-w-0">
                                <p className="font-medium">{r.name}</p>
                                {r.message && <p className="text-[10px] text-muted-foreground/70 mt-0.5">{r.message}</p>}
                              </div>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  );
                })
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

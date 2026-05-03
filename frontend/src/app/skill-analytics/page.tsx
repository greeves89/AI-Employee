"use client";

import { useState, useEffect, useCallback } from "react";
import {
  Sparkles, Clock, DollarSign, TrendingUp,
  RefreshCw, Loader2, ChevronUp, ChevronDown,
  X, Layers, Hash, Zap, Award,
} from "lucide-react";
import {
  ResponsiveContainer, AreaChart, Area, BarChart, Bar,
  XAxis, YAxis, Tooltip, CartesianGrid, ComposedChart,
  Cell, PieChart, Pie, Legend,
} from "recharts";
import * as Dialog from "@radix-ui/react-dialog";
import { motion, AnimatePresence } from "framer-motion";
import { Header } from "@/components/layout/header";
import { cn } from "@/lib/utils";
import { getAnalyticsSkills, getSkillTrend } from "@/lib/api";

/* eslint-disable @typescript-eslint/no-explicit-any */

// ── helpers ──────────────────────────────────────────────────────────────────

function fmtSeconds(s: number | null | undefined): string {
  if (!s) return "—";
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.round(s / 60)}min`;
  return `${(s / 3600).toFixed(1)}h`;
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

const CATEGORY_COLORS: Record<string, string> = {
  routine: "#3b82f6",
  template: "#a855f7",
  workflow: "#10b981",
  pattern: "#f59e0b",
  recipe: "#ec4899",
  tool: "#06b6d4",
};

const CATEGORY_LABELS: Record<string, string> = {
  routine: "Routine",
  template: "Template",
  workflow: "Workflow",
  pattern: "Pattern",
  recipe: "Recipe",
  tool: "Tool",
};

// ── Skill Detail Modal ───────────────────────────────────────────────────────

function SkillDetailModal({
  skill, days, onClose,
}: {
  skill: any; days: number; onClose: () => void;
}) {
  const [trend, setTrend] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getSkillTrend(skill.id, Math.max(days, 60))
      .then(setTrend)
      .catch(console.error)
      .finally(() => setLoading(false));
  }, [skill.id, days]);

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

          {/* Header */}
          <div className="mb-5">
            <div className="flex items-center gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary/10">
                <Sparkles className="h-5 w-5 text-primary" />
              </div>
              <div>
                <Dialog.Title className="text-base font-semibold">{skill.name}</Dialog.Title>
                <p className="text-[11px] text-muted-foreground">{skill.description}</p>
              </div>
              <span className={cn(
                "ml-auto inline-flex items-center rounded-full border px-2.5 py-1 text-[11px] font-medium",
                "bg-primary/10 text-primary border-primary/20"
              )}>
                {CATEGORY_LABELS[skill.category] || skill.category}
              </span>
            </div>
          </div>

          {/* Summary stats */}
          <div className="grid grid-cols-3 gap-3 mb-5">
            {[
              { label: "Nutzungen gesamt", value: String(skill.usage_count ?? 0) },
              { label: `Nutzungen (${days}d)`, value: String(skill.period_uses ?? 0) },
              { label: "Bewertung", value: skill.avg_rating ? `${skill.avg_rating.toFixed(1)}/5` : "—" },
              { label: "Manuell", value: fmtSeconds(skill.manual_duration_seconds) },
              { label: "Agent-Dauer", value: fmtSeconds(skill.avg_agent_duration_seconds) },
              { label: "ROI-Faktor", value: skill.roi_factor ? `${skill.roi_factor}×` : "—" },
              { label: "Helpfulness", value: skill.avg_helpfulness ? `${skill.avg_helpfulness.toFixed(1)}/5` : "—" },
              { label: "Gesamt gespart", value: fmtSeconds(skill.total_time_saved_seconds) },
              { label: "Gesamtkosten", value: fmtCost(skill.total_cost_usd) },
            ].map((s) => (
              <div key={s.label} className="rounded-xl border border-foreground/[0.06] bg-foreground/[0.02] p-3">
                <p className="text-[10px] font-medium text-muted-foreground/70">{s.label}</p>
                <p className="mt-0.5 text-lg font-semibold">{s.value}</p>
              </div>
            ))}
          </div>

          {/* Weekly trend chart */}
          {loading ? (
            <div className="flex items-center justify-center py-16">
              <Loader2 className="h-7 w-7 animate-spin text-muted-foreground" />
            </div>
          ) : trend?.trend?.length > 0 ? (
            <div className="space-y-4">
              <div>
                <p className="text-[11px] font-medium text-muted-foreground/70 mb-2">
                  Wöchentliche Nutzung & Qualität
                </p>
                <ResponsiveContainer width="100%" height={200}>
                  <ComposedChart data={trend.trend}>
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                    <XAxis dataKey="week" tick={{ fontSize: 10 }} stroke="rgba(255,255,255,0.2)" />
                    <YAxis yAxisId="left" tick={{ fontSize: 10 }} stroke="rgba(255,255,255,0.2)" />
                    <YAxis yAxisId="right" orientation="right" domain={[0, 5]} tick={{ fontSize: 10 }} stroke="rgba(255,255,255,0.2)" />
                    <Tooltip content={<ChartTooltipCustom />} />
                    <Bar yAxisId="left" dataKey="uses" name="Nutzungen" fill="#3b82f6" radius={[4, 4, 0, 0]} opacity={0.7} />
                    <Area yAxisId="right" type="monotone" dataKey="avg_helpfulness" name="Helpfulness" stroke="#10b981" fill="none" strokeWidth={2} />
                    <Area yAxisId="right" type="monotone" dataKey="avg_user_rating" name="User Rating" stroke="#f59e0b" fill="none" strokeWidth={2} strokeDasharray="5 5" />
                  </ComposedChart>
                </ResponsiveContainer>
              </div>

              {/* Time saved trend */}
              <div>
                <p className="text-[11px] font-medium text-muted-foreground/70 mb-2">
                  Zeitersparnis pro Woche
                </p>
                <ResponsiveContainer width="100%" height={140}>
                  <AreaChart data={trend.trend}>
                    <defs>
                      <linearGradient id="timeSavedGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#10b981" stopOpacity={0.3} />
                        <stop offset="95%" stopColor="#10b981" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                    <XAxis dataKey="week" tick={{ fontSize: 10 }} stroke="rgba(255,255,255,0.2)" />
                    <YAxis tick={{ fontSize: 10 }} stroke="rgba(255,255,255,0.2)" tickFormatter={(v) => fmtSeconds(v)} />
                    <Tooltip content={<ChartTooltipCustom />} />
                    <Area type="monotone" dataKey="time_saved_seconds" name="Gespart" stroke="#10b981" fill="url(#timeSavedGrad)" strokeWidth={2} />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            </div>
          ) : (
            <p className="text-center text-sm text-muted-foreground py-8">
              Noch keine Trend-Daten verfügbar.
            </p>
          )}
        </motion.div>
      </Dialog.Content>
    </Dialog.Portal>
  );
}

// ── main page ────────────────────────────────────────────────────────────────

type SortKey = "usage_count" | "period_uses" | "avg_rating" | "avg_helpfulness" | "total_time_saved_seconds" | "roi_factor" | "total_cost_usd";

export default function SkillAnalyticsPage() {
  const [days, setDays] = useState(30);
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [sortKey, setSortKey] = useState<SortKey>("total_time_saved_seconds");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const [selectedSkill, setSelectedSkill] = useState<any>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await getAnalyticsSkills(days);
      setData(res);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }, [days]);

  useEffect(() => { load(); }, [load]);

  const skills: any[] = data?.skills ?? [];

  // Aggregated metrics
  const totalUsages = skills.reduce((sum, s) => sum + (s.usage_count ?? 0), 0);
  const periodUsages = skills.reduce((sum, s) => sum + (s.period_uses ?? 0), 0);
  const totalTimeSaved = skills.reduce((sum, s) => sum + (s.total_time_saved_seconds ?? 0), 0);
  const totalCost = skills.reduce((sum, s) => sum + (s.total_cost_usd ?? 0), 0);
  const avgHelpfulness = skills.filter(s => s.avg_helpfulness).length > 0
    ? skills.filter(s => s.avg_helpfulness).reduce((sum, s) => sum + s.avg_helpfulness, 0) / skills.filter(s => s.avg_helpfulness).length
    : null;
  const avgRoi = skills.filter(s => s.roi_factor).length > 0
    ? skills.filter(s => s.roi_factor).reduce((sum, s) => sum + s.roi_factor, 0) / skills.filter(s => s.roi_factor).length
    : null;

  // Category distribution
  const categoryMap: Record<string, { count: number; usages: number }> = {};
  for (const s of skills) {
    const cat = s.category || "unknown";
    if (!categoryMap[cat]) categoryMap[cat] = { count: 0, usages: 0 };
    categoryMap[cat].count++;
    categoryMap[cat].usages += s.usage_count ?? 0;
  }
  const categoryData = Object.entries(categoryMap).map(([cat, v]) => ({
    name: CATEGORY_LABELS[cat] || cat,
    value: v.count,
    usages: v.usages,
    color: CATEGORY_COLORS[cat] || "#6b7280",
  }));

  // Top 10 skills by time saved for bar chart
  const topByTimeSaved = [...skills]
    .filter(s => s.total_time_saved_seconds > 0)
    .sort((a, b) => b.total_time_saved_seconds - a.total_time_saved_seconds)
    .slice(0, 10)
    .map(s => ({
      name: s.name.length > 25 ? s.name.slice(0, 22) + "…" : s.name,
      fullName: s.name,
      timeSaved: Math.round((s.total_time_saved_seconds ?? 0) / 60),
      cost: s.total_cost_usd ?? 0,
    }));

  // Top by helpfulness for bar chart
  const topByHelpfulness = [...skills]
    .filter(s => s.avg_helpfulness && s.period_uses > 0)
    .sort((a, b) => b.avg_helpfulness - a.avg_helpfulness)
    .slice(0, 10)
    .map(s => ({
      name: s.name.length > 25 ? s.name.slice(0, 22) + "…" : s.name,
      fullName: s.name,
      helpfulness: s.avg_helpfulness,
      userRating: s.avg_user_rating ?? 0,
      agentRating: s.avg_agent_self_rating ?? 0,
    }));

  // Sorting for the table
  const toggleSort = (key: SortKey) => {
    if (sortKey === key) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else { setSortKey(key); setSortDir("desc"); }
  };

  const sortedSkills = [...skills].sort((a, b) => {
    const av = a[sortKey] ?? -Infinity;
    const bv = b[sortKey] ?? -Infinity;
    return sortDir === "desc" ? bv - av : av - bv;
  });

  const SortIcon = ({ k }: { k: SortKey }) => {
    if (sortKey !== k) return null;
    return sortDir === "desc"
      ? <ChevronDown className="h-3 w-3 inline ml-0.5" />
      : <ChevronUp className="h-3 w-3 inline ml-0.5" />;
  };

  return (
    <div className="flex flex-col h-screen overflow-hidden">
      <Header title="Skill Analytics" />
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

        {loading && !data ? (
          <div className="flex items-center justify-center py-24">
            <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
          </div>
        ) : (
          <>
            {/* ── Summary cards ── */}
            <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-4">
              <StatCard
                label="Aktive Skills"
                value={String(skills.length)}
                sub={`${periodUsages} Nutzungen (${days}d)`}
                icon={Sparkles}
                color="purple"
              />
              <StatCard
                label="Gesamt Nutzungen"
                value={String(totalUsages)}
                sub={`${periodUsages} im Zeitraum`}
                icon={Hash}
                color="blue"
              />
              <StatCard
                label="Zeitersparnis"
                value={fmtSeconds(totalTimeSaved)}
                sub={`im Zeitraum (${days}d)`}
                icon={Clock}
                color="emerald"
              />
              <StatCard
                label="Gesamtkosten"
                value={fmtCost(totalCost)}
                sub={`${days} Tage`}
                icon={DollarSign}
                color="amber"
              />
              <StatCard
                label="Ø Helpfulness"
                value={avgHelpfulness ? `${avgHelpfulness.toFixed(1)}/5` : "—"}
                icon={Award}
                color="cyan"
              />
              <StatCard
                label="Ø ROI-Faktor"
                value={avgRoi ? `${avgRoi.toFixed(1)}×` : "—"}
                sub="manuell vs. agent"
                icon={TrendingUp}
                color="emerald"
              />
            </div>

            {/* ── Charts row ── */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">

              {/* Top Skills by Time Saved */}
              {topByTimeSaved.length > 0 && (
                <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-5">
                  <div className="flex items-center gap-2 mb-4">
                    <Clock className="h-4 w-4 text-emerald-400" />
                    <h2 className="text-sm font-semibold">Top Skills — Zeitersparnis (min)</h2>
                  </div>
                  <ResponsiveContainer width="100%" height={280}>
                    <BarChart data={topByTimeSaved} layout="vertical" margin={{ left: 0, right: 16 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" horizontal={false} />
                      <XAxis type="number" tick={{ fontSize: 10 }} stroke="rgba(255,255,255,0.2)" />
                      <YAxis type="category" dataKey="name" width={140} tick={{ fontSize: 10 }} stroke="rgba(255,255,255,0.2)" />
                      <Tooltip
                        content={({ active, payload }) => {
                          if (!active || !payload?.length) return null;
                          const d = payload[0].payload;
                          return (
                            <div className="rounded-lg border border-border bg-card/95 px-3 py-2 text-xs shadow-xl backdrop-blur-sm">
                              <p className="font-medium mb-1">{d.fullName}</p>
                              <p className="text-emerald-400">Gespart: {d.timeSaved} min</p>
                              <p className="text-amber-400">Kosten: {fmtCost(d.cost)}</p>
                            </div>
                          );
                        }}
                      />
                      <Bar dataKey="timeSaved" fill="#10b981" radius={[0, 4, 4, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              )}

              {/* Category Distribution */}
              {categoryData.length > 0 && (
                <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-5">
                  <div className="flex items-center gap-2 mb-4">
                    <Layers className="h-4 w-4 text-purple-400" />
                    <h2 className="text-sm font-semibold">Kategorien</h2>
                  </div>
                  <div className="flex items-center">
                    <ResponsiveContainer width="50%" height={250}>
                      <PieChart>
                        <Pie
                          data={categoryData}
                          cx="50%"
                          cy="50%"
                          innerRadius={50}
                          outerRadius={90}
                          paddingAngle={3}
                          dataKey="value"
                          stroke="none"
                        >
                          {categoryData.map((entry, i) => (
                            <Cell key={i} fill={entry.color} />
                          ))}
                        </Pie>
                        <Tooltip
                          content={({ active, payload }) => {
                            if (!active || !payload?.length) return null;
                            const d = payload[0].payload;
                            return (
                              <div className="rounded-lg border border-border bg-card/95 px-3 py-2 text-xs shadow-xl backdrop-blur-sm">
                                <p className="font-medium">{d.name}</p>
                                <p>{d.value} Skills, {d.usages} Nutzungen</p>
                              </div>
                            );
                          }}
                        />
                      </PieChart>
                    </ResponsiveContainer>
                    <div className="w-1/2 space-y-2 pl-4">
                      {categoryData.map((c) => (
                        <div key={c.name} className="flex items-center gap-2 text-xs">
                          <div className="h-3 w-3 rounded-sm shrink-0" style={{ backgroundColor: c.color }} />
                          <span className="text-foreground font-medium">{c.name}</span>
                          <span className="text-muted-foreground ml-auto">{c.value} Skills</span>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              )}

              {/* Top Skills by Helpfulness */}
              {topByHelpfulness.length > 0 && (
                <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-5 lg:col-span-2">
                  <div className="flex items-center gap-2 mb-4">
                    <Zap className="h-4 w-4 text-amber-400" />
                    <h2 className="text-sm font-semibold">Qualitätsvergleich — Helpfulness vs. Ratings</h2>
                  </div>
                  <ResponsiveContainer width="100%" height={220}>
                    <ComposedChart data={topByHelpfulness} margin={{ left: 0, right: 16 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                      <XAxis dataKey="name" tick={{ fontSize: 9 }} stroke="rgba(255,255,255,0.2)" angle={-15} textAnchor="end" height={50} />
                      <YAxis domain={[0, 5]} tick={{ fontSize: 10 }} stroke="rgba(255,255,255,0.2)" />
                      <Tooltip content={<ChartTooltipCustom />} />
                      <Bar dataKey="helpfulness" name="Helpfulness" fill="#10b981" radius={[4, 4, 0, 0]} opacity={0.8} />
                      <Bar dataKey="userRating" name="User Rating" fill="#f59e0b" radius={[4, 4, 0, 0]} opacity={0.8} />
                      <Bar dataKey="agentRating" name="Agent Rating" fill="#3b82f6" radius={[4, 4, 0, 0]} opacity={0.8} />
                    </ComposedChart>
                  </ResponsiveContainer>
                </div>
              )}
            </div>

            {/* ── Full skills table ── */}
            <Dialog.Root open={!!selectedSkill} onOpenChange={(open) => { if (!open) setSelectedSkill(null); }}>
              <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-5">
                <div className="flex items-center gap-2 mb-4">
                  <Sparkles className="h-4 w-4 text-primary" />
                  <h2 className="text-sm font-semibold">Alle Skills — Detail-Tabelle</h2>
                  <span className="ml-auto text-[11px] text-muted-foreground">
                    {sortedSkills.length} Skills · Klicken für Trend
                  </span>
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
                          <th className="text-right pb-2 px-3 cursor-pointer hover:text-foreground whitespace-nowrap" onClick={() => toggleSort("usage_count")}>
                            Gesamt <SortIcon k="usage_count" />
                          </th>
                          <th className="text-right pb-2 px-3 cursor-pointer hover:text-foreground whitespace-nowrap" onClick={() => toggleSort("period_uses")}>
                            Zeitraum <SortIcon k="period_uses" />
                          </th>
                          <th className="text-right pb-2 px-3 whitespace-nowrap">Manuell</th>
                          <th className="text-right pb-2 px-3 whitespace-nowrap">Agent</th>
                          <th className="text-right pb-2 px-3 cursor-pointer hover:text-foreground whitespace-nowrap" onClick={() => toggleSort("total_time_saved_seconds")}>
                            Gespart <SortIcon k="total_time_saved_seconds" />
                          </th>
                          <th className="text-right pb-2 px-3 cursor-pointer hover:text-foreground whitespace-nowrap" onClick={() => toggleSort("roi_factor")}>
                            ROI <SortIcon k="roi_factor" />
                          </th>
                          <th className="text-right pb-2 px-3 cursor-pointer hover:text-foreground whitespace-nowrap" onClick={() => toggleSort("avg_helpfulness")}>
                            Helpfulness <SortIcon k="avg_helpfulness" />
                          </th>
                          <th className="text-right pb-2 px-3 cursor-pointer hover:text-foreground whitespace-nowrap" onClick={() => toggleSort("avg_rating")}>
                            Rating <SortIcon k="avg_rating" />
                          </th>
                          <th className="text-right pb-2 px-3 cursor-pointer hover:text-foreground whitespace-nowrap" onClick={() => toggleSort("total_cost_usd")}>
                            Kosten <SortIcon k="total_cost_usd" />
                          </th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-foreground/[0.04]">
                        {sortedSkills.map((s: any) => (
                          <Dialog.Trigger asChild key={s.id}>
                            <tr
                              className="hover:bg-foreground/[0.04] transition-colors cursor-pointer"
                              onClick={() => setSelectedSkill(s)}
                            >
                              <td className="py-2.5 pr-4">
                                <div className="flex items-center gap-2">
                                  <div
                                    className="h-2 w-2 rounded-full shrink-0"
                                    style={{ backgroundColor: CATEGORY_COLORS[s.category] || "#6b7280" }}
                                  />
                                  <div>
                                    <div className="font-medium text-[13px]">{s.name}</div>
                                    <div className="text-[11px] text-muted-foreground truncate max-w-[220px]">{s.description}</div>
                                  </div>
                                </div>
                              </td>
                              <td className="py-2.5 px-3 text-right text-[12px]">
                                <span className={s.usage_count > 0 ? "text-foreground font-medium" : "text-muted-foreground"}>{s.usage_count ?? 0}</span>
                              </td>
                              <td className="py-2.5 px-3 text-right text-[12px]">
                                <span className={s.period_uses > 0 ? "text-blue-400 font-medium" : "text-muted-foreground/40"}>{s.period_uses ?? 0}</span>
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
                              <td className="py-2.5 px-3 text-right text-[12px]">
                                {s.avg_helpfulness
                                  ? <span className="text-cyan-400">{s.avg_helpfulness.toFixed(1)}</span>
                                  : <span className="text-muted-foreground/40">—</span>}
                              </td>
                              <td className="py-2.5 px-3 text-right">
                                <Stars value={s.avg_rating} />
                              </td>
                              <td className="py-2.5 px-3 text-right text-[12px] text-muted-foreground">
                                {fmtCost(s.total_cost_usd)}
                              </td>
                            </tr>
                          </Dialog.Trigger>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>

              <AnimatePresence>
                {selectedSkill && (
                  <SkillDetailModal
                    skill={selectedSkill}
                    days={days}
                    onClose={() => setSelectedSkill(null)}
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

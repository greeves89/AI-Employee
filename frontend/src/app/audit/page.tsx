"use client";

import { useState, useEffect, useCallback } from "react";
import { motion } from "framer-motion";
import {
  ShieldCheck,
  Terminal,
  Ban,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  RefreshCw,
  Filter,
  DollarSign,
  Activity,
  Bot,
} from "lucide-react";
import * as api from "@/lib/api";
import { useAgents } from "@/hooks/use-agents";
import type { AuditLog, AuditSummary } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Header } from "@/components/layout/header";

const EVENT_ICONS: Record<string, React.ReactNode> = {
  COMMAND_EXECUTED: <Terminal className="h-3.5 w-3.5" />,
  COMMAND_APPROVED: <CheckCircle2 className="h-3.5 w-3.5" />,
  COMMAND_DENIED: <XCircle className="h-3.5 w-3.5" />,
  COMMAND_BLOCKED: <Ban className="h-3.5 w-3.5" />,
  AGENT_STARTED: <Bot className="h-3.5 w-3.5" />,
  AGENT_STOPPED: <Bot className="h-3.5 w-3.5" />,
  FILE_WRITTEN: <Activity className="h-3.5 w-3.5" />,
  NETWORK_REQUEST: <Activity className="h-3.5 w-3.5" />,
};

const EVENT_COLORS: Record<string, string> = {
  COMMAND_EXECUTED: "text-blue-400 bg-blue-500/10 border-blue-500/20",
  COMMAND_APPROVED: "text-emerald-400 bg-emerald-500/10 border-emerald-500/20",
  COMMAND_DENIED: "text-red-400 bg-red-500/10 border-red-500/20",
  COMMAND_BLOCKED: "text-orange-400 bg-orange-500/10 border-orange-500/20",
  AGENT_STARTED: "text-violet-400 bg-violet-500/10 border-violet-500/20",
  AGENT_STOPPED: "text-slate-400 bg-slate-500/10 border-slate-500/20",
  FILE_WRITTEN: "text-cyan-400 bg-cyan-500/10 border-cyan-500/20",
  NETWORK_REQUEST: "text-indigo-400 bg-indigo-500/10 border-indigo-500/20",
};

const OUTCOME_BADGE: Record<string, string> = {
  success: "text-emerald-400 bg-emerald-500/10 border-emerald-500/20",
  failure: "text-red-400 bg-red-500/10 border-red-500/20",
  blocked: "text-orange-400 bg-orange-500/10 border-orange-500/20",
};

const itemVariants = {
  hidden: { opacity: 0, y: 8 },
  visible: { opacity: 1, y: 0, transition: { duration: 0.25 } },
};

export default function AuditPage() {
  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [summary, setSummary] = useState<AuditSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const PAGE_SIZE = 50;

  // Filters
  const [agentFilter, setAgentFilter] = useState("");
  const [eventTypeFilter, setEventTypeFilter] = useState("");
  const [outcomeFilter, setOutcomeFilter] = useState("");

  const { agents } = useAgents();

  const load = useCallback(async (resetOffset = false) => {
    const off = resetOffset ? 0 : offset;
    if (resetOffset) setOffset(0);
    try {
      const [logsData, summaryData] = await Promise.all([
        api.getAuditLogs({
          agent_id: agentFilter || undefined,
          event_type: eventTypeFilter || undefined,
          outcome: outcomeFilter || undefined,
          limit: PAGE_SIZE,
          offset: off,
        }),
        api.getAuditSummary(),
      ]);
      setLogs(logsData.logs);
      setTotal(logsData.total);
      setSummary(summaryData);
    } catch {
      // ignore
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [agentFilter, eventTypeFilter, outcomeFilter, offset]);

  useEffect(() => { load(true); }, [agentFilter, eventTypeFilter, outcomeFilter]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { load(); }, [offset]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleRefresh = () => {
    setRefreshing(true);
    load(true);
  };

  const agentName = (id: string) => agents.find((a) => a.id === id)?.name || id;

  return (
    <div>
      <Header
        title="Audit Log"
        subtitle="Agent actions, command history, and compliance trail"
        actions={
          <button
            onClick={handleRefresh}
            className="inline-flex items-center gap-2 rounded-xl px-4 py-2.5 text-sm text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04] transition-all"
          >
            <RefreshCw className={cn("h-4 w-4", refreshing && "animate-spin")} />
            Refresh
          </button>
        }
      />

      <div className="px-8 py-8 space-y-6">
        {/* Summary Cards */}
        {summary && (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <SummaryCard
              label="Total Events"
              value={summary.total}
              icon={<Activity className="h-4 w-4 text-primary" />}
              color="text-primary"
            />
            <SummaryCard
              label="Successful"
              value={summary.by_outcome?.success ?? 0}
              icon={<CheckCircle2 className="h-4 w-4 text-emerald-400" />}
              color="text-emerald-400"
            />
            <SummaryCard
              label="Blocked"
              value={summary.by_outcome?.blocked ?? 0}
              icon={<Ban className="h-4 w-4 text-orange-400" />}
              color="text-orange-400"
            />
            <SummaryCard
              label="Failed"
              value={summary.by_outcome?.failure ?? 0}
              icon={<XCircle className="h-4 w-4 text-red-400" />}
              color="text-red-400"
            />
          </div>
        )}

        {/* Budget Overview */}
        {agents.some((a) => a.budget_usd != null) && (
          <div className="rounded-xl border border-foreground/[0.06] bg-card/80 p-5 backdrop-blur-sm">
            <div className="flex items-center gap-2 mb-4">
              <DollarSign className="h-4 w-4 text-emerald-400" />
              <h3 className="text-sm font-semibold">Agent Budgets</h3>
            </div>
            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
              {agents
                .filter((a) => a.budget_usd != null)
                .map((agent) => {
                  const pct = agent.total_cost_usd != null && agent.budget_usd
                    ? Math.min(100, (agent.total_cost_usd / agent.budget_usd) * 100)
                    : 0;
                  const color = pct >= 90 ? "bg-red-500" : pct >= 75 ? "bg-amber-500" : "bg-emerald-500";
                  return (
                    <div key={agent.id} className="rounded-lg border border-foreground/[0.06] bg-foreground/[0.02] p-3">
                      <p className="text-xs font-medium truncate mb-1">{agent.name}</p>
                      <div className="flex items-center justify-between mb-1.5">
                        <span className="text-[10px] text-muted-foreground">
                          ${(agent.total_cost_usd ?? 0).toFixed(2)} / ${agent.budget_usd!.toFixed(2)}
                        </span>
                        <span className={cn(
                          "text-[10px] font-bold",
                          pct >= 90 ? "text-red-400" : pct >= 75 ? "text-amber-400" : "text-emerald-400"
                        )}>
                          {Math.round(pct)}%
                        </span>
                      </div>
                      <div className="h-1.5 rounded-full bg-foreground/[0.06] overflow-hidden">
                        <div className={cn("h-full rounded-full transition-all", color)} style={{ width: `${pct}%` }} />
                      </div>
                    </div>
                  );
                })}
            </div>
          </div>
        )}

        {/* Event Type Breakdown */}
        {summary && summary.by_event_type && Object.keys(summary.by_event_type).length > 0 && (
          <div className="rounded-xl border border-foreground/[0.06] bg-card/80 p-5 backdrop-blur-sm">
            <div className="flex items-center gap-2 mb-4">
              <ShieldCheck className="h-4 w-4 text-primary" />
              <h3 className="text-sm font-semibold">Event Breakdown</h3>
            </div>
            <div className="flex flex-wrap gap-2">
              {Object.entries(summary.by_event_type)
                .sort((a, b) => b[1] - a[1])
                .map(([type, count]) => (
                  <button
                    key={type}
                    onClick={() => setEventTypeFilter(eventTypeFilter === type ? "" : type)}
                    className={cn(
                      "inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-[11px] font-medium transition-all",
                      eventTypeFilter === type
                        ? (EVENT_COLORS[type] ?? "text-primary bg-primary/10 border-primary/20")
                        : "text-muted-foreground bg-foreground/[0.03] border-foreground/[0.06] hover:bg-foreground/[0.06]"
                    )}
                  >
                    {EVENT_ICONS[type] ?? <Activity className="h-3 w-3" />}
                    {type.replace(/_/g, " ")}
                    <span className="opacity-70">{count}</span>
                  </button>
                ))}
            </div>
          </div>
        )}

        {/* Filters */}
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <Filter className="h-3.5 w-3.5" />
            <span>Filter:</span>
          </div>
          <select
            value={agentFilter}
            onChange={(e) => setAgentFilter(e.target.value)}
            className="rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-primary/25"
          >
            <option value="">All agents</option>
            {agents.map((a) => (
              <option key={a.id} value={a.id}>{a.name}</option>
            ))}
          </select>
          <select
            value={outcomeFilter}
            onChange={(e) => setOutcomeFilter(e.target.value)}
            className="rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-primary/25"
          >
            <option value="">All outcomes</option>
            <option value="success">Success</option>
            <option value="failure">Failure</option>
            <option value="blocked">Blocked</option>
          </select>
          {(agentFilter || eventTypeFilter || outcomeFilter) && (
            <button
              onClick={() => { setAgentFilter(""); setEventTypeFilter(""); setOutcomeFilter(""); }}
              className="text-xs text-muted-foreground hover:text-foreground transition-colors"
            >
              Clear filters
            </button>
          )}
          <span className="ml-auto text-xs text-muted-foreground/60">{total} entries</span>
        </div>

        {/* Log Table */}
        {loading ? (
          <div className="space-y-2">
            {[1, 2, 3, 4, 5].map((i) => (
              <div key={i} className="h-12 animate-pulse rounded-lg bg-foreground/[0.03] border border-foreground/[0.04]" />
            ))}
          </div>
        ) : logs.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-20 text-center">
            <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-foreground/[0.04] mb-4">
              <ShieldCheck className="h-7 w-7 text-muted-foreground/50" />
            </div>
            <p className="text-sm text-muted-foreground">No audit events found</p>
          </div>
        ) : (
          <>
            <motion.div
              className="rounded-xl border border-foreground/[0.06] overflow-hidden"
              initial="hidden"
              animate="visible"
            >
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-foreground/[0.06] bg-foreground/[0.02]">
                    <th className="px-4 py-3 text-left font-medium text-muted-foreground/70">Time</th>
                    <th className="px-4 py-3 text-left font-medium text-muted-foreground/70">Agent</th>
                    <th className="px-4 py-3 text-left font-medium text-muted-foreground/70">Event</th>
                    <th className="px-4 py-3 text-left font-medium text-muted-foreground/70">Command</th>
                    <th className="px-4 py-3 text-left font-medium text-muted-foreground/70">Outcome</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-foreground/[0.04]">
                  {logs.map((log, i) => (
                    <motion.tr
                      key={log.id}
                      variants={itemVariants}
                      custom={i}
                      className="hover:bg-foreground/[0.02] transition-colors"
                    >
                      <td className="px-4 py-2.5 text-muted-foreground/60 whitespace-nowrap font-mono">
                        {new Date(log.created_at).toLocaleString("de-DE", {
                          month: "2-digit", day: "2-digit",
                          hour: "2-digit", minute: "2-digit", second: "2-digit",
                        })}
                      </td>
                      <td className="px-4 py-2.5 font-medium truncate max-w-[140px]">
                        {agentName(log.agent_id)}
                      </td>
                      <td className="px-4 py-2.5">
                        <span className={cn(
                          "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-medium",
                          EVENT_COLORS[log.event_type] ?? "text-muted-foreground bg-foreground/[0.04] border-foreground/[0.06]"
                        )}>
                          {EVENT_ICONS[log.event_type]}
                          {log.event_type.replace(/_/g, " ")}
                        </span>
                      </td>
                      <td className="px-4 py-2.5 text-muted-foreground/80 font-mono max-w-[280px] truncate">
                        {log.command ?? "—"}
                      </td>
                      <td className="px-4 py-2.5">
                        <span className={cn(
                          "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-medium",
                          OUTCOME_BADGE[log.outcome] ?? "text-muted-foreground"
                        )}>
                          {log.outcome === "success" && <CheckCircle2 className="h-2.5 w-2.5" />}
                          {log.outcome === "failure" && <XCircle className="h-2.5 w-2.5" />}
                          {log.outcome === "blocked" && <AlertTriangle className="h-2.5 w-2.5" />}
                          {log.outcome}
                        </span>
                      </td>
                    </motion.tr>
                  ))}
                </tbody>
              </table>
            </motion.div>

            {/* Pagination */}
            {total > PAGE_SIZE && (
              <div className="flex items-center justify-center gap-3">
                <button
                  onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                  disabled={offset === 0}
                  className="rounded-lg px-3 py-1.5 text-xs text-muted-foreground hover:text-foreground disabled:opacity-40 transition-colors"
                >
                  ← Previous
                </button>
                <span className="text-xs text-muted-foreground/60">
                  {offset + 1}–{Math.min(offset + PAGE_SIZE, total)} of {total}
                </span>
                <button
                  onClick={() => setOffset(offset + PAGE_SIZE)}
                  disabled={offset + PAGE_SIZE >= total}
                  className="rounded-lg px-3 py-1.5 text-xs text-muted-foreground hover:text-foreground disabled:opacity-40 transition-colors"
                >
                  Next →
                </button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function SummaryCard({ label, value, icon, color }: { label: string; value: number; icon: React.ReactNode; color: string }) {
  return (
    <div className="rounded-xl border border-foreground/[0.06] bg-card/80 p-4 backdrop-blur-sm">
      <div className="flex items-center justify-between mb-2">
        <span className="text-[11px] font-medium text-muted-foreground/70">{label}</span>
        {icon}
      </div>
      <p className={cn("text-2xl font-bold tabular-nums", color)}>{value.toLocaleString()}</p>
    </div>
  );
}

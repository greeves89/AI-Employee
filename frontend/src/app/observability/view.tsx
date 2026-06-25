"use client";

import { useState, useEffect, useCallback } from "react";
import { motion } from "framer-motion";
import {
  Activity,
  DollarSign,
  Clock,
  RefreshCw,
  ExternalLink,
  Telescope,
  AlertTriangle,
} from "lucide-react";
import * as api from "@/lib/api";
import type { ObservabilityConfig, ObservabilityTrace } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Header } from "@/components/layout/header";

const PAGE_SIZE = 50;

export function ObservabilityView({ embedded = false }: { embedded?: boolean }) {
  const [config, setConfig] = useState<ObservabilityConfig | null>(null);
  const [traces, setTraces] = useState<ObservabilityTrace[]>([]);
  const [daily, setDaily] = useState<Array<{ date: string; countTraces?: number; totalCost?: number }>>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const cfg = await api.getObservabilityConfig();
      setConfig(cfg);
      if (!cfg.enabled) {
        setLoading(false);
        setRefreshing(false);
        return;
      }
      const [tracesRes, dailyRes] = await Promise.all([
        api.getObservabilityTraces({ page, limit: PAGE_SIZE }),
        api.getObservabilityDaily(14).catch(() => ({ data: [] })),
      ]);
      setTraces(tracesRes.data ?? []);
      setTotalPages(tracesRes.meta?.totalPages ?? 1);
      setDaily(dailyRes.data ?? []);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Laden fehlgeschlagen");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [page]);

  useEffect(() => { load(); }, [load]);

  const handleRefresh = () => { setRefreshing(true); load(); };

  const totalCost = daily.reduce((s, d) => s + (d.totalCost ?? 0), 0);
  const totalTraces = daily.reduce((s, d) => s + (d.countTraces ?? 0), 0);

  const body = (
    <div className={embedded ? "space-y-6" : "px-8 py-8 space-y-6"}>
      {/* Not configured → setup hint */}
      {config && !config.enabled ? (
        <div className="rounded-xl border border-amber-500/20 bg-amber-500/[0.04] p-6">
          <div className="flex items-center gap-2 mb-3">
            <AlertTriangle className="h-5 w-5 text-amber-400" />
            <h3 className="text-sm font-semibold">LLM-Observability ist nicht aktiviert</h3>
          </div>
          <p className="text-sm text-muted-foreground mb-4">
            Tracing läuft als No-Op, solange Langfuse nicht konfiguriert ist — die Plattform
            funktioniert unverändert. So aktivierst du es (einmalig):
          </p>
          <ol className="list-decimal list-inside space-y-1.5 text-sm text-muted-foreground">
            <li>Secrets in <code className="text-xs">.env</code> setzen
              (<code className="text-xs">LANGFUSE_PUBLIC_KEY</code>, <code className="text-xs">LANGFUSE_SECRET_KEY</code>,
              <code className="text-xs"> LANGFUSE_ENCRYPTION_KEY</code> …)</li>
            <li>Stack starten: <code className="text-xs">docker compose --profile observability up -d</code></li>
            <li>Orchestrator neu starten</li>
          </ol>
        </div>
      ) : error ? (
        <div className="rounded-xl border border-red-500/20 bg-red-500/[0.04] p-6 text-sm text-red-300">
          {error}
        </div>
      ) : (
        <>
          {/* Summary */}
          <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
            <SummaryCard label="Traces (14 T.)" value={totalTraces.toLocaleString()} icon={<Activity className="h-4 w-4 text-primary" />} color="text-primary" />
            <SummaryCard label="Kosten (14 T.)" value={`$${totalCost.toFixed(2)}`} icon={<DollarSign className="h-4 w-4 text-emerald-400" />} color="text-emerald-400" />
            <SummaryCard label="Seite" value={`${page} / ${totalPages}`} icon={<Clock className="h-4 w-4 text-violet-400" />} color="text-violet-400" />
          </div>

          {/* Traces table */}
          {loading ? (
            <div className="space-y-2">
              {[1, 2, 3, 4, 5].map((i) => (
                <div key={i} className="h-12 animate-pulse rounded-lg bg-foreground/[0.03] border border-foreground/[0.04]" />
              ))}
            </div>
          ) : traces.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-20 text-center">
              <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-foreground/[0.04] mb-4">
                <Telescope className="h-7 w-7 text-muted-foreground/50" />
              </div>
              <p className="text-sm text-muted-foreground">Noch keine Traces — sobald Tasks laufen, erscheinen sie hier.</p>
            </div>
          ) : (
            <motion.div className="rounded-xl border border-foreground/[0.06] overflow-hidden" initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-foreground/[0.06] bg-foreground/[0.02]">
                    <th className="px-4 py-3 text-left font-medium text-muted-foreground/70">Zeit</th>
                    <th className="px-4 py-3 text-left font-medium text-muted-foreground/70">Name</th>
                    <th className="px-4 py-3 text-left font-medium text-muted-foreground/70">User</th>
                    <th className="px-4 py-3 text-right font-medium text-muted-foreground/70">Kosten</th>
                    <th className="px-4 py-3 text-right font-medium text-muted-foreground/70">Latenz</th>
                    <th className="px-4 py-3 text-right font-medium text-muted-foreground/70"></th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-foreground/[0.04]">
                  {traces.map((t) => (
                    <tr key={t.id} className="hover:bg-foreground/[0.02] transition-colors">
                      <td className="px-4 py-2.5 text-muted-foreground/60 whitespace-nowrap font-mono">
                        {t.timestamp ? new Date(t.timestamp).toLocaleString("de-DE", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—"}
                      </td>
                      <td className="px-4 py-2.5 font-medium truncate max-w-[220px]">{t.name ?? t.id}</td>
                      <td className="px-4 py-2.5 text-muted-foreground/80 truncate max-w-[160px]">{t.userId ?? "—"}</td>
                      <td className="px-4 py-2.5 text-right tabular-nums">{t.totalCost != null ? `$${Number(t.totalCost).toFixed(4)}` : "—"}</td>
                      <td className="px-4 py-2.5 text-right tabular-nums text-muted-foreground/70">{t.latency != null ? `${Number(t.latency).toFixed(2)}s` : "—"}</td>
                      <td className="px-4 py-2.5 text-right">
                        {config?.public_url && (
                          <a
                            href={`${config.public_url.replace(/\/$/, "")}/project/${config.project_id}/traces/${t.id}`}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="inline-flex items-center gap-1 text-primary/80 hover:text-primary"
                          >
                            <ExternalLink className="h-3.5 w-3.5" />
                          </a>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </motion.div>
          )}

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="flex items-center justify-center gap-3">
              <button onClick={() => setPage(Math.max(1, page - 1))} disabled={page <= 1}
                className="rounded-lg px-3 py-1.5 text-xs text-muted-foreground hover:text-foreground disabled:opacity-40 transition-colors">← Zurück</button>
              <span className="text-xs text-muted-foreground/60">Seite {page} / {totalPages}</span>
              <button onClick={() => setPage(page + 1)} disabled={page >= totalPages}
                className="rounded-lg px-3 py-1.5 text-xs text-muted-foreground hover:text-foreground disabled:opacity-40 transition-colors">Weiter →</button>
            </div>
          )}

          {config?.public_url && (
            <div className="text-center">
              <a href={config.public_url} target="_blank" rel="noopener noreferrer"
                className="inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors">
                <ExternalLink className="h-3.5 w-3.5" /> Volle Langfuse-Oberfläche öffnen
              </a>
            </div>
          )}
        </>
      )}
    </div>
  );

  return (
    <div>
      {!embedded && (
        <Header
          title="LLM-Observability"
          subtitle="Traces, Token-Kosten und Qualität deiner LLM-/Agenten-Läufe (Langfuse)"
          actions={
            <button onClick={handleRefresh} className="inline-flex items-center gap-2 rounded-xl px-4 py-2.5 text-sm text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04] transition-all">
              <RefreshCw className={cn("h-4 w-4", refreshing && "animate-spin")} />
              Aktualisieren
            </button>
          }
        />
      )}
      {body}
    </div>
  );
}

function SummaryCard({ label, value, icon, color }: { label: string; value: string; icon: React.ReactNode; color: string }) {
  return (
    <div className="rounded-xl border border-foreground/[0.06] bg-card/80 p-4 backdrop-blur-sm">
      <div className="flex items-center justify-between mb-2">
        <span className="text-[11px] font-medium text-muted-foreground/70">{label}</span>
        {icon}
      </div>
      <p className={cn("text-2xl font-bold tabular-nums", color)}>{value}</p>
    </div>
  );
}

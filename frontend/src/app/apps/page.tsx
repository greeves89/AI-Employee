"use client";

import { useState, useEffect, useCallback } from "react";
import { AppWindow, Play, Square, Loader2, Cpu, RefreshCw, ScrollText, Trash2, X, Flag, CheckCircle2 } from "lucide-react";
import { Header } from "@/components/layout/header";
import { cn } from "@/lib/utils";
import * as api from "@/lib/api";

export default function AppsPage() {
  const [apps, setApps] = useState<api.AppEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<Set<string>>(new Set());   // per-app: multiple in parallel
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [reported, setReported] = useState<Record<string, string>>({});  // project -> agent name
  const [reporting, setReporting] = useState<Set<string>>(new Set());
  const [logsFor, setLogsFor] = useState<api.AppEntry | null>(null);

  const report = async (app: api.AppEntry) => {
    const err = errors[app.project];
    if (!err) return;
    setReporting((s) => new Set(s).add(app.project));
    try {
      const r = await api.reportApp(app.project, err, app.path);
      setReported((m) => ({ ...m, [app.project]: r.agent_name }));
    } catch {
      /* ignore */
    } finally {
      setReporting((s) => { const n = new Set(s); n.delete(app.project); return n; });
    }
  };

  const load = useCallback(async () => {
    try {
      const r = await api.listApps();
      setApps(r.apps);
    } catch {
      /* ignore */
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const iv = setInterval(load, 5000);
    return () => clearInterval(iv);
  }, [load]);

  // Per-app action: each spins its OWN card until it finishes — independent of other
  // clicks. Errors land on the card (not as uncaught console rejections).
  const act = async (key: string, fn: () => Promise<unknown>) => {
    setBusy((s) => new Set(s).add(key));
    setErrors((e) => { const n = { ...e }; delete n[key]; return n; });
    try {
      await fn();
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Fehlgeschlagen.";
      // Strip the "API Error 500: {json}" envelope → short readable hint.
      const short = msg.replace(/^API Error \d+:\s*/, "").slice(0, 240);
      setErrors((e) => ({ ...e, [key]: short }));
    } finally {
      setBusy((s) => { const n = new Set(s); n.delete(key); return n; });
      load();
    }
  };

  const start = (app: api.AppEntry) =>
    act(app.project, () =>
      app.path && app.containers.length === 0
        ? api.startDockerApp(app.agent_id, app.path)   // never-started workspace app → compose up
        : api.startAppByProject(app.project),          // stopped container → start
    );

  return (
    <div className="flex flex-col h-[calc(100vh-2rem)]">
      <Header title="Apps" subtitle="Alle Apps deiner Agenten — laufend, gestoppt und noch nicht gestartet" />

      <div className="flex-1 overflow-y-auto p-6">
        <div className="flex items-center justify-between mb-4">
          <p className="text-sm text-muted-foreground">{apps.length} App{apps.length === 1 ? "" : "s"}</p>
          <button
            onClick={load}
            className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm text-muted-foreground hover:text-foreground hover:bg-accent/50 transition-colors"
          >
            <RefreshCw className="h-4 w-4" /> Aktualisieren
          </button>
        </div>

        {loading && apps.length === 0 ? (
          <div className="flex items-center justify-center py-16">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        ) : apps.length === 0 ? (
          <div className="rounded-xl border border-border bg-card/60 p-10 text-center">
            <AppWindow className="h-8 w-8 mx-auto text-muted-foreground/50 mb-3" />
            <p className="text-sm text-muted-foreground">
              Noch keine Apps. Sie erscheinen hier, sobald einer deiner Agenten ein docker-compose-Projekt hat
              (Taskforce-Ergebnis oder Agenten-Workspace).
            </p>
          </div>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {apps.map((app) => {
              const running = app.status === "running";
              const notStarted = app.status === "not_started";
              const isBusy = busy.has(app.project);
              const err = errors[app.project];
              return (
                <div key={app.project} className="rounded-xl border border-border bg-card/80 p-4 flex flex-col gap-3">
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex items-center gap-2 min-w-0">
                      <div className={cn(
                        "flex h-8 w-8 shrink-0 items-center justify-center rounded-lg",
                        running ? "bg-emerald-500/10" : "bg-foreground/[0.05]",
                      )}>
                        <AppWindow className={cn("h-4 w-4", running ? "text-emerald-400" : "text-muted-foreground")} />
                      </div>
                      <div className="min-w-0">
                        <p className="text-sm font-medium truncate">{app.name}</p>
                        <p className="flex items-center gap-1 text-[11px] text-muted-foreground truncate">
                          <Cpu className="h-3 w-3" /> {app.agent_name}
                        </p>
                      </div>
                    </div>
                    <span className={cn(
                      "flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full border font-medium shrink-0",
                      isBusy ? "bg-primary/10 text-primary border-primary/20"
                        : running ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/20"
                        : notStarted ? "bg-blue-500/10 text-blue-400 border-blue-500/20"
                        : "bg-amber-500/10 text-amber-400 border-amber-500/20",
                    )}>
                      {isBusy && <Loader2 className="h-2.5 w-2.5 animate-spin" />}
                      {isBusy ? "startet…" : running ? "läuft" : notStarted ? "nicht gestartet" : "gestoppt"}
                    </span>
                  </div>

                  <p className="text-[11px] text-muted-foreground/70 truncate">
                    {app.containers.length > 0
                      ? `${app.containers.length} Container · ${app.containers.map((c) => c.service || c.name).slice(0, 3).join(", ")}`
                      : app.path ? `Workspace: ${app.path}` : app.project}
                  </p>

                  <div className="flex flex-wrap items-center gap-2 mt-auto">
                    {running && app.url && (
                      <a href={app.url} target="_blank" rel="noopener noreferrer"
                        className="flex items-center gap-1.5 rounded-lg bg-emerald-500/15 px-3 py-1.5 text-sm font-medium text-emerald-400 hover:bg-emerald-500/25 transition-colors">
                        <Play className="h-4 w-4" /> Öffnen
                      </a>
                    )}
                    {!running && (
                      <button onClick={() => start(app)} disabled={isBusy}
                        className="flex items-center gap-1.5 rounded-lg bg-emerald-500/15 px-3 py-1.5 text-sm font-medium text-emerald-400 hover:bg-emerald-500/25 disabled:opacity-60 transition-colors">
                        {isBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
                        {isBusy ? "startet…" : "Starten"}
                      </button>
                    )}
                    {running && (
                      <button onClick={() => act(app.project, () => api.stopApp(app.project))} disabled={isBusy}
                        className="flex items-center gap-1.5 rounded-lg bg-amber-500/10 px-3 py-1.5 text-sm font-medium text-amber-400 hover:bg-amber-500/20 disabled:opacity-50 transition-colors">
                        {isBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Square className="h-4 w-4" />} Stoppen
                      </button>
                    )}
                    {app.containers.length > 0 && (
                      <button onClick={() => setLogsFor(app)}
                        className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm text-muted-foreground hover:text-foreground hover:bg-accent/50 transition-colors">
                        <ScrollText className="h-4 w-4" /> Logs
                      </button>
                    )}
                    {!running && app.containers.length > 0 && (
                      <button onClick={() => act(app.project, () => api.removeApp(app.project))} disabled={isBusy}
                        title="Container endgültig entfernen"
                        className="flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-sm text-red-400/80 hover:text-red-400 hover:bg-red-500/10 transition-colors">
                        <Trash2 className="h-4 w-4" />
                      </button>
                    )}
                  </div>
                  {err && (
                    <div className="space-y-1.5">
                      <p className="text-[11px] text-red-400/90 bg-red-500/[0.06] rounded-lg px-2.5 py-1.5 break-words">
                        {err}{app.containers.length > 0 ? " — Details unter Logs." : ""}
                      </p>
                      {reported[app.project] ? (
                        <p className="flex items-center gap-1.5 text-[11px] text-emerald-400">
                          <CheckCircle2 className="h-3.5 w-3.5" />
                          An {reported[app.project]} gemeldet — der Agent kümmert sich darum.
                        </p>
                      ) : (
                        <button
                          onClick={() => report(app)}
                          disabled={reporting.has(app.project)}
                          className="flex items-center gap-1.5 rounded-lg bg-amber-500/10 px-3 py-1.5 text-xs font-medium text-amber-400 hover:bg-amber-500/20 disabled:opacity-50 transition-colors"
                        >
                          {reporting.has(app.project) ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Flag className="h-3.5 w-3.5" />}
                          An Agent melden (soll beheben)
                        </button>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {logsFor && <LogsModal app={logsFor} onClose={() => setLogsFor(null)} />}
    </div>
  );
}

function LogsModal({ app, onClose }: { app: api.AppEntry; onClose: () => void }) {
  const [data, setData] = useState<api.AppLogContainer[] | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    api.getAppLogs(app.project, 300)
      .then((r) => setData(r.containers))
      .catch(() => setData([]))
      .finally(() => setLoading(false));
  }, [app.project]);

  useEffect(() => {
    load();
    const iv = setInterval(load, 4000);
    return () => clearInterval(iv);
  }, [load]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4" onClick={onClose}>
      <div
        className="w-full max-w-3xl max-h-[85vh] flex flex-col rounded-2xl border border-border bg-card shadow-xl overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-3 border-b border-border shrink-0">
          <div className="flex items-center gap-2 min-w-0">
            <ScrollText className="h-4 w-4 text-primary shrink-0" />
            <span className="font-medium truncate">Logs — {app.name}</span>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={load} className="text-muted-foreground hover:text-foreground"><RefreshCw className="h-4 w-4" /></button>
            <button onClick={onClose} className="text-muted-foreground hover:text-foreground"><X className="h-4 w-4" /></button>
          </div>
        </div>
        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          {loading && !data ? (
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          ) : !data || data.length === 0 ? (
            <p className="text-sm text-muted-foreground">Keine Logs verfügbar.</p>
          ) : (
            data.map((c) => (
              <div key={c.name}>
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-xs font-medium">{c.service || c.name}</span>
                  <span className={cn(
                    "text-[10px] px-1.5 py-0.5 rounded-full border",
                    c.status === "running" ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/20"
                      : "bg-amber-500/10 text-amber-400 border-amber-500/20",
                  )}>{c.status}</span>
                </div>
                <pre className="text-[11px] leading-relaxed whitespace-pre-wrap break-words font-mono text-foreground/75 bg-foreground/[0.03] rounded-lg p-3 max-h-72 overflow-auto">
                  {c.logs || "(leer)"}
                </pre>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}

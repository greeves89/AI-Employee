"use client";

import { useState, useEffect, useCallback } from "react";
import { AppWindow, Play, Square, Loader2, Cpu, RefreshCw } from "lucide-react";
import { Header } from "@/components/layout/header";
import { cn } from "@/lib/utils";
import * as api from "@/lib/api";

export default function AppsPage() {
  const [apps, setApps] = useState<api.AppEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [stopping, setStopping] = useState<string | null>(null);

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

  const stop = async (project: string) => {
    setStopping(project);
    try {
      await api.stopApp(project);
      await load();
    } finally {
      setStopping(null);
    }
  };

  return (
    <div className="flex flex-col h-[calc(100vh-2rem)]">
      <Header title="Apps" subtitle="Deine laufenden Docker-Apps — nur die deiner eigenen Agenten" />

      <div className="flex-1 overflow-y-auto p-6">
        <div className="flex items-center justify-between mb-4">
          <p className="text-sm text-muted-foreground">
            {apps.length} App{apps.length === 1 ? "" : "s"}
          </p>
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
              Noch keine Apps. Starte eine aus einem Taskforce-Meeting-Ergebnis oder aus einem Agenten-Workspace.
            </p>
          </div>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {apps.map((app) => {
              const running = app.status === "running";
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
                        <p className="text-sm font-medium truncate">{app.project}</p>
                        <p className="flex items-center gap-1 text-[11px] text-muted-foreground truncate">
                          <Cpu className="h-3 w-3" /> {app.agent_name}
                        </p>
                      </div>
                    </div>
                    <span className={cn(
                      "text-[10px] px-2 py-0.5 rounded-full border font-medium shrink-0",
                      running ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/20"
                              : "bg-foreground/[0.04] text-muted-foreground border-border",
                    )}>
                      {running ? "läuft" : "gestoppt"}
                    </span>
                  </div>

                  <p className="text-[11px] text-muted-foreground/70">
                    {app.containers.length} Container{app.containers.length === 1 ? "" : ""}
                    {app.containers.length > 0 && ` · ${app.containers.map((c) => c.service || c.name).slice(0, 3).join(", ")}`}
                  </p>

                  <div className="flex items-center gap-2 mt-auto">
                    {running && app.url ? (
                      <a
                        href={app.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="flex items-center gap-1.5 rounded-lg bg-emerald-500/15 px-3 py-1.5 text-sm font-medium text-emerald-400 hover:bg-emerald-500/25 transition-colors"
                      >
                        <Play className="h-4 w-4" /> Öffnen
                      </a>
                    ) : running ? (
                      <span className="text-xs text-muted-foreground">läuft (kein Web-Port)</span>
                    ) : null}
                    <button
                      onClick={() => stop(app.project)}
                      disabled={stopping === app.project}
                      className="flex items-center gap-1.5 rounded-lg bg-red-500/10 px-3 py-1.5 text-sm font-medium text-red-400 hover:bg-red-500/20 disabled:opacity-50 transition-colors"
                    >
                      {stopping === app.project ? <Loader2 className="h-4 w-4 animate-spin" /> : <Square className="h-4 w-4" />}
                      Stoppen
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

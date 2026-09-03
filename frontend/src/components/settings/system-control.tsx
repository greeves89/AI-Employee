"use client";

import { useState, useEffect, useCallback } from "react";
import { RotateCw, Loader2, RefreshCw, Server, Power, AlertTriangle } from "lucide-react";
import * as api from "@/lib/api";
import { cn } from "@/lib/utils";
import { useConfirm } from "@/components/ui/dialog-provider";

// Remote system control: container states + restart of orchestrator/frontend
// from the already-exposed web UI (over the Cloudflare tunnel), so the platform
// can be restarted from outside the home LAN without SSH.
const STATE_STYLES: Record<string, string> = {
  running: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
  restarting: "bg-amber-500/10 text-amber-700 dark:text-amber-400 border-amber-500/20",
  exited: "bg-red-500/10 text-red-400 border-red-500/20",
  absent: "bg-zinc-500/10 text-zinc-400 border-zinc-500/20",
};

export function SystemControl() {
  const confirm = useConfirm();
  const [status, setStatus] = useState<api.SystemStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setStatus(await api.getSystemStatus());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Status nicht abrufbar");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const restart = async (target: "orchestrator" | "frontend") => {
    const ok = await confirm({
      title: `${target === "orchestrator" ? "Orchestrator" : "Frontend"} neustarten?`,
      message:
        target === "orchestrator"
          ? "Der Orchestrator startet neu — kurze Nichterreichbarkeit (~10-20s). Danach Seite neu laden."
          : "Das Frontend startet neu — die Oberfläche ist kurz nicht erreichbar.",
      variant: "destructive",
      confirmLabel: "Neustarten",
    });
    if (!ok) return;
    setBusy(target);
    setError(null);
    setNote(null);
    try {
      const res = await api.restartSystemComponent(target);
      setNote(res.note || `${target} neu gestartet.`);
      // Give the container a moment, then refresh status.
      setTimeout(load, 4000);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Neustart fehlgeschlagen");
    } finally {
      setBusy(null);
    }
  };

  return (
    <section>
      <div className="flex items-center gap-2 mb-3">
        <Server className="h-4 w-4 text-muted-foreground/60" />
        <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground/60">
          System-Steuerung
        </h2>
      </div>

      <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm overflow-hidden">
        <div className="flex items-center justify-between gap-3 px-5 py-3.5 border-b border-foreground/[0.04]">
          <div className="min-w-0">
            <h3 className="text-sm font-semibold">Dienste</h3>
            <p className="text-[11px] text-muted-foreground/60">
              Fernwartung: Status & Neustart von außerhalb des Heimnetzes {status?.version ? `· v${status.version}` : ""}
            </p>
          </div>
          <button
            onClick={load}
            className="inline-flex items-center gap-1.5 rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-2.5 py-1.5 text-[11px] font-medium text-muted-foreground/80 hover:bg-foreground/[0.05] transition-all shrink-0"
          >
            <RefreshCw className="h-3.5 w-3.5" />
            Aktualisieren
          </button>
        </div>

        {note && (
          <div className="px-5 py-2.5 bg-primary/[0.04] border-b border-foreground/[0.04]">
            <p className="text-[11px] text-muted-foreground/80">{note}</p>
          </div>
        )}
        {error && (
          <div className="px-5 py-2.5 bg-red-500/[0.06] border-b border-red-500/10">
            <p className="text-[11px] text-red-400">{error}</p>
          </div>
        )}

        {loading ? (
          <div className="flex items-center justify-center py-10">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground/40" />
          </div>
        ) : (
          <div className="divide-y divide-foreground/[0.04]">
            {status &&
              Object.entries(status.containers).map(([label, state]) => {
                const restartable = label === "orchestrator" || label === "frontend";
                return (
                  <div key={label} className="flex items-center justify-between gap-3 px-5 py-3">
                    <div className="flex items-center gap-2.5 min-w-0">
                      <span className="text-sm font-medium capitalize">{label}</span>
                      <span
                        className={cn(
                          "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[10px] font-medium",
                          STATE_STYLES[state] || STATE_STYLES.absent,
                        )}
                      >
                        <span className="h-1.5 w-1.5 rounded-full bg-current" />
                        {state}
                      </span>
                    </div>
                    {restartable && (
                      <button
                        onClick={() => restart(label as "orchestrator" | "frontend")}
                        disabled={busy !== null}
                        className="inline-flex items-center gap-1.5 rounded-lg border border-amber-500/30 bg-amber-500/10 px-2.5 py-1.5 text-[11px] font-medium text-amber-700 dark:text-amber-400 hover:bg-amber-500/15 disabled:opacity-50 transition-all shrink-0"
                      >
                        {busy === label ? (
                          <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        ) : (
                          <RotateCw className="h-3.5 w-3.5" />
                        )}
                        Neustart
                      </button>
                    )}
                  </div>
                );
              })}
            {status?.agent_containers != null && (
              <div className="flex items-center gap-2 px-5 py-3">
                <Power className="h-3.5 w-3.5 text-muted-foreground/40" />
                <span className="text-[12px] text-muted-foreground/70">
                  {status.agent_containers} Agent-Container laufen
                </span>
              </div>
            )}
          </div>
        )}

        <div className="flex items-start gap-2 px-5 py-3 border-t border-foreground/[0.04] bg-foreground/[0.01]">
          <AlertTriangle className="h-3.5 w-3.5 text-amber-700 dark:text-amber-400/60 mt-0.5 shrink-0" />
          <p className="text-[10px] text-muted-foreground/50">
            Nur Orchestrator und Frontend sind neustartbar — Datenbank, Redis und Proxy sind bewusst ausgenommen,
            damit ein Fehlklick nichts Kritisches trifft.
          </p>
        </div>
      </div>
    </section>
  );
}

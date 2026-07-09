"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Loader2, Moon, Play, ShieldCheck } from "lucide-react";
import { getReflectionStatus, runReflectionNow } from "@/lib/api";
import type { ReflectionStatus } from "@/lib/types";
import { useAuthStore } from "@/lib/auth";
import { cn } from "@/lib/utils";

const STATUS_CONFIG: Record<string, { label: string; className: string }> = {
  running: {
    label: "Läuft",
    className: "bg-blue-500/10 text-blue-400 border-blue-500/20",
  },
  completed: {
    label: "Erfolgreich",
    className: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
  },
  budget_exceeded: {
    label: "Budget erschöpft",
    className: "bg-amber-500/10 text-amber-400 border-amber-500/20",
  },
  failed: {
    label: "Fehlgeschlagen",
    className: "bg-red-500/10 text-red-400 border-red-500/20",
  },
};

export function ReflectionCard() {
  const [status, setStatus] = useState<ReflectionStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [triggering, setTriggering] = useState(false);
  const [message, setMessage] = useState("");
  const user = useAuthStore((s) => s.user);
  const isAdmin = user?.role === "admin";

  const load = useCallback(async () => {
    try {
      setStatus(await getReflectionStatus());
    } catch {
      // ignore — card stays in last known state
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    load();
    const interval = setInterval(load, 30000);
    return () => clearInterval(interval);
  }, [load]);

  const handleRunNow = async () => {
    setTriggering(true);
    setMessage("");
    try {
      await runReflectionNow();
      setMessage("Lauf gestartet.");
      await load();
    } catch (e) {
      const msg = e instanceof Error ? e.message : "";
      setMessage(msg.includes("409") ? "Ein Lauf ist bereits aktiv." : "Start fehlgeschlagen.");
    } finally {
      setTriggering(false);
    }
  };

  if (loading) {
    return (
      <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-5 h-[180px] animate-shimmer bg-[length:200%_100%] bg-gradient-to-r from-foreground/[0.03] via-foreground/[0.06] to-foreground/[0.03]" />
    );
  }

  const lastRun = status?.last_run ?? null;
  const stats = lastRun?.stats ?? {};
  const isRunning = lastRun?.status === "running";
  const statusCfg = lastRun ? STATUS_CONFIG[lastRun.status] ?? STATUS_CONFIG.failed : null;
  const pending = status?.pending_approvals ?? 0;

  return (
    <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-5">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-violet-500/10">
            <Moon className="h-4 w-4 text-violet-400" />
          </div>
          <h3 className="text-sm font-semibold tracking-tight">Nachtschicht</h3>
        </div>
        {statusCfg && (
          <span
            className={cn(
              "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[10px] font-medium",
              statusCfg.className,
            )}
          >
            {isRunning && <Loader2 className="h-2.5 w-2.5 animate-spin" />}
            {statusCfg.label}
          </span>
        )}
      </div>

      {!status?.enabled ? (
        <div className="text-center py-4">
          <p className="text-sm text-muted-foreground/60">Nachtschicht ist deaktiviert</p>
          <Link
            href="/settings"
            className="mt-2 inline-flex text-[12px] text-primary hover:text-primary/80 transition-colors"
          >
            In den Einstellungen aktivieren
          </Link>
        </div>
      ) : (
        <>
          {lastRun ? (
            <>
              <p className="text-[11px] text-muted-foreground/60 mb-2">
                Letzter Lauf:{" "}
                {lastRun.started_at
                  ? new Date(lastRun.started_at).toLocaleString("de-DE", {
                      dateStyle: "medium",
                      timeStyle: "short",
                    })
                  : "unbekannt"}
              </p>
              <p className="text-xs text-muted-foreground">
                {stats.facts_new ?? 0} Notizen neu · {stats.facts_superseded ?? 0} aktualisiert ·{" "}
                {pending} Freigaben offen · {stats.skills_drafted ?? 0} Skill-Entwürfe
              </p>
              {(stats.errors?.length ?? 0) > 0 && (
                <p className="text-xs text-amber-500/90 mt-1.5">
                  {stats.errors!.length} von {stats.transcripts_read ?? 0} Auswertungen fehlgeschlagen
                  {" — "}
                  {stats.errors!.some((e) => e.includes("extraction failed"))
                    ? "kein LLM-Zugang? Anthropic-Key oder Bedrock-Account pruefen."
                    : "Details im Audit-Log."}
                </p>
              )}
            </>
          ) : (
            <p className="text-xs text-muted-foreground/60">
              Noch kein Lauf — die Nachtschicht startet um {status.hour}:00 Uhr.
            </p>
          )}

          <div className="mt-4 pt-3 border-t border-foreground/[0.06] flex items-center gap-2 flex-wrap">
            {pending > 0 && (
              <Link
                href="/approvals"
                className="inline-flex items-center gap-1.5 rounded-lg bg-violet-500/10 border border-violet-500/20 px-3 py-1.5 text-xs font-medium text-violet-400 hover:bg-violet-500/20 transition-colors"
              >
                <ShieldCheck className="h-3.5 w-3.5" />
                Freigaben ansehen ({pending})
              </Link>
            )}
            {isAdmin && (
              <button
                onClick={handleRunNow}
                disabled={triggering || isRunning}
                className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04] disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              >
                {triggering || isRunning ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Play className="h-3.5 w-3.5" />
                )}
                Jetzt laufen lassen
              </button>
            )}
            {message && (
              <span className="text-[11px] text-muted-foreground/60">{message}</span>
            )}
          </div>
        </>
      )}
    </div>
  );
}

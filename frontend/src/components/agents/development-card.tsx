"use client";

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, Loader2, TrendingDown, TrendingUp, Minus } from "lucide-react";
import { cn } from "@/lib/utils";
import { getAgentDevelopment, type AgentDevelopment } from "@/lib/api";

/**
 * Wird dieser Agent besser? Und war die Probezeit erfolgreich?
 *
 * Sichtbar waren bisher Kosten und Laufzahl — beides sagt nichts darüber, ob die
 * Arbeit taugt. Ein Agent kann 500 Läufe und 50 Dollar verbrauchen und nichts
 * zustande gebracht haben; genau das ist beim Kunden passiert. Die Zahlen hier
 * kommen aus vorhandenen Daten (Bewertungen, Fehlerquote, Plan-Treue), es wird
 * nichts zusätzlich erhoben.
 */
const TREND_STYLE: Record<string, { icon: typeof TrendingUp; className: string }> = {
  besser: { icon: TrendingUp, className: "text-emerald-500 dark:text-emerald-400" },
  schlechter: { icon: TrendingDown, className: "text-red-500 dark:text-red-400" },
  stabil: { icon: Minus, className: "text-muted-foreground" },
};

function Kennzahl({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-lg border border-foreground/[0.06] bg-background/60 p-2.5">
      <div className="text-[10px] uppercase tracking-wide text-muted-foreground/40">{label}</div>
      <div className="mt-0.5 text-sm font-medium text-foreground">{value}</div>
      {hint && <div className="mt-0.5 text-[10px] text-muted-foreground/50">{hint}</div>}
    </div>
  );
}

export function DevelopmentCard({ agentId }: { agentId: string }) {
  const [data, setData] = useState<AgentDevelopment | null>(null);
  const [days, setDays] = useState(30);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setData(await getAgentDevelopment(agentId, days));
    } catch {
      setData(null);   // keine Zahlen ist ein gueltiger Zustand, kein Fehlerbild
    } finally {
      setLoading(false);
    }
  }, [agentId, days]);

  useEffect(() => { load(); }, [load]);

  const trend = TREND_STYLE[data?.trend ?? ""] ?? {
    icon: Minus,
    className: "text-muted-foreground/50",
  };
  const TrendIcon = trend.icon;

  return (
    <div className="rounded-xl border border-foreground/[0.06] bg-card/80 p-5 backdrop-blur-sm">
      <div className="mb-3 flex items-start justify-between gap-3">
        <div>
          <div className="text-sm font-medium">Entwicklung &amp; Probezeit</div>
          <div className="text-[11px] text-muted-foreground/60">
            Wird er besser? Aus Bewertungen, Fehlerquote und Plan-Treue — nichts davon
            wird zusätzlich erhoben.
          </div>
        </div>
        <select
          value={days}
          onChange={(e) => setDays(parseInt(e.target.value, 10))}
          className="rounded-lg border border-foreground/[0.08] bg-background/60 px-2 py-1 text-[11px]"
        >
          <option value={7}>7 Tage</option>
          <option value={30}>30 Tage</option>
          <option value={90}>90 Tage</option>
        </select>
      </div>

      {loading ? (
        <div className="flex items-center gap-2 py-4 text-[11px] text-muted-foreground/50">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
          Zahlen werden geholt
        </div>
      ) : !data ? (
        <div className="py-4 text-[11px] text-muted-foreground/50">
          Für diesen Agenten liegen keine Zahlen vor.
        </div>
      ) : (
        <>
          <div className={cn("mb-3 flex items-center gap-1.5 text-sm font-medium", trend.className)}>
            <TrendIcon className="h-4 w-4" />
            {data.trend === "zu wenig Daten" ? "Noch zu wenig Daten für ein Urteil" : `Tendenz: ${data.trend}`}
          </div>

          <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
            <Kennzahl
              label="Fehlerquote"
              value={`${data.tasks.failure_rate} %`}
              hint={`zuletzt ${data.failure_rate_recent} % · davor ${data.failure_rate_older} %`}
            />
            <Kennzahl
              label="Nacharbeit"
              value={`${data.rework.rate} %`}
              hint={`zuletzt ${data.rework.rate_recent} % · davor ${data.rework.rate_older} % · ${data.rework.resumed} fortgesetzt, ${data.rework.poorly_rated} zurückgegeben`}
            />
            <Kennzahl
              label="Plan-Treue"
              value={`${data.plan_adherence.rate} %`}
              hint={`${data.plan_adherence.done} von ${data.plan_adherence.planned} Blöcken erledigt`}
            />
            <Kennzahl
              label="Bewertungen"
              value={data.ratings.avg_recent !== null ? `${data.ratings.avg_recent} ★` : "–"}
              hint={
                data.ratings.avg_older !== null
                  ? `davor ${data.ratings.avg_older} ★ · ${data.ratings.count} Stück`
                  : `${data.ratings.count} Stück`
              }
            />
            <Kennzahl
              label="Im Dienst seit"
              value={`${data.probation.days_active} Tagen`}
              hint={`${data.tasks.total} Aufgaben in ${data.days} Tagen`}
            />
          </div>

          {(!data.probation.onboarded || !data.probation.has_responsibilities) && (
            <div className="mt-3 flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/[0.07] p-2.5 text-[11px] text-amber-700 dark:text-amber-300">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span>
                {!data.probation.onboarded
                  ? "Er weiß noch nicht, wofür er da ist — ohne Einrichtung kann kein Lauf etwas zustande bringen."
                  : "Ihm fehlen die Verantwortungsbereiche — er hat also keine wiederkehrenden Aufgaben, aus denen er sich den Tag bauen könnte."}
              </span>
            </div>
          )}

          {data.probation.review_due && data.probation.onboarded && data.probation.has_responsibilities && (
            <div className="mt-3 text-[11px] text-muted-foreground/60">
              Probezeit-Bilanz möglich: er ist seit {data.probation.days_active} Tagen im Dienst.
              Taugt die Arbeit nicht, sind die Verantwortungsbereiche der erste Hebel — nicht das Modell.
            </div>
          )}
        </>
      )}
    </div>
  );
}

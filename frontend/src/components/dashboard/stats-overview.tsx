"use client";

import { useEffect, useState } from "react";
import { Cpu, Zap, CheckCircle2, Clock, DollarSign } from "lucide-react";
import type { Agent, Task } from "@/lib/types";
import { cn } from "@/lib/utils";
import { formatMoney, moneyTitle } from "@/lib/money";
import { getAnalyticsOverview } from "@/lib/api";

/** Zeitraum der Kennzahlen „Erledigt" und „Kosten" — vom Server gerechnet. */
const KENNZAHLEN_TAGE = 30;

interface StatsOverviewProps {
  agents: Agent[];
  tasks: Task[];
}

const statConfig = [
  {
    key: "active",
    label: "Aktive Agenten",
    icon: Cpu,
    color: "text-emerald-400",
    gradient: "from-emerald-500/20 via-emerald-500/5 to-transparent",
    iconBg: "bg-emerald-500/10",
  },
  {
    key: "working",
    label: "Arbeiten gerade",
    icon: Zap,
    color: "text-blue-400",
    gradient: "from-blue-500/20 via-blue-500/5 to-transparent",
    iconBg: "bg-blue-500/10",
  },
  {
    key: "running",
    label: "In Warteschlange",
    icon: Clock,
    color: "text-amber-700 dark:text-amber-400",
    gradient: "from-amber-500/20 via-amber-500/5 to-transparent",
    iconBg: "bg-amber-500/10",
  },
  {
    key: "completed",
    label: `Erledigt (${KENNZAHLEN_TAGE} Tage)`,
    icon: CheckCircle2,
    color: "text-emerald-400",
    gradient: "from-emerald-500/20 via-emerald-500/5 to-transparent",
    iconBg: "bg-emerald-500/10",
  },
  {
    key: "cost",
    label: `Kosten (${KENNZAHLEN_TAGE} Tage)`,
    icon: DollarSign,
    color: "text-violet-400",
    gradient: "from-violet-500/20 via-violet-500/5 to-transparent",
    iconBg: "bg-violet-500/10",
  },
];

/** Erledigt + Kosten kommen vom Server (#896): fertige Aufgaben UND Antworten im
 *  Chat, Kosten aus Aufgaben UND Chat, nur die eigenen Agenten. Vorher zählte der
 *  Browser die geladenen Aufgaben — nach einem Tag im Chat stand „Erledigt 0". */
function useKennzahlen() {
  const [werte, setWerte] = useState<{ erledigt: number; kosten: number } | null>(null);
  useEffect(() => {
    let aktiv = true;
    getAnalyticsOverview(KENNZAHLEN_TAGE)
      .then((o) => {
        if (aktiv) {
          setWerte({
            erledigt: o.done_total ?? o.completed_tasks + (o.chat_replies ?? 0),
            kosten: o.total_cost_usd,
          });
        }
      })
      .catch(() => {});
    return () => { aktiv = false; };
  }, []);
  return werte;
}

export function StatsOverview({ agents, tasks }: StatsOverviewProps) {
  const kennzahlen = useKennzahlen();
  const activeAgents = agents.filter(
    (a) => a.state === "running" || a.state === "idle" || a.state === "working"
  ).length;
  const workingAgents = agents.filter((a) => a.state === "working").length;
  const runningTasks = tasks.filter(
    (t) => t.status === "running" || t.status === "queued"
  ).length;

  const values: Record<string, string | number> = {
    active: activeAgents,
    working: workingAgents,
    running: runningTasks,
    completed: kennzahlen ? kennzahlen.erledigt : "–",
    cost: kennzahlen ? formatMoney(kennzahlen.kosten) : "–",
  };
  const titles: Record<string, string | undefined> = {
    cost: kennzahlen ? moneyTitle(kennzahlen.kosten) : undefined,
    completed: "Fertige Aufgaben und Antworten im Chat",
  };

  return (
    <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
      {statConfig.map((stat) => {
        const Icon = stat.icon;
        return (
          <div
            key={stat.key}
            className="relative overflow-hidden rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-4 transition-all duration-200 hover:border-foreground/[0.1]"
          >
            {/* Subtle gradient glow */}
            <div className={cn(
              "absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent to-transparent",
              stat.gradient
            )} />

            <div className="flex items-center justify-between mb-2">
              <div className={cn("flex h-8 w-8 items-center justify-center rounded-lg", stat.iconBg)}>
                <Icon className={cn("h-4 w-4", stat.color)} />
              </div>
            </div>

            <p className="text-[11px] font-medium text-muted-foreground mb-0.5">
              {stat.label}
            </p>
            <p
              title={titles[stat.key]}
              className={cn("text-2xl font-bold tabular-nums leading-none tracking-tight", stat.color)}
            >
              {values[stat.key]}
            </p>
          </div>
        );
      })}
    </div>
  );
}

"use client";

import { useEffect, useState } from "react";
import { DollarSign, TrendingUp, Coins } from "lucide-react";
import { getCostAttribution } from "@/lib/api";
import type { CostAttribution as CostAttributionData } from "@/lib/api";
import { cn } from "@/lib/utils";

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

export function CostAttribution() {
  const [data, setData] = useState<CostAttributionData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getCostAttribution(5)
      .then(setData)
      .catch(() => {})
      .finally(() => setLoading(false));

    const interval = setInterval(() => {
      getCostAttribution(5).then(setData).catch(() => {});
    }, 30000);
    return () => clearInterval(interval);
  }, []);

  if (loading) {
    return (
      <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-5 h-[280px] animate-shimmer bg-[length:200%_100%] bg-gradient-to-r from-foreground/[0.03] via-foreground/[0.06] to-foreground/[0.03]" />
    );
  }

  if (!data || data.top_agents.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-foreground/[0.1] bg-card/30 p-8 text-center">
        <div className="flex justify-center mb-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-violet-500/10">
            <DollarSign className="h-5 w-5 text-violet-400" />
          </div>
        </div>
        <p className="text-sm text-muted-foreground">No cost data available yet</p>
      </div>
    );
  }

  const maxCost = Math.max(...data.top_agents.map((a) => a.total_cost_usd));

  return (
    <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-5">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-violet-500/10">
            <TrendingUp className="h-4 w-4 text-violet-400" />
          </div>
          <h3 className="text-sm font-semibold tracking-tight">Cost Attribution</h3>
        </div>
        <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <Coins className="h-3.5 w-3.5" />
          <span>Top 5 Agents</span>
        </div>
      </div>

      <div className="space-y-3">
        {data.top_agents.map((agent, i) => {
          const pct = maxCost > 0 ? (agent.total_cost_usd / maxCost) * 100 : 0;
          const colors = [
            "bg-violet-500/70",
            "bg-blue-500/70",
            "bg-emerald-500/70",
            "bg-amber-500/70",
            "bg-rose-500/70",
          ];
          return (
            <div key={agent.agent_id}>
              <div className="flex items-center justify-between mb-1">
                <span className="text-xs font-medium truncate max-w-[160px]">
                  {agent.agent_name}
                </span>
                <div className="flex items-center gap-3 text-xs tabular-nums">
                  <span className="text-muted-foreground">
                    {formatTokens(agent.total_input_tokens + agent.total_output_tokens)} tok
                  </span>
                  <span className={cn("font-semibold", i === 0 ? "text-violet-400" : "text-foreground/80")}>
                    ${agent.total_cost_usd.toFixed(2)}
                  </span>
                </div>
              </div>
              <div className="h-1.5 rounded-full bg-foreground/[0.06] overflow-hidden">
                <div
                  className={cn("h-full rounded-full transition-all duration-500", colors[i % colors.length])}
                  style={{ width: `${pct}%` }}
                />
              </div>
            </div>
          );
        })}
      </div>

      <div className="mt-4 pt-3 border-t border-foreground/[0.06] flex items-center justify-between text-xs text-muted-foreground">
        <span>Platform Total</span>
        <div className="flex items-center gap-3 tabular-nums">
          <span>{formatTokens(data.platform_total_input_tokens + data.platform_total_output_tokens)} tok</span>
          <span className="font-semibold text-foreground/80">${data.platform_total_usd.toFixed(2)}</span>
        </div>
      </div>
    </div>
  );
}

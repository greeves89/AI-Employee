"use client";

import Link from "next/link";
import { Cpu, MemoryStick, Layers, ArrowUpRight, UserCheck, UserCog } from "lucide-react";
import type { Agent } from "@/lib/types";
import { cn } from "@/lib/utils";

const statusConfig: Record<string, {
  dot: string;
  badge: string;
  label: string;
  glow?: string;
}> = {
  running: {
    dot: "bg-emerald-500",
    badge: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
    label: "Online",
    glow: "from-emerald-500/20 to-transparent",
  },
  working: {
    dot: "bg-blue-500",
    badge: "bg-blue-500/10 text-blue-400 border-blue-500/20",
    label: "Working",
    glow: "from-blue-500/20 to-transparent",
  },
  idle: {
    dot: "bg-amber-500",
    badge: "bg-amber-500/10 text-amber-400 border-amber-500/20",
    label: "Idle",
  },
  stopped: {
    dot: "bg-zinc-500",
    badge: "bg-zinc-500/10 text-zinc-400 border-zinc-500/20",
    label: "Stopped",
  },
  error: {
    dot: "bg-red-500",
    badge: "bg-red-500/10 text-red-400 border-red-500/20",
    label: "Error",
    glow: "from-red-500/20 to-transparent",
  },
  created: {
    dot: "bg-violet-500",
    badge: "bg-violet-500/10 text-violet-400 border-violet-500/20",
    label: "Starting",
  },
};

interface AgentCardProps {
  agent: Agent;
}

export function AgentCard({ agent }: AgentCardProps) {
  const cpuPercent = agent.cpu_percent ?? 0;
  const memMb = agent.memory_usage_mb ?? 0;
  const config = statusConfig[agent.state] ?? statusConfig.stopped;
  const isActive = agent.state === "running" || agent.state === "working";

  return (
    <Link href={`/agents/${agent.id}`} className="group block">
      <div className="relative overflow-hidden rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-5 transition-all duration-200 hover:border-foreground/[0.1] hover:bg-card/90 hover:shadow-lg hover:shadow-primary/5 hover:-translate-y-0.5">
        {/* Top accent line */}
        {isActive && (
          <div className={cn(
            "absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-primary/50 to-transparent"
          )} />
        )}

        {/* Header */}
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-3">
            <div className={cn(
              "flex h-10 w-10 items-center justify-center rounded-xl transition-colors",
              isActive ? "bg-primary/10" : "bg-foreground/[0.06]"
            )}>
              <Cpu className={cn("h-5 w-5", isActive ? "text-primary" : "text-muted-foreground")} />
            </div>
            <div>
              <h4 className="font-semibold text-sm tracking-tight">{agent.name}</h4>
              <div className="flex items-center gap-1.5">
                {agent.role ? (
                  <span className="text-[11px] text-primary/70 font-medium">{agent.role}</span>
                ) : (
                  <span className="text-[11px] font-mono text-muted-foreground/70">
                    {agent.model.split("-").slice(0, 2).join("-")}
                  </span>
                )}
                {agent.onboarding_complete ? (
                  <span title="Onboarded"><UserCheck className="h-3 w-3 text-emerald-400" /></span>
                ) : (
                  <span title="Needs onboarding"><UserCog className="h-3 w-3 text-amber-400 animate-pulse" /></span>
                )}
              </div>
            </div>
          </div>

          {/* Status badge */}
          <div className={cn(
            "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-medium",
            config.badge
          )}>
            <span className="relative flex h-1.5 w-1.5">
              {isActive && (
                <span className={cn("absolute inline-flex h-full w-full animate-ping rounded-full opacity-75", config.dot)} />
              )}
              <span className={cn("relative inline-flex h-1.5 w-1.5 rounded-full", config.dot)} />
            </span>
            {config.label}
          </div>
        </div>

        {/* Current task */}
        {agent.current_task && (
          <div className="mb-4 rounded-lg bg-blue-500/5 border border-blue-500/10 px-3 py-2">
            <p className="text-[11px] font-medium text-blue-400 truncate">
              {agent.current_task}
            </p>
          </div>
        )}

        {/* Metrics */}
        <div className="space-y-3">
          <div className="space-y-1.5">
            <div className="flex items-center justify-between text-[11px]">
              <span className="flex items-center gap-1.5 text-muted-foreground">
                <Cpu className="h-3 w-3" /> CPU
              </span>
              <span className="font-mono font-medium tabular-nums">{cpuPercent.toFixed(1)}%</span>
            </div>
            <div className="h-1.5 rounded-full bg-foreground/[0.06]">
              <div
                className="h-1.5 rounded-full bg-gradient-to-r from-blue-500 to-cyan-400 shadow-[0_0_8px_rgba(59,130,246,0.3)] transition-all duration-700 ease-out"
                style={{ width: `${Math.min(cpuPercent, 100)}%` }}
              />
            </div>
          </div>

          <div className="space-y-1.5">
            <div className="flex items-center justify-between text-[11px]">
              <span className="flex items-center gap-1.5 text-muted-foreground">
                <MemoryStick className="h-3 w-3" /> Memory
              </span>
              <span className="font-mono font-medium tabular-nums">{memMb.toFixed(0)} MB</span>
            </div>
            <div className="h-1.5 rounded-full bg-foreground/[0.06]">
              <div
                className="h-1.5 rounded-full bg-gradient-to-r from-emerald-500 to-teal-400 shadow-[0_0_8px_rgba(34,197,94,0.3)] transition-all duration-700 ease-out"
                style={{ width: `${Math.min((memMb / 2048) * 100, 100)}%` }}
              />
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="mt-4 flex items-center justify-between pt-3 border-t border-foreground/[0.04]">
          {agent.queue_depth !== null && agent.queue_depth > 0 ? (
            <div className="flex items-center gap-1.5 text-[11px] text-amber-400">
              <Layers className="h-3 w-3" />
              {agent.queue_depth} queued
            </div>
          ) : (
            <span className="text-[11px] text-muted-foreground/50">No queue</span>
          )}
          <div className="flex items-center gap-1 text-[11px] text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity">
            Open <ArrowUpRight className="h-3 w-3" />
          </div>
        </div>
      </div>
    </Link>
  );
}

"use client";

import {
  Bot, Cpu, Brain, Sparkles, Rocket, Briefcase, Cog, MessageSquare, Code,
  Database, Mail, Calendar, FileText, Headphones, ShieldCheck, Stethoscope,
  FlaskConical, Bug, type LucideIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";

// Curated lucide set — MUST match the backend allowlist in api/agents.py.
export const AVATAR_ICONS: Record<string, LucideIcon> = {
  Bot, Cpu, Brain, Sparkles, Rocket, Briefcase, Cog, MessageSquare, Code,
  Database, Mail, Calendar, FileText, Headphones, ShieldCheck, Stethoscope,
  FlaskConical, Bug,
};

export const AVATAR_COLORS: Record<string, { bg: string; text: string; dot: string }> = {
  violet: { bg: "bg-violet-500/10", text: "text-violet-400", dot: "bg-violet-500" },
  blue: { bg: "bg-blue-500/10", text: "text-blue-400", dot: "bg-blue-500" },
  emerald: { bg: "bg-emerald-500/10", text: "text-emerald-400", dot: "bg-emerald-500" },
  amber: { bg: "bg-amber-500/10", text: "text-amber-400", dot: "bg-amber-500" },
  rose: { bg: "bg-rose-500/10", text: "text-rose-400", dot: "bg-rose-500" },
  cyan: { bg: "bg-cyan-500/10", text: "text-cyan-400", dot: "bg-cyan-500" },
  fuchsia: { bg: "bg-fuchsia-500/10", text: "text-fuchsia-400", dot: "bg-fuchsia-500" },
  slate: { bg: "bg-slate-500/10", text: "text-slate-400", dot: "bg-slate-500" },
  orange: { bg: "bg-orange-500/10", text: "text-orange-400", dot: "bg-orange-500" },
};

export function getAgentAvatar(
  config?: Record<string, unknown> | null,
): { icon?: string; color?: string } {
  const a = (config?.avatar ?? null) as { icon?: string; color?: string } | null;
  return a || {};
}

export function AgentAvatar({
  config,
  active = true,
  size = "md",
  className,
}: {
  config?: Record<string, unknown> | null;
  active?: boolean;
  size?: "sm" | "md" | "lg";
  className?: string;
}) {
  const av = getAgentAvatar(config);
  const Icon = (av.icon && AVATAR_ICONS[av.icon]) || Cpu;
  const colors = av.color ? AVATAR_COLORS[av.color] : null;
  const box =
    size === "lg" ? "h-12 w-12 rounded-2xl" : size === "sm" ? "h-8 w-8 rounded-lg" : "h-10 w-10 rounded-xl";
  const ic = size === "lg" ? "h-6 w-6" : size === "sm" ? "h-4 w-4" : "h-5 w-5";
  // A custom color always wins; otherwise fall back to active/idle styling.
  const bg = colors ? colors.bg : active ? "bg-primary/10" : "bg-foreground/[0.06]";
  const fg = colors ? colors.text : active ? "text-primary" : "text-muted-foreground";
  return (
    <div className={cn("flex shrink-0 items-center justify-center transition-colors", box, bg, className)}>
      <Icon className={cn(ic, fg)} />
    </div>
  );
}

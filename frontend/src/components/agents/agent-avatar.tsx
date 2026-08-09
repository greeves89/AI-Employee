"use client";

import dynamic from "next/dynamic";
import {
  Bot, Cpu, Brain, Sparkles, Rocket, Briefcase, Cog, MessageSquare, Code,
  Database, Mail, Calendar, FileText, Headphones, ShieldCheck, Stethoscope,
  FlaskConical, Bug, type LucideIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * Der häufig gebrauchte Satz — statisch importiert und damit sofort da.
 *
 * Seit #523 ist das **keine Sperrliste mehr**, sondern eine Abkürzung: jedes
 * lucide-Sinnbild ist erlaubt, diese hier sind nur ohne Nachladen zur Stelle. Die
 * Prüfung, was gespeichert werden darf, macht der Server (``core.agent_appearance``)
 * über die Form des Namens, nicht über eine Aufzählung.
 */
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

/** Alles außerhalb des kuratierten Satzes — erst beim ersten Bedarf geladen. */
const LazyCatalogIcon = dynamic(
  () => import("./lucide-catalog").then((m) => m.CatalogIcon),
  { ssr: false, loading: () => <span className="inline-block" /> },
);

export const HEX_RE = /^#[0-9a-fA-F]{6}$/;

/** Ist das eine freie Farbe (statt eines Palettennamens)? */
export function isCustomColor(color?: string | null): boolean {
  return !!color && HEX_RE.test(color);
}

/**
 * Farbe in Darstellung übersetzen.
 *
 * Eigene Farben können **nicht** über Tailwind-Klassen laufen: die Klassennamen
 * entstehen beim Bauen, ein zur Laufzeit zusammengesetztes ``bg-[#abc123]`` gibt es
 * in der fertigen Datei nicht. Deshalb Stilangaben — und deshalb prüft der Server,
 * dass hier wirklich nur ein Farbwert ankommt.
 */
export function avatarColorStyle(color?: string | null): {
  bgClass: string | null;
  fgClass: string | null;
  style: React.CSSProperties | undefined;
} {
  if (isCustomColor(color)) {
    return {
      bgClass: null,
      fgClass: null,
      // 1a = etwa 10 % Deckung, dieselbe Wirkung wie die /10 der Palette.
      style: { backgroundColor: `${color}1a`, color: color as string },
    };
  }
  const preset = color ? AVATAR_COLORS[color] : null;
  return {
    bgClass: preset?.bg ?? null,
    fgClass: preset?.text ?? null,
    style: undefined,
  };
}

export function getAgentAvatar(
  config?: Record<string, unknown> | null,
): { icon?: string; color?: string } {
  const a = (config?.avatar ?? null) as { icon?: string; color?: string } | null;
  return a || {};
}

/** Das Schlagwort eines Agenten (#524) — leer, wenn keins gesetzt ist. */
export function getAgentTag(config?: Record<string, unknown> | null): string {
  return String(config?.tag ?? "").trim();
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
  const Curated = av.icon ? AVATAR_ICONS[av.icon] : null;
  const { bgClass, fgClass, style } = avatarColorStyle(av.color);

  const box =
    size === "lg" ? "h-12 w-12 rounded-2xl" : size === "sm" ? "h-8 w-8 rounded-lg" : "h-10 w-10 rounded-xl";
  const ic = size === "lg" ? "h-6 w-6" : size === "sm" ? "h-4 w-4" : "h-5 w-5";

  // Eine eigene Farbe gewinnt immer; sonst entscheidet aktiv/untätig.
  const hasCustomColor = !!style;
  const bg = bgClass ?? (hasCustomColor ? "" : active ? "bg-primary/10" : "bg-foreground/[0.06]");
  const fg = fgClass ?? (hasCustomColor ? "" : active ? "text-primary" : "text-muted-foreground");

  return (
    <div
      className={cn("flex shrink-0 items-center justify-center transition-colors", box, bg, className)}
      style={style}
    >
      {Curated ? (
        <Curated className={cn(ic, fg)} />
      ) : av.icon ? (
        <LazyCatalogIcon name={av.icon} className={cn(ic, fg)} />
      ) : (
        <Cpu className={cn(ic, fg)} />
      )}
    </div>
  );
}

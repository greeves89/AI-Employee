"use client";

/**
 * Bausteine für isometrische Räume — genutzt vom Projektraum
 * (components/projekte/projekt-raum.tsx) und vom Büro der Agenten-Seite
 * (components/raum/buero-ansicht.tsx). Reines SVG, kein WebGL: läuft auch auf
 * schwachen Geräten (der WebGL-Graph des Second Brain stürzte dort ab).
 *
 * Die Agenten liegen als HTML über dem SVG (Avatar, Name, Klick wie überall).
 * Damit Namen auf schmalen Bildschirmen nicht übereinander liegen, wird diese
 * Ebene mit dem Raum skaliert (``RaumEbene``) und Text unter einer Mindestgröße
 * ausgeblendet.
 */

import { useEffect, useRef, useState } from "react";
import { Crown } from "lucide-react";
import type { Agent } from "@/lib/types";
import { AgentAvatar } from "@/components/agents/agent-avatar";
import { cn } from "@/lib/utils";

export type Punkt = [number, number];
export type Projektion = (x: number, y: number, z?: number) => Punkt;

/** Rahmen eines Raums mit ``breite`` × ``tiefe`` Rasterfeldern und Wandhöhe ``wand``. */
export function isoRahmen(breite: number, tiefe: number, wand: number, u: number, rand = 16) {
  const cx = tiefe * u * 0.866 + rand;
  const cy = wand * u + rand;
  const p: Projektion = (x, y, z = 0) => [cx + (x - y) * u * 0.866, cy + (x + y) * u * 0.5 - z * u];
  return {
    p,
    u,
    breite: (breite + tiefe) * u * 0.866 + 2 * rand,
    hoehe: (breite + tiefe) * u * 0.5 + wand * u + 2 * rand,
  };
}

export function punkte(...ps: Punkt[]): string {
  return ps.map(([a, b]) => `${a.toFixed(1)},${b.toFixed(1)}`).join(" ");
}

/** Fläche in Vordergrundfarbe mit Deckung — passt sich hellem und dunklem Modus an. */
export const fg = (o: number): React.CSSProperties => ({ fill: "hsl(var(--foreground))", fillOpacity: o });

/** Quader: Deckel, linke (dem Betrachter zugewandte) und rechte Seite. */
export function Quader({
  p, x, y, w, d, h, z = 0, deckel, seiteL, seiteR, kante,
}: {
  p: Projektion; x: number; y: number; w: number; d: number; h: number; z?: number;
  deckel: React.CSSProperties; seiteL: React.CSSProperties; seiteR: React.CSSProperties;
  kante?: boolean;
}) {
  const o = z + h;
  const strich = kante ? { stroke: "hsl(var(--foreground))", strokeOpacity: 0.25, strokeWidth: 0.6 } : {};
  return (
    <g>
      <polygon points={punkte(p(x, y + d, z), p(x + w, y + d, z), p(x + w, y + d, o), p(x, y + d, o))} style={{ ...seiteL, ...strich }} />
      <polygon points={punkte(p(x + w, y, z), p(x + w, y + d, z), p(x + w, y + d, o), p(x + w, y, o))} style={{ ...seiteR, ...strich }} />
      <polygon points={punkte(p(x, y, o), p(x + w, y, o), p(x + w, y + d, o), p(x, y + d, o))} style={{ ...deckel, ...strich }} />
    </g>
  );
}

/** Weichzeichner für Leuchten — je Raum eine eigene Kennung (mehrere Räume je Seite). */
export function GlowFilter({ id }: { id: string }) {
  return (
    <filter id={id} x="-50%" y="-50%" width="200%" height="200%">
      <feGaussianBlur stdDeviation="4" />
    </filter>
  );
}

/** Schreibtisch mit Bildschirm; arbeitet der Agent, leuchtet der Bildschirm und schreibt Zeilen. */
export function Schreibtisch({
  p, x, y, w = 1.9, d = 1.1, arbeitet, farbe, glow, platzX, platzY, besetzt = true,
}: {
  p: Projektion; x: number; y: number; w?: number; d?: number;
  arbeitet: boolean; farbe: string; glow: string;
  /** Leere Plätze (Büro) ohne Bodenfleck — sonst wirkt der Raum unruhig. */
  besetzt?: boolean;
  /** Wo der Agent steht (Bodenfleck). */
  platzX: number; platzY: number;
}) {
  const h = 0.75;
  const mx0 = x + w * 0.24;
  const mx1 = x + w * 0.76;
  const my = y + 0.25;
  const [fx, fy] = p(platzX, platzY);
  return (
    <g>
      {besetzt && <ellipse cx={fx} cy={fy} rx={22} ry={11}
        fill={arbeitet ? "#10b981" : "hsl(var(--foreground))"} opacity={arbeitet ? 0.35 : 0.08}
        filter={arbeitet ? `url(#${glow})` : undefined}
        className={arbeitet ? "raum-pulsen" : undefined} />}
      <Quader p={p} x={x} y={y} w={w} d={d} h={h} deckel={fg(0.2)} seiteL={fg(0.12)} seiteR={fg(0.16)} />
      <polygon
        points={punkte(p(mx0, my, h), p(mx1, my, h), p(mx1, my, h + 0.72), p(mx0, my, h + 0.72))}
        fill={arbeitet ? farbe : "hsl(var(--foreground))"} fillOpacity={arbeitet ? 0.85 : 0.22}
        stroke="hsl(var(--foreground))" strokeOpacity={0.3}
      />
      {arbeitet && [0.2, 0.34, 0.48].map((dz, i) => (
        <line key={dz} pathLength={1} className="raum-tippen" style={{ animationDelay: `${i * 0.35}s` }}
          x1={p(mx0 + 0.12, my, h + dz)[0]} y1={p(mx0 + 0.12, my, h + dz)[1]}
          x2={p(mx1 - 0.15, my, h + dz)[0]} y2={p(mx1 - 0.15, my, h + dz)[1]}
          stroke="#fff" strokeOpacity={0.75} strokeWidth={1.5} strokeLinecap="round" />
      ))}
    </g>
  );
}

/** Topfpflanze als Schmuck. */
export function Pflanze({ p, x, y }: { p: Projektion; x: number; y: number }) {
  const [bx, by] = p(x + 0.3, y + 0.3, 0.9);
  return (
    <g>
      <Quader p={p} x={x} y={y} w={0.6} d={0.6} h={0.55} deckel={fg(0.25)} seiteL={fg(0.16)} seiteR={fg(0.2)} />
      <circle cx={bx} cy={by} r={13} fill="#22c55e" opacity={0.55} />
      <circle cx={bx - 6} cy={by - 6} r={8} fill="#16a34a" opacity={0.6} />
      <circle cx={bx + 6} cy={by - 3} r={7} fill="#4ade80" opacity={0.5} />
    </g>
  );
}

export type RaumZustand = "arbeitet" | "bereit" | "aus" | "fehler";

export function raumZustand(agent?: Agent): RaumZustand {
  if (!agent) return "aus";
  if (agent.state === "working") return "arbeitet";
  if (agent.state === "error") return "fehler";
  if (agent.state === "stopped") return "aus";
  return "bereit";
}

const ZUSTAND_RING: Record<RaumZustand, string> = {
  arbeitet: "ring-emerald-500/70",
  bereit: "ring-sky-500/40",
  aus: "ring-foreground/10 opacity-60",
  fehler: "ring-red-500/70",
};

/** Misst die Breite des Raums und liefert den Maßstab gegenüber der viewBox. */
export function useMassstab(basisBreite: number) {
  const ref = useRef<HTMLDivElement>(null);
  const [massstab, setMassstab] = useState(1);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const messen = () => setMassstab(el.clientWidth / basisBreite || 1);
    messen();
    const beob = new ResizeObserver(messen);
    beob.observe(el);
    return () => beob.disconnect();
  }, [basisBreite]);
  return { ref, massstab };
}

/** HTML-Ebene über dem SVG in viewBox-Pixeln; skaliert mit dem Raum. */
export function RaumEbene({
  breite, hoehe, massstab, children,
}: { breite: number; hoehe: number; massstab: number; children: React.ReactNode }) {
  return (
    <div
      className="pointer-events-none absolute left-0 top-0 origin-top-left"
      style={{ width: breite, height: hoehe, transform: `scale(${massstab})` }}
    >
      {children}
    </div>
  );
}

/** Ein Agent an seinem Platz (Position in viewBox-Pixeln, Fußpunkt unten Mitte). */
export function AgentFigur({
  agent, x, y, titel, istLead, mitText, klein, onClick, hinweis,
}: {
  agent?: Agent;
  x: number;
  y: number;
  /** Sprechblase über dem Agenten (z. B. laufender Auftrag). */
  titel?: string;
  istLead?: boolean;
  /** Name und Sprechblase zeigen — aus, wenn der Raum zu klein dargestellt wird. */
  mitText: boolean;
  klein?: boolean;
  onClick?: () => void;
  hinweis?: string;
}) {
  const zustand = raumZustand(agent);
  return (
    <div
      className="pointer-events-auto absolute flex -translate-x-1/2 -translate-y-full flex-col items-center"
      style={{ left: x, top: y + 4 }}
    >
      {mitText && titel && (
        <div className="mb-1 line-clamp-2 max-w-[11rem] rounded-lg bg-background/95 px-2 py-0.5 text-[10px] leading-snug shadow ring-1 ring-emerald-500/30" title={titel}>
          {titel}
        </div>
      )}
      <button
        type="button"
        onClick={onClick}
        disabled={!onClick}
        title={hinweis ?? (agent ? `${agent.name}${zustand === "arbeitet" ? " — arbeitet" : ""}` : "Agent gelöscht")}
        aria-label={agent?.name ?? "Gelöschter Agent"}
        className={cn(
          "relative rounded-xl ring-2 transition-transform",
          ZUSTAND_RING[zustand],
          zustand === "arbeitet" && "raum-wippen",
          onClick && "hover:scale-110",
        )}
      >
        <AgentAvatar config={agent?.config} size={klein ? "sm" : "md"} active={zustand !== "aus"} />
        {istLead && <Crown className="absolute -right-1.5 -top-1.5 h-3.5 w-3.5 text-amber-500 drop-shadow" />}
      </button>
      {mitText && (
        <span className="mt-1 max-w-[7rem] truncate rounded bg-background/80 px-1.5 text-[10px] font-medium">
          {agent?.name ?? "gelöscht"}
        </span>
      )}
    </div>
  );
}

/** Text auf einer Wand entlang der x-Achse, auf ``laenge`` Rasterfelder eingepasst. */
export function WandText({
  p, x, z, laenge, u, text, farbe, max = 28,
}: { p: Projektion; x: number; z: number; laenge: number; u: number; text: string; farbe: string; max?: number }) {
  const platz = laenge * u; // Länge entlang der Wand in Text-Einheiten (die Matrix erhält Längen)
  const groesse = Math.max(12, Math.min(max, platz / Math.max(1, text.length * 0.62)));
  const zeichen = Math.floor(platz / (groesse * 0.62));
  const anzeige = text.length > zeichen ? `${text.slice(0, Math.max(1, zeichen - 1))}…` : text;
  const [tx, ty] = p(x, 0, z);
  return (
    <text transform={`matrix(0.866,0.5,0,1,${tx},${ty})`} fontSize={groesse} fontWeight={700} fill={farbe} opacity={0.9}>
      {anzeige}
    </text>
  );
}

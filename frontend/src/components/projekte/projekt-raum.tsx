"use client";

/**
 * Der Raum eines Projekts — isometrisch als SVG, ohne WebGL (läuft auch auf
 * schwachen Geräten; der WebGL-Graph des Second Brain stürzte dort ab).
 *
 * Im Raum: je Mitglied ein Schreibtisch mit Bildschirm (leuchtet, wenn der
 * Agent arbeitet), der Stapel wartender Aufträge und ein Board an der Wand
 * (In Arbeit / Zur Abnahme / heute fertig). Die Agenten selbst liegen als
 * HTML über dem SVG, damit Avatar, Name und Klick wie überall funktionieren.
 */

import { useId } from "react";
import { Crown } from "lucide-react";
import type { Agent } from "@/lib/types";
import type { ProjektKarte, ProjektKennzahlen } from "@/lib/projekte";
import { AgentAvatar } from "@/components/agents/agent-avatar";
import { cn } from "@/lib/utils";

const B = 600; // viewBox-Breite
const H = 475; // viewBox-Höhe
const U = 32; // Einheit des Bodenrasters in Pixeln
const CX = 300;
const CY = 145;
const WAND = 4.2;

function p(x: number, y: number, z = 0): [number, number] {
  return [CX + (x - y) * U * 0.866, CY + (x + y) * U * 0.5 - z * U];
}

function punkte(...ps: [number, number][]): string {
  return ps.map(([a, b]) => `${a.toFixed(1)},${b.toFixed(1)}`).join(" ");
}

const fg = (o: number) => ({ fill: "hsl(var(--foreground))", fillOpacity: o });

/** Quader: Deckel, Vorderseite (zum Betrachter links), rechte Seite. */
function Quader({
  x, y, w, d, h, z = 0, deckel, seiteL, seiteR,
}: {
  x: number; y: number; w: number; d: number; h: number; z?: number;
  deckel: React.CSSProperties; seiteL: React.CSSProperties; seiteR: React.CSSProperties;
}) {
  const o = z + h;
  return (
    <g>
      <polygon points={punkte(p(x, y + d, z), p(x + w, y + d, z), p(x + w, y + d, o), p(x, y + d, o))} style={seiteL} />
      <polygon points={punkte(p(x + w, y, z), p(x + w, y + d, z), p(x + w, y + d, o), p(x + w, y, o))} style={seiteR} />
      <polygon points={punkte(p(x, y, o), p(x + w, y, o), p(x + w, y + d, o), p(x, y + d, o))} style={deckel} />
    </g>
  );
}

// Plätze für Schreibtische (Bodenraster). Mehr Mitglieder → Hinweis „+N“.
const PLAETZE: [number, number][] = [
  [1.6, 4.0], [4.6, 4.0], [7.4, 4.0],
  [1.6, 7.2], [4.6, 7.2], [7.4, 7.2],
];
const TISCH = { w: 1.9, d: 1.1, h: 0.75 };

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
  bereit: "ring-foreground/20",
  aus: "ring-foreground/10 opacity-60",
  fehler: "ring-red-500/70",
};

export function ProjektRaum({
  name,
  farbe,
  mitglieder,
  leadId,
  kennzahlen,
  laufend,
  kompakt = false,
  onAgent,
  onStapel,
}: {
  name: string;
  farbe: string;
  /** In Reihenfolge der Mitglieder; fehlende Agenten (gelöscht) sind undefined. */
  mitglieder: { id: string; agent?: Agent }[];
  leadId?: string | null;
  kennzahlen: ProjektKennzahlen;
  /** Laufende Aufträge — für die Sprechblase über dem Agenten. */
  laufend: ProjektKarte[];
  kompakt?: boolean;
  onAgent?: (agentId: string) => void;
  onStapel?: () => void;
}) {
  const plaetze = mitglieder.slice(0, PLAETZE.length);
  const weitere = mitglieder.length - plaetze.length;
  const blaetter = Math.min(kennzahlen.wartet, 12);
  const zettel = [
    ...Array(Math.min(kennzahlen.in_arbeit, 4)).fill("#3b82f6"),
    ...Array(Math.min(kennzahlen.zur_abnahme, 4)).fill("#f97316"),
    ...Array(Math.min(kennzahlen.heute_fertig, 4)).fill("#22c55e"),
  ].slice(0, 12);
  const titelJeAgent = new Map<string, string>();
  for (const k of laufend) if (k.agent_id && !titelJeAgent.has(k.agent_id)) titelJeAgent.set(k.agent_id, k.titel);

  const [sx, sy] = p(1.2, 1.4, 0.9);
  // Eigene Filter-Kennung je Raum: die Übersicht zeigt mehrere Räume auf einer Seite.
  const glow = `raum-glow-${useId().replace(/[^a-zA-Z0-9]/g, "")}`;

  return (
    <div className="relative w-full select-none" style={{ aspectRatio: `${B} / ${H}` }}>
      <svg viewBox={`0 0 ${B} ${H}`} className="absolute inset-0 h-full w-full" aria-hidden>
        <defs>
          <filter id={glow} x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur stdDeviation="4" />
          </filter>
        </defs>

        {/* Boden und Wände */}
        <polygon points={punkte(p(0, 0), p(10, 0), p(10, 10), p(0, 10))} style={fg(0.05)} />
        <polygon points={punkte(p(0, 0), p(0, 10), p(0, 10, WAND), p(0, 0, WAND))} style={fg(0.08)} />
        <polygon points={punkte(p(0, 0), p(10, 0), p(10, 0, WAND), p(0, 0, WAND))} style={fg(0.11)} />
        {/* Kanten */}
        <polyline points={punkte(p(0, 10, WAND), p(0, 0, WAND), p(10, 0, WAND))} fill="none" stroke="hsl(var(--foreground))" strokeOpacity={0.15} />
        <line x1={p(0, 0)[0]} y1={p(0, 0)[1]} x2={p(0, 0, WAND)[0]} y2={p(0, 0, WAND)[1]} stroke="hsl(var(--foreground))" strokeOpacity={0.12} />

        {/* Leuchtleiste in Projektfarbe */}
        <line x1={p(0.3, 0, 3.9)[0]} y1={p(0.3, 0, 3.9)[1]} x2={p(9.7, 0, 3.9)[0]} y2={p(9.7, 0, 3.9)[1]}
          stroke={farbe} strokeWidth={6} strokeLinecap="round" opacity={0.55} filter={`url(#${glow})`} />
        <line x1={p(0.3, 0, 3.9)[0]} y1={p(0.3, 0, 3.9)[1]} x2={p(9.7, 0, 3.9)[0]} y2={p(9.7, 0, 3.9)[1]}
          stroke={farbe} strokeWidth={2.5} strokeLinecap="round" />
        <line x1={p(0, 0.3, 0.02)[0]} y1={p(0, 0.3, 0.02)[1]} x2={p(0, 9.7, 0.02)[0]} y2={p(0, 9.7, 0.02)[1]}
          stroke={farbe} strokeWidth={2} strokeLinecap="round" opacity={0.6} />

        {/* Projektname an der Wand */}
        {!kompakt && (
          <text
            transform={`matrix(0.866,0.5,0,1,${p(4.6, 0, 3.0)[0]},${p(4.6, 0, 3.0)[1]})`}
            fontSize={28} fontWeight={700} fill={farbe} opacity={0.9}
          >
            {name.length > 18 ? `${name.slice(0, 17)}…` : name}
          </text>
        )}

        {/* Tür an der linken Wand */}
        <polygon points={punkte(p(0, 2.2, 0), p(0, 3.6, 0), p(0, 3.6, 2.6), p(0, 2.2, 2.6))} style={fg(0.14)} />
        <circle cx={p(0, 3.35, 1.3)[0]} cy={p(0, 3.35, 1.3)[1]} r={2} style={fg(0.4)} />

        {/* Board an der rechten Wand: Zettel je Zustand */}
        <polygon points={punkte(p(1.0, 0, 1.4), p(4.0, 0, 1.4), p(4.0, 0, 2.9), p(1.0, 0, 2.9))}
          style={fg(0.06)} stroke="hsl(var(--foreground))" strokeOpacity={0.2} />
        {zettel.map((farbeZettel, i) => {
          const sp = i % 4;
          const ze = Math.floor(i / 4);
          const x0 = 1.25 + sp * 0.7;
          const z0 = 2.55 - ze * 0.45;
          return (
            <polygon key={i} points={punkte(p(x0, 0, z0), p(x0 + 0.45, 0, z0), p(x0 + 0.45, 0, z0 + 0.3), p(x0, 0, z0 + 0.3))}
              fill={farbeZettel} opacity={0.9} />
          );
        })}

        {/* Stapel wartender Aufträge */}
        <g onClick={onStapel} style={{ cursor: onStapel ? "pointer" : undefined }}>
          <Quader x={0.6} y={0.8} w={1.6} d={1.3} h={0.5} deckel={fg(0.16)} seiteL={fg(0.1)} seiteR={fg(0.13)} />
          {Array.from({ length: blaetter }).map((_, i) => (
            <Quader key={i} x={0.75 + (i % 2) * 0.05} y={0.95} w={1.25} d={1.0} h={0.07} z={0.5 + i * 0.075}
              deckel={{ fill: i % 3 === 0 ? "#fbbf24" : "#f8fafc" }} seiteL={{ fill: "#cbd5e1" }} seiteR={{ fill: "#e2e8f0" }} />
          ))}
        </g>

        {/* Schreibtische */}
        {plaetze.map(({ id, agent }, i) => {
          const [x, y] = PLAETZE[i];
          const arbeitet = raumZustand(agent) === "arbeitet";
          const z = TISCH.h;
          return (
            <g key={id}>
              <ellipse cx={p(x + 0.95, y + 2.05)[0]} cy={p(x + 0.95, y + 2.05)[1]} rx={22} ry={11}
                fill={arbeitet ? "#10b981" : "hsl(var(--foreground))"} opacity={arbeitet ? 0.35 : 0.08}
                filter={arbeitet ? `url(#${glow})` : undefined} />
              <Quader x={x} y={y} w={TISCH.w} d={TISCH.d} h={TISCH.h} deckel={fg(0.2)} seiteL={fg(0.12)} seiteR={fg(0.16)} />
              {/* Bildschirm */}
              <polygon
                points={punkte(p(x + 0.45, y + 0.25, z), p(x + 1.45, y + 0.25, z), p(x + 1.45, y + 0.25, z + 0.75), p(x + 0.45, y + 0.25, z + 0.75))}
                fill={arbeitet ? farbe : "hsl(var(--foreground))"} fillOpacity={arbeitet ? 0.85 : 0.25}
                stroke="hsl(var(--foreground))" strokeOpacity={0.3}
              />
              {arbeitet && [0.2, 0.35, 0.5].map((dz) => (
                <line key={dz}
                  x1={p(x + 0.6, y + 0.25, z + dz)[0]} y1={p(x + 0.6, y + 0.25, z + dz)[1]}
                  x2={p(x + 1.2, y + 0.25, z + dz)[0]} y2={p(x + 1.2, y + 0.25, z + dz)[1]}
                  stroke="#fff" strokeOpacity={0.7} strokeWidth={1.5} />
              ))}
            </g>
          );
        })}
      </svg>

      {/* Zahl am Stapel */}
      {!kompakt && kennzahlen.wartet > 0 && (
        <button
          type="button"
          onClick={onStapel}
          className="absolute -translate-x-1/2 -translate-y-full rounded-md bg-background/90 px-1.5 py-0.5 text-[10px] font-medium shadow ring-1 ring-foreground/10"
          style={{ left: `${(sx / B) * 100}%`, top: `${((sy - 6 - blaetter * 2.4) / H) * 100}%` }}
        >
          {kennzahlen.wartet} wartet
        </button>
      )}

      {/* Agenten */}
      {plaetze.map(({ id, agent }, i) => {
        const [x, y] = PLAETZE[i];
        const [ax, ay] = p(x + 0.95, y + 2.05);
        const zustand = raumZustand(agent);
        const titel = titelJeAgent.get(id);
        return (
          <div
            key={id}
            className="absolute flex -translate-x-1/2 -translate-y-full flex-col items-center"
            style={{ left: `${(ax / B) * 100}%`, top: `${((ay + 4) / H) * 100}%` }}
          >
            {!kompakt && titel && (
              <div className="mb-1 max-w-[9rem] truncate rounded-lg bg-background/95 px-2 py-0.5 text-[10px] shadow ring-1 ring-emerald-500/30" title={titel}>
                {titel}
              </div>
            )}
            <button
              type="button"
              onClick={() => onAgent?.(id)}
              disabled={!onAgent}
              title={agent ? `${agent.name}${zustand === "arbeitet" ? " — arbeitet" : ""}` : "Agent gelöscht"}
              className={cn(
                "relative rounded-xl ring-2 transition-transform",
                ZUSTAND_RING[zustand],
                zustand === "arbeitet" && "animate-pulse",
                onAgent && "hover:scale-110",
              )}
            >
              <AgentAvatar config={agent?.config} size={kompakt ? "sm" : "md"} active={zustand !== "aus"} />
              {leadId === id && (
                <Crown className="absolute -right-1.5 -top-1.5 h-3.5 w-3.5 text-amber-500 drop-shadow" />
              )}
            </button>
            {!kompakt && (
              <span className="mt-1 max-w-[7rem] truncate rounded bg-background/80 px-1.5 text-[10px] font-medium">
                {agent?.name ?? "gelöscht"}
              </span>
            )}
          </div>
        );
      })}

      {weitere > 0 && (
        <span className="absolute bottom-1 right-2 rounded-md bg-background/90 px-2 py-0.5 text-[10px] text-muted-foreground ring-1 ring-foreground/10">
          +{weitere} weitere
        </span>
      )}
      {mitglieder.length === 0 && !kompakt && (
        <span className="absolute left-1/2 top-[62%] -translate-x-1/2 rounded-lg bg-background/90 px-3 py-1.5 text-xs text-muted-foreground ring-1 ring-foreground/10">
          Noch niemand im Raum — weise dem Projekt Agenten zu.
        </span>
      )}
    </div>
  );
}

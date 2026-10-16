"use client";

/**
 * Der Raum eines Projekts. Je Mitglied ein Schreibtisch (Bildschirm leuchtet,
 * wenn der Agent arbeitet), der Stapel wartender Aufträge und ein Board an der
 * Wand (In Arbeit / Zur Abnahme / heute fertig). Bausteine: components/raum/iso.
 */

import { useId } from "react";
import type { Agent } from "@/lib/types";
import type { ProjektKennzahlen, ProjektLauf } from "@/lib/projekte";
import {
  AgentFigur, GlowFilter, Quader, RaumEbene, Schreibtisch, WandText,
  fg, isoRahmen, punkte, raumZustand, useMassstab,
} from "@/components/raum/iso";

const WAND = 4.2;
const RAHMEN = isoRahmen(10, 10, WAND, 32, 23);
const { p, breite: B, hoehe: H } = RAHMEN;

// Plätze für Schreibtische (Bodenraster). Mehr Mitglieder → Hinweis „+N“.
const PLAETZE: [number, number][] = [
  [1.6, 4.0], [4.6, 4.0], [7.4, 4.0],
  [1.6, 7.2], [4.6, 7.2], [7.4, 7.2],
];

/** Unter diesem Maßstab sind Namen und Sprechblasen zu klein — dann nur Avatare. */
const TEXT_AB = 0.72;

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
  /** Was gerade läuft — für die Sprechblase über dem Agenten. */
  laufend: ProjektLauf[];
  kompakt?: boolean;
  onAgent?: (agentId: string) => void;
  onStapel?: () => void;
}) {
  const { ref, massstab } = useMassstab(B);
  // Eigene Filter-Kennung je Raum: die Übersicht zeigt mehrere Räume auf einer Seite.
  const glow = `raum-glow-${useId().replace(/[^a-zA-Z0-9]/g, "")}`;
  const mitText = !kompakt && massstab >= TEXT_AB;

  const plaetze = mitglieder.slice(0, PLAETZE.length);
  const weitere = mitglieder.length - plaetze.length;
  // Der Stapel an der Tür: alles, was noch nicht vergeben ist (Eingang + Bereit).
  const offen = kennzahlen.eingang + kennzahlen.bereit;
  const blaetter = Math.min(offen, 12);
  const zettel = [
    ...Array(Math.min(kennzahlen.in_arbeit, 4)).fill("#3b82f6"),
    ...Array(Math.min(kennzahlen.pruefung, 4)).fill("#f97316"),
    ...Array(Math.min(kennzahlen.heute_fertig, 4)).fill("#22c55e"),
  ].slice(0, 12);
  const titelJeAgent = new Map<string, string>();
  for (const l of laufend) {
    if (l.agent_id && !titelJeAgent.has(l.agent_id)) titelJeAgent.set(l.agent_id, l.karte_titel ?? l.titel);
  }
  const [sx, sy] = p(1.4, 1.45, 0.55 + blaetter * 0.075);

  return (
    <div ref={ref} className="relative w-full select-none overflow-hidden" style={{ aspectRatio: `${B} / ${H}` }}>
      <svg viewBox={`0 0 ${B} ${H}`} className="absolute inset-0 h-full w-full" aria-hidden>
        <defs>
          <GlowFilter id={glow} />
        </defs>

        {/* Boden und Wände */}
        <polygon points={punkte(p(0, 0), p(10, 0), p(10, 10), p(0, 10))} style={fg(0.05)} />
        <polygon points={punkte(p(0, 0), p(0, 10), p(0, 10, WAND), p(0, 0, WAND))} style={fg(0.08)} />
        <polygon points={punkte(p(0, 0), p(10, 0), p(10, 0, WAND), p(0, 0, WAND))} style={fg(0.11)} />
        <polyline points={punkte(p(0, 10, WAND), p(0, 0, WAND), p(10, 0, WAND))} fill="none" stroke="hsl(var(--foreground))" strokeOpacity={0.15} />
        <line x1={p(0, 0)[0]} y1={p(0, 0)[1]} x2={p(0, 0, WAND)[0]} y2={p(0, 0, WAND)[1]} stroke="hsl(var(--foreground))" strokeOpacity={0.12} />

        {/* Leuchtleisten in Projektfarbe */}
        <line x1={p(0.3, 0, 3.9)[0]} y1={p(0.3, 0, 3.9)[1]} x2={p(9.7, 0, 3.9)[0]} y2={p(9.7, 0, 3.9)[1]}
          stroke={farbe} strokeWidth={6} strokeLinecap="round" opacity={0.55} filter={`url(#${glow})`} />
        <line x1={p(0.3, 0, 3.9)[0]} y1={p(0.3, 0, 3.9)[1]} x2={p(9.7, 0, 3.9)[0]} y2={p(9.7, 0, 3.9)[1]}
          stroke={farbe} strokeWidth={2.5} strokeLinecap="round" />
        <line x1={p(0, 0.3, 0.02)[0]} y1={p(0, 0.3, 0.02)[1]} x2={p(0, 9.7, 0.02)[0]} y2={p(0, 9.7, 0.02)[1]}
          stroke={farbe} strokeWidth={2} strokeLinecap="round" opacity={0.6} />

        {/* Projektname auf der Wand rechts vom Board, eingepasst */}
        {!kompakt && <WandText p={p} x={4.5} z={3.0} laenge={5.1} u={RAHMEN.u} text={name} farbe={farbe} />}

        {/* Tür an der linken Wand */}
        <polygon points={punkte(p(0, 2.2, 0), p(0, 3.6, 0), p(0, 3.6, 2.6), p(0, 2.2, 2.6))} style={fg(0.14)} />
        <circle cx={p(0, 3.35, 1.3)[0]} cy={p(0, 3.35, 1.3)[1]} r={2} style={fg(0.4)} />

        {/* Board an der rechten Wand: Zettel je Zustand */}
        <polygon points={punkte(p(1.0, 0, 1.4), p(4.0, 0, 1.4), p(4.0, 0, 2.9), p(1.0, 0, 2.9))}
          style={fg(0.06)} stroke="hsl(var(--foreground))" strokeOpacity={0.2} />
        {zettel.map((farbeZettel, i) => {
          const x0 = 1.25 + (i % 4) * 0.7;
          const z0 = 2.55 - Math.floor(i / 4) * 0.45;
          return (
            <polygon key={i} points={punkte(p(x0, 0, z0), p(x0 + 0.45, 0, z0), p(x0 + 0.45, 0, z0 + 0.3), p(x0, 0, z0 + 0.3))}
              fill={farbeZettel} opacity={0.9} />
          );
        })}

        {/* Stapel wartender Aufträge — Blätter mit Kante, damit sie auch im hellen Modus zu sehen sind */}
        <g onClick={onStapel} style={{ cursor: onStapel ? "pointer" : undefined }}>
          <Quader p={p} x={0.6} y={0.8} w={1.6} d={1.3} h={0.5} deckel={fg(0.16)} seiteL={fg(0.1)} seiteR={fg(0.13)} />
          {Array.from({ length: blaetter }).map((_, i) => (
            <Quader key={i} p={p} x={0.75 + (i % 2) * 0.05} y={0.95} w={1.25} d={1.0} h={0.07} z={0.5 + i * 0.075} kante
              deckel={{ fill: i % 3 === 0 ? "#fbbf24" : "hsl(var(--background))" }} seiteL={fg(0.18)} seiteR={fg(0.12)} />
          ))}
        </g>

        {/* Schreibtische (hintere Reihe zuerst — sie liegt optisch dahinter) */}
        {plaetze.map(({ id, agent }, i) => {
          const [x, y] = PLAETZE[i];
          return (
            <Schreibtisch key={id} p={p} x={x} y={y} glow={glow} farbe={farbe}
              arbeitet={raumZustand(agent) === "arbeitet"} platzX={x + 0.95} platzY={y + 2.05} />
          );
        })}
      </svg>

      <RaumEbene breite={B} hoehe={H} massstab={massstab}>
        {mitText && offen > 0 && (
          <button
            type="button"
            onClick={onStapel}
            className="pointer-events-auto absolute -translate-x-1/2 -translate-y-full rounded-md bg-background/90 px-1.5 py-0.5 text-[10px] font-medium shadow ring-1 ring-foreground/10"
            style={{ left: sx, top: sy - 4 }}
          >
            {offen} offen
          </button>
        )}
        {plaetze.map(({ id, agent }, i) => {
          const [x, y] = PLAETZE[i];
          const [ax, ay] = p(x + 0.95, y + 2.05);
          return (
            <AgentFigur key={id} agent={agent} x={ax} y={ay} titel={titelJeAgent.get(id)} istLead={leadId === id}
              mitText={mitText} klein={kompakt} onClick={onAgent ? () => onAgent(id) : undefined} />
          );
        })}
      </RaumEbene>

      {weitere > 0 && (
        <span className="absolute bottom-1 right-2 rounded-md bg-background/90 px-2 py-0.5 text-[10px] text-muted-foreground ring-1 ring-foreground/10">
          +{weitere} weitere
        </span>
      )}
      {mitglieder.length === 0 && !kompakt && (
        <span className="absolute left-1/2 top-[62%] -translate-x-1/2 rounded-lg bg-background/90 px-3 py-1.5 text-center text-xs text-muted-foreground ring-1 ring-foreground/10">
          Noch niemand im Raum — weise dem Projekt Agenten zu.
        </span>
      )}
    </div>
  );
}

"use client";

/**
 * Agenten-Seite, Ansicht „Büro“: alle Agenten an ihren Plätzen. Das Büro ist in
 * Bereiche zu je sechs Plätzen geteilt (Werkstatt, Bibliothek, Lounge,
 * Fensterfront, bei Bedarf Anbauten). Wer arbeitet, wippt am leuchtenden
 * Bildschirm; ein Klick öffnet den Agenten.
 */

import { useId, useMemo } from "react";
import { useRouter } from "next/navigation";
import type { Agent } from "@/lib/types";
import { agentZustand } from "@/lib/aufgaben-anzeige";
import {
  AgentFigur, GlowFilter, Pflanze, RaumEbene, Schreibtisch,
  fg, isoRahmen, punkte, raumZustand, useMassstab,
} from "./iso";
import { PLAETZE_JE_BEREICH, bereichFarbe, bereichName, sitzordnung } from "./buero";

const SPALTEN = 2;
const ZONE_B = 6.2; // Breite eines Bereichs in Rasterfeldern
const ZONE_T = 5.4; // Tiefe
const GANG = 1.0;
const RAND = 1.0;
const WAND = 3.0;
// Plätze in einem Bereich: zwei Reihen zu drei Tischen.
const TISCHE: [number, number][] = [
  [0.3, 0.7], [2.3, 0.7], [4.3, 0.7],
  [0.3, 3.0], [2.3, 3.0], [4.3, 3.0],
];
const TISCH_B = 1.6;
const TISCH_T = 0.9;
const TEXT_AB = 0.62;

function zoneUrsprung(bereich: number): [number, number] {
  const spalte = bereich % SPALTEN;
  const reihe = Math.floor(bereich / SPALTEN);
  return [RAND + spalte * (ZONE_B + GANG), RAND + reihe * (ZONE_T + GANG)];
}

export function BueroAnsicht({ agents }: { agents: Agent[] }) {
  const router = useRouter();
  const glow = `buero-glow-${useId().replace(/[^a-zA-Z0-9]/g, "")}`;
  const { sitze, bereiche } = useMemo(() => sitzordnung(agents), [agents]);

  const reihen = Math.ceil(bereiche / SPALTEN);
  const breite = 2 * RAND + SPALTEN * ZONE_B + (SPALTEN - 1) * GANG;
  const tiefe = 2 * RAND + reihen * ZONE_T + (reihen - 1) * GANG;
  const rahmen = useMemo(() => isoRahmen(breite, tiefe, WAND, 26, 20), [breite, tiefe]);
  const { p, breite: B, hoehe: H } = rahmen;
  const { ref, massstab } = useMassstab(B);
  const mitText = massstab >= TEXT_AB;

  // Alle Plätze mit ihrem Agenten (oder leer), von hinten nach vorn gezeichnet.
  const plaetze = useMemo(() => {
    const jeSitz = new Map<string, Agent>();
    for (const a of agents) {
      const s = sitze.get(a.id);
      if (s) jeSitz.set(`${s.bereich}:${s.platz}`, a);
    }
    const liste: { bereich: number; platz: number; x: number; y: number; agent?: Agent }[] = [];
    for (let b = 0; b < bereiche; b++) {
      const [zx, zy] = zoneUrsprung(b);
      for (let pl = 0; pl < PLAETZE_JE_BEREICH; pl++) {
        const [dx, dy] = TISCHE[pl];
        liste.push({ bereich: b, platz: pl, x: zx + dx, y: zy + dy, agent: jeSitz.get(`${b}:${pl}`) });
      }
    }
    return liste.sort((a, b) => a.x + a.y - (b.x + b.y));
  }, [agents, sitze, bereiche]);

  const arbeiten = agents.filter((a) => raumZustand(a) === "arbeitet").length;

  return (
    <div className="space-y-3">
      <div ref={ref} className="relative w-full select-none overflow-hidden rounded-2xl border border-foreground/[0.08] bg-card" style={{ aspectRatio: `${B} / ${H}` }}>
        <svg viewBox={`0 0 ${B} ${H}`} className="absolute inset-0 h-full w-full" aria-hidden>
          <defs>
            <GlowFilter id={glow} />
          </defs>

          {/* Boden, Wände, Fenster */}
          <polygon points={punkte(p(0, 0), p(breite, 0), p(breite, tiefe), p(0, tiefe))} style={fg(0.04)} />
          <polygon points={punkte(p(0, 0), p(0, tiefe), p(0, tiefe, WAND), p(0, 0, WAND))} style={fg(0.08)} />
          <polygon points={punkte(p(0, 0), p(breite, 0), p(breite, 0, WAND), p(0, 0, WAND))} style={fg(0.11)} />
          {Array.from({ length: Math.floor(breite / 2.4) }).map((_, i) => {
            const x0 = 0.8 + i * 2.4;
            return (
              <polygon key={i} points={punkte(p(x0, 0, 1.0), p(x0 + 1.6, 0, 1.0), p(x0 + 1.6, 0, 2.4), p(x0, 0, 2.4))}
                fill="#7dd3fc" fillOpacity={0.18} stroke="hsl(var(--foreground))" strokeOpacity={0.15} />
            );
          })}
          <line x1={p(0.2, 0, 2.8)[0]} y1={p(0.2, 0, 2.8)[1]} x2={p(breite - 0.2, 0, 2.8)[0]} y2={p(breite - 0.2, 0, 2.8)[1]}
            stroke="hsl(var(--primary))" strokeWidth={5} opacity={0.5} filter={`url(#${glow})`} />
          <line x1={p(0.2, 0, 2.8)[0]} y1={p(0.2, 0, 2.8)[1]} x2={p(breite - 0.2, 0, 2.8)[0]} y2={p(breite - 0.2, 0, 2.8)[1]}
            stroke="hsl(var(--primary))" strokeWidth={2} />

          {/* Bereiche: Teppich in eigener Farbe, Name auf dem Boden */}
          {Array.from({ length: bereiche }).map((_, b) => {
            const [zx, zy] = zoneUrsprung(b);
            const farbe = bereichFarbe(b);
            const [tx, ty] = p(zx + 0.25, zy + 0.45, 0.01);
            return (
              <g key={b}>
                <polygon points={punkte(p(zx, zy), p(zx + ZONE_B, zy), p(zx + ZONE_B, zy + ZONE_T), p(zx, zy + ZONE_T))}
                  fill={farbe} fillOpacity={0.09} stroke={farbe} strokeOpacity={0.35} />
                <text transform={`matrix(0.866,0.5,-0.866,0.5,${tx},${ty})`} fontSize={15} fontWeight={700} fill={farbe} opacity={0.8}>
                  {bereichName(b)}
                </text>
              </g>
            );
          })}

          <Pflanze p={p} x={0.25} y={tiefe - 0.9} />
          <Pflanze p={p} x={breite - 0.9} y={0.25} />

          {plaetze.map((pl) => (
            <Schreibtisch key={`${pl.bereich}:${pl.platz}`} p={p} x={pl.x} y={pl.y} w={TISCH_B} d={TISCH_T}
              glow={glow} farbe={bereichFarbe(pl.bereich)} arbeitet={raumZustand(pl.agent) === "arbeitet"} besetzt={!!pl.agent}
              platzX={pl.x + TISCH_B / 2} platzY={pl.y + TISCH_T + 0.75} />
          ))}
        </svg>

        <RaumEbene breite={B} hoehe={H} massstab={massstab}>
          {plaetze.filter((pl) => pl.agent).map((pl) => {
            const [ax, ay] = p(pl.x + TISCH_B / 2, pl.y + TISCH_T + 0.75);
            const agent = pl.agent as Agent;
            return (
              <AgentFigur key={agent.id} agent={agent} x={ax} y={ay} mitText={mitText} klein={!mitText}
                hinweis={`${agent.name} — ${agentZustand(agent.state)} · ${bereichName(pl.bereich)}`}
                onClick={() => router.push(`/agents/${agent.id}`)} />
            );
          })}
        </RaumEbene>
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-1 text-xs text-muted-foreground">
        <span>{agents.length} Agenten · {arbeiten} {arbeiten === 1 ? "arbeitet" : "arbeiten"} gerade</span>
        <span className="inline-flex items-center gap-1.5"><span className="h-2 w-2 rounded-full bg-emerald-500" /> arbeitet</span>
        <span className="inline-flex items-center gap-1.5"><span className="h-2 w-2 rounded-full bg-sky-500" /> bereit</span>
        <span className="inline-flex items-center gap-1.5"><span className="h-2 w-2 rounded-full bg-foreground/20" /> gestoppt</span>
        <span className="inline-flex items-center gap-1.5"><span className="h-2 w-2 rounded-full bg-red-500" /> Fehler</span>
        <span>Klick auf einen Agenten öffnet ihn.</span>
      </div>
    </div>
  );
}

"use client";

/**
 * Leitstand eines Projekts: was gerade wer macht (mit Stufen bis zum Merge),
 * was als Nächstes kommt, was auf den Merge wartet, und der Arbeitstakt.
 */

import Link from "next/link";
import { ExternalLink, GitPullRequest, Timer } from "lucide-react";
import type { Agent } from "@/lib/types";
import { AgentAvatar } from "@/components/agents/agent-avatar";
import {
  PRIORITAETEN, PR_ZUSTAND, STUFEN, TAKTE, bis,
  type ProjektBoard, type ProjektStufe,
} from "@/lib/projekte";
import { cn, timeAgo } from "@/lib/utils";

export function StufenLeiste({ stufe, farbe, klein = false }: { stufe: ProjektStufe | null; farbe: string; klein?: boolean }) {
  const aktuell = STUFEN.findIndex((s) => s.key === stufe);
  return (
    <div className="flex items-center gap-0.5" aria-label={`Stufe: ${STUFEN[aktuell]?.label ?? "noch keine"}`}>
      {STUFEN.map((s, i) => (
        <div key={s.key} className="flex min-w-0 flex-1 flex-col items-center gap-0.5">
          <div className="flex w-full items-center">
            <div className={cn("h-px flex-1", i === 0 ? "opacity-0" : i <= aktuell ? "" : "bg-foreground/15")}
              style={i > 0 && i <= aktuell ? { background: farbe } : undefined} />
            <div
              className={cn("shrink-0 rounded-full", klein ? "h-1.5 w-1.5" : "h-2.5 w-2.5", i > aktuell && "bg-foreground/15",
                i === aktuell && "ring-2 ring-offset-1 ring-offset-card")}
              style={i <= aktuell ? { background: farbe, ...(i === aktuell ? { ["--tw-ring-color" as string]: farbe } : {}) } : undefined}
            />
            <div className={cn("h-px flex-1", i === STUFEN.length - 1 ? "opacity-0" : i < aktuell ? "" : "bg-foreground/15")}
              style={i < aktuell ? { background: farbe } : undefined} />
          </div>
          {!klein && (
            <span className={cn("text-[9px]", i === aktuell ? "font-semibold text-foreground" : "text-muted-foreground")}>
              {s.label}
            </span>
          )}
        </div>
      ))}
    </div>
  );
}

export function ProjektLeitstand({
  board,
  agents,
  farbe,
  onZumBoard,
}: {
  board: ProjektBoard;
  agents: Agent[];
  farbe: string;
  onZumBoard?: () => void;
}) {
  const agentById = new Map(agents.map((a) => [a.id, a]));
  const { kennzahlen: k, laufend, spalten, takt } = board;
  const naechste = spalten.bereit[0];
  const merge = spalten.pruefung.filter((x) => x.pr_url && x.pr_zustand !== "gemergt");
  const taktLabel = TAKTE.find((t) => t.minuten === (takt.minuten ?? 0))?.label ?? "Aus";

  return (
    <div className="space-y-4">
      {/* Gerade in Arbeit */}
      <section className="rounded-2xl border border-foreground/[0.08] bg-card p-4">
        <div className="flex items-baseline justify-between gap-2">
          <h2 className="text-sm font-semibold">Gerade in Arbeit</h2>
          <span className="inline-flex items-center gap-1 text-[11px] text-muted-foreground" title="Arbeitstakt des Projekts">
            <Timer className="h-3 w-3" />
            {takt.aktiv ? `Takt ${taktLabel.toLowerCase()} · nächster ${bis(takt.naechster_lauf) ?? "—"}` : "Takt aus"}
          </span>
        </div>
        {laufend.length === 0 ? (
          <p className="mt-2 text-xs text-muted-foreground">
            Niemand arbeitet gerade an einer Karte.{k.bereit ? ` ${k.bereit} ${k.bereit === 1 ? "Karte ist" : "Karten sind"} bereit.` : ""}
          </p>
        ) : (
          <ul className="mt-3 space-y-3">
            {laufend.map((l) => {
              const a = l.agent_id ? agentById.get(l.agent_id) : undefined;
              return (
                <li key={l.task_id} className="space-y-1.5">
                  <div className="flex items-center gap-2.5">
                    <AgentAvatar config={a?.config} size="sm" />
                    <div className="min-w-0 flex-1">
                      <Link href={`/tasks/${l.task_id}`} className="block truncate text-sm font-medium hover:underline">
                        {l.karte_titel ?? l.titel}
                      </Link>
                      <p className="truncate text-[11px] text-muted-foreground">
                        {a?.name ?? "Agent"}{l.teilarbeit ? " · Teilarbeit" : ""}
                        {l.status === "running" ? " · arbeitet" : " · wartet auf Start"}
                        {l.gestartet ? ` · seit ${timeAgo(l.gestartet).replace("vor ", "")}` : ""}
                      </p>
                    </div>
                  </div>
                  {!l.teilarbeit && <StufenLeiste stufe={l.stufe} farbe={farbe} />}
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-1 xl:grid-cols-2">
        {/* Reststapel */}
        <section className="rounded-2xl border border-foreground/[0.08] bg-card p-4">
          <h2 className="text-sm font-semibold">Reststapel</h2>
          <button type="button" onClick={onZumBoard} className="mt-2 flex flex-wrap items-center gap-1.5 text-xs">
            <span className="rounded-full bg-foreground/[0.06] px-2 py-0.5"><b className="tabular-nums">{k.eingang}</b> Eingang</span>
            <span className="text-muted-foreground">›</span>
            <span className="rounded-full px-2 py-0.5" style={{ background: `${farbe}22` }}><b className="tabular-nums">{k.bereit}</b> Bereit</span>
          </button>
          {naechste ? (
            <div className="mt-3 rounded-xl border border-foreground/[0.06] bg-foreground/[0.02] p-2.5">
              <p className="text-[10px] uppercase tracking-wide text-muted-foreground">Als Nächstes</p>
              <p className="mt-0.5 flex items-center gap-1.5 text-sm">
                <span className={cn("rounded px-1 text-[10px] font-semibold", PRIORITAETEN[naechste.prioritaet]?.klasse)}>
                  {PRIORITAETEN[naechste.prioritaet]?.label}
                </span>
                {naechste.extern_nr ? <span className="text-xs text-muted-foreground">#{naechste.extern_nr}</span> : null}
                <span className="truncate">{naechste.titel}</span>
              </p>
            </div>
          ) : (
            <p className="mt-3 text-xs text-muted-foreground">Nichts eingeplant.</p>
          )}
        </section>

        {/* Wartet auf Merge */}
        <section className="rounded-2xl border border-foreground/[0.08] bg-card p-4">
          <h2 className="text-sm font-semibold">Wartet auf Merge</h2>
          {merge.length === 0 ? (
            <p className="mt-2 text-xs text-muted-foreground">Kein offener Pull Request.</p>
          ) : (
            <ul className="mt-2 space-y-1.5">
              {merge.slice(0, 6).map((x) => (
                <li key={x.id}>
                  <a href={x.pr_url ?? "#"} target="_blank" rel="noopener noreferrer"
                    className="flex items-center gap-1.5 rounded-lg bg-foreground/[0.03] px-2 py-1.5 text-xs hover:bg-foreground/[0.06]">
                    <GitPullRequest className="h-3.5 w-3.5 shrink-0" style={{ color: farbe }} />
                    <span className="font-medium">#{x.pr_nr ?? "?"}</span>
                    <span className="min-w-0 flex-1 truncate">{x.titel}</span>
                    <span className={cn("shrink-0 rounded px-1 text-[10px]",
                      x.pr_zustand === "ci_rot" ? "bg-red-500/15 text-red-600 dark:text-red-400"
                        : x.pr_zustand === "ci_gruen" ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400"
                          : "bg-foreground/[0.06] text-muted-foreground")}>
                      {PR_ZUSTAND[x.pr_zustand ?? "offen"] ?? x.pr_zustand}
                    </span>
                    <ExternalLink className="h-3 w-3 shrink-0 text-muted-foreground" />
                  </a>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}

"use client";

/**
 * Board eines Projekts: Eingang → Bereit → In Arbeit → Prüfung → Erledigt.
 * Karten werden per Klick weitergeschoben: einplanen, Priorität setzen,
 * vergeben („Los“), abnehmen oder zurückgeben.
 */

import Link from "next/link";
import { useState } from "react";
import {
  ArrowLeft, ArrowRight, Check, CircleDot, CornerUpLeft, ExternalLink, GitPullRequest, Loader2, Play, Plus,
  Trash2, TriangleAlert,
} from "lucide-react";
import type { Agent } from "@/lib/types";
import {
  PRIORITAETEN, PR_ZUSTAND, SPALTEN, wochentag,
  type ProjektBoard, type ProjektKarte, type ProjektKennzahlen,
} from "@/lib/projekte";
import { AgentAvatar } from "@/components/agents/agent-avatar";
import { Fenster } from "@/components/ui/fenster";
import { useMoney } from "@/hooks/use-money";
import { cn, timeAgo } from "@/lib/utils";
import { StufenLeiste } from "./projekt-leitstand";

/** Erledigte Karten der letzten sieben Tage als kleine Balken. */
export function ProjektVerlauf({ kennzahlen, farbe }: { kennzahlen: ProjektKennzahlen; farbe: string }) {
  const max = Math.max(1, ...kennzahlen.verlauf.map((v) => v.anzahl));
  return (
    <div className="flex h-16 items-end gap-1.5">
      {kennzahlen.verlauf.map((v, i) => (
        <div key={v.tag} className="flex flex-1 flex-col items-center gap-1" title={`${v.anzahl} erledigt`}>
          <div
            className="w-full rounded-sm"
            style={{
              height: `${Math.max(3, (v.anzahl / max) * 44)}px`,
              background: v.anzahl ? farbe : "hsl(var(--foreground) / 0.08)",
              opacity: v.anzahl ? 0.85 : 1,
            }}
          />
          <span className={cn("text-[9px] text-muted-foreground", i === kennzahlen.verlauf.length - 1 && "font-semibold text-foreground")}>
            {i === kennzahlen.verlauf.length - 1 ? "heute" : wochentag(v.tag)}
          </span>
        </div>
      ))}
    </div>
  );
}

export interface BoardAktionen {
  verschieben: (k: ProjektKarte, aenderung: { spalte?: string; prioritaet?: number }) => Promise<void>;
  vergeben: (k: ProjektKarte, agentId: string | null) => Promise<void>;
  abnehmen: (k: ProjektKarte) => Promise<void>;
  zurueckgeben: (k: ProjektKarte, hinweis: string) => Promise<void>;
  anlegen: (titel: string, text: string) => Promise<void>;
}

function Knopf({
  onClick, children, titel, haupt = false, gefahr = false,
}: { onClick: () => Promise<void> | void; children: React.ReactNode; titel?: string; haupt?: boolean; gefahr?: boolean }) {
  const [busy, setBusy] = useState(false);
  return (
    <button
      type="button"
      title={titel}
      disabled={busy}
      onClick={async () => {
        setBusy(true);
        try {
          await onClick();
        } finally {
          setBusy(false);
        }
      }}
      className={cn(
        "inline-flex items-center gap-1 rounded-lg px-2 py-1 text-xs font-medium transition-colors disabled:opacity-50",
        haupt ? "bg-primary text-primary-foreground hover:bg-primary/90"
          : gefahr ? "text-muted-foreground hover:bg-red-500/10 hover:text-red-500"
            : "border border-foreground/[0.1] hover:bg-foreground/[0.04]",
      )}
    >
      {busy ? <Loader2 className="h-3 w-3 animate-spin" /> : null}
      {children}
    </button>
  );
}

function Karte({
  karte, agents, mitglieder, leadId, farbe, aktionen, onZurueck,
}: {
  karte: ProjektKarte;
  agents: Map<string, Agent>;
  mitglieder: string[];
  leadId: string | null;
  farbe: string;
  aktionen: BoardAktionen;
  onZurueck: (k: ProjektKarte) => void;
}) {
  const money = useMoney();
  const agent = karte.agent_id ? agents.get(karte.agent_id) : undefined;
  const prio = PRIORITAETEN[karte.prioritaet] ?? PRIORITAETEN[2];
  const gescheitert = karte.task_status === "failed";
  // Leer = an den Lead (er verteilt); ohne Lead muss man wählen (außer bei einem Mitglied).
  const [ziel, setZiel] = useState<string>(!leadId && mitglieder.length === 1 ? mitglieder[0] : "");
  const wann = karte.erledigt ?? karte.aktualisiert ?? karte.erstellt;

  return (
    <div className={cn("rounded-xl border bg-card p-3 text-sm shadow-sm", gescheitert ? "border-red-500/30" : "border-foreground/[0.08]")}>
      <div className="flex items-start gap-1.5">
        <button
          type="button"
          title="Priorität ändern"
          onClick={() => aktionen.verschieben(karte, { prioritaet: (karte.prioritaet % 3) + 1 })}
          disabled={karte.spalte === "erledigt"}
          className={cn("mt-0.5 shrink-0 rounded px-1 text-[10px] font-semibold", prio.klasse)}
        >
          {prio.label}
        </button>
        <div className="min-w-0 flex-1">
          {karte.task_id ? (
            <Link href={`/tasks/${karte.task_id}`} className="line-clamp-2 font-medium hover:underline">{karte.titel}</Link>
          ) : (
            <p className="line-clamp-2 font-medium">{karte.titel}</p>
          )}
          <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-muted-foreground">
            {karte.extern_nr && karte.url ? (
              <a href={karte.url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-0.5 hover:text-foreground">
                {karte.quelle === "pr" ? <GitPullRequest className="h-3 w-3" /> : <CircleDot className="h-3 w-3" />}#{karte.extern_nr}
              </a>
            ) : null}
            {agent && (
              <span className="inline-flex items-center gap-1">
                <AgentAvatar config={agent.config} size="sm" className="h-4 w-4 rounded" />
                {agent.name}
              </span>
            )}
            {wann && <span>{timeAgo(wann)}</span>}
            {karte.kosten_usd ? <span title={money.title(karte.kosten_usd)}>{money.fmt(karte.kosten_usd)}</span> : null}
          </div>
        </div>
      </div>

      {karte.notiz && <p className="mt-1.5 rounded-md bg-amber-500/10 px-2 py-1 text-[11px] text-amber-700 dark:text-amber-300">{karte.notiz}</p>}

      {karte.spalte === "in_arbeit" && (
        <div className="mt-2"><StufenLeiste stufe={karte.stufe} farbe={farbe} klein /></div>
      )}

      {karte.pr_url && (
        <a href={karte.pr_url} target="_blank" rel="noopener noreferrer"
          className="mt-2 inline-flex items-center gap-1 rounded-md bg-foreground/[0.04] px-1.5 py-0.5 text-[11px] hover:bg-foreground/[0.08]">
          <GitPullRequest className="h-3 w-3" /> PR #{karte.pr_nr ?? "?"} · {PR_ZUSTAND[karte.pr_zustand ?? "offen"] ?? karte.pr_zustand}
          <ExternalLink className="h-2.5 w-2.5" />
        </a>
      )}

      {karte.spalte === "pruefung" && (karte.fehler || karte.ergebnis) && (
        <p className={cn("mt-2 line-clamp-4 whitespace-pre-line text-xs", gescheitert ? "text-red-500" : "text-muted-foreground")}>
          {gescheitert && <TriangleAlert className="mr-1 inline h-3 w-3" />}
          {karte.fehler ?? karte.ergebnis}
        </p>
      )}

      {/* Aktionen je Spalte */}
      <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
        {karte.spalte === "eingang" && (
          <>
            <Knopf haupt onClick={() => aktionen.verschieben(karte, { spalte: "bereit" })}>
              Einplanen <ArrowRight className="h-3 w-3" />
            </Knopf>
            <Knopf gefahr titel="Verwerfen (nach Erledigt)" onClick={() => aktionen.verschieben(karte, { spalte: "erledigt" })}>
              <Trash2 className="h-3 w-3" />
            </Knopf>
          </>
        )}
        {karte.spalte === "bereit" && (
          <>
            <select
              value={ziel}
              onChange={(e) => setZiel(e.target.value)}
              aria-label="Wer bekommt die Karte?"
              className="min-w-0 max-w-[9rem] rounded-lg border border-foreground/[0.1] bg-background/80 px-1.5 py-1 text-xs"
            >
              <option value="">{leadId ? "Lead verteilt" : "Agent wählen …"}</option>
              {mitglieder.map((m) => (
                <option key={m} value={m}>{leadId ? "Direkt: " : ""}{agents.get(m)?.name ?? m}</option>
              ))}
            </select>
            <Knopf
              haupt
              titel="Karte vergeben — der Agent legt los"
              onClick={() => {
                if (!ziel && !leadId) return;
                return aktionen.vergeben(karte, ziel || null);
              }}
            >
              <Play className="h-3 w-3" /> Los
            </Knopf>
            <Knopf titel="Zurück in den Eingang" onClick={() => aktionen.verschieben(karte, { spalte: "eingang" })}>
              <ArrowLeft className="h-3 w-3" />
            </Knopf>
          </>
        )}
        {karte.spalte === "pruefung" && (
          <>
            <Knopf haupt onClick={() => aktionen.abnehmen(karte)}>
              <Check className="h-3 w-3" /> Abnehmen
            </Knopf>
            <Knopf onClick={() => onZurueck(karte)}>
              <CornerUpLeft className="h-3 w-3" /> Zurückgeben
            </Knopf>
          </>
        )}
      </div>
    </div>
  );
}

export function ProjektBoardAnsicht({
  board, agents, mitglieder, leadId, farbe, aktionen,
}: {
  board: ProjektBoard;
  agents: Agent[];
  mitglieder: string[];
  leadId: string | null;
  farbe: string;
  aktionen: BoardAktionen;
}) {
  const [zurueck, setZurueck] = useState<ProjektKarte | null>(null);
  const [hinweis, setHinweis] = useState("");
  const [sendet, setSendet] = useState(false);
  const [neu, setNeu] = useState(false);
  const [titel, setTitel] = useState("");
  const [text, setText] = useState("");
  const agentById = new Map(agents.map((a) => [a.id, a]));

  return (
    <>
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
        {SPALTEN.map((sp) => {
          const karten = board.spalten[sp.key] ?? [];
          return (
            <section key={sp.key} className="flex min-w-0 flex-col rounded-2xl border border-foreground/[0.06] bg-foreground/[0.02] p-2.5">
              <h3 className="mb-2 flex items-center justify-between px-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                {sp.label}
                <span className="flex items-center gap-1">
                  <span className={cn("rounded-full px-2 py-0.5 text-[11px] normal-case",
                    karten.length && sp.key === "pruefung" ? "bg-orange-500/15 text-orange-600 dark:text-orange-400" : "bg-foreground/[0.06]")}>
                    {karten.length}
                  </span>
                  {sp.key === "eingang" && (
                    <button type="button" onClick={() => setNeu(true)} title="Neue Karte" aria-label="Neue Karte"
                      className="rounded-md p-0.5 hover:bg-foreground/[0.08] hover:text-foreground">
                      <Plus className="h-3.5 w-3.5" />
                    </button>
                  )}
                </span>
              </h3>
              <div className="flex max-h-[34rem] flex-col gap-2 overflow-y-auto">
                {karten.length === 0 ? (
                  <p className="px-1 py-3 text-xs text-muted-foreground/70">{sp.leer}</p>
                ) : (
                  karten.map((k) => (
                    <Karte key={k.id} karte={k} agents={agentById} mitglieder={mitglieder} leadId={leadId} farbe={farbe}
                      aktionen={aktionen} onZurueck={(karte) => { setHinweis(""); setZurueck(karte); }} />
                  ))
                )}
              </div>
            </section>
          );
        })}
      </div>

      <Fenster offen={neu} schliessen={() => setNeu(false)} titel="Neue Karte im Eingang" breite="max-w-lg">
        <div className="space-y-3 p-5">
          <input value={titel} onChange={(e) => setTitel(e.target.value)} autoFocus placeholder="Titel"
            className="w-full rounded-lg border border-foreground/[0.1] bg-background/80 px-3 py-2 text-sm outline-none focus:border-primary/50" />
          <textarea value={text} onChange={(e) => setText(e.target.value)} rows={5}
            placeholder="Was genau ist zu tun? Woran merkt man, dass es fertig ist?"
            className="w-full resize-none rounded-lg border border-foreground/[0.1] bg-background/80 px-3 py-2 text-sm outline-none focus:border-primary/50" />
          <div className="flex justify-end gap-2">
            <button type="button" onClick={() => setNeu(false)} className="rounded-lg px-3 py-2 text-sm text-muted-foreground hover:bg-foreground/[0.04]">Abbrechen</button>
            <Knopf haupt onClick={async () => {
              if (!titel.trim()) return;
              await aktionen.anlegen(titel.trim(), text.trim());
              setTitel(""); setText(""); setNeu(false);
            }}>
              <Plus className="h-3 w-3" /> Anlegen
            </Knopf>
          </div>
        </div>
      </Fenster>

      <Fenster offen={!!zurueck} schliessen={() => setZurueck(null)} titel="Zur Nacharbeit zurückgeben" breite="max-w-lg">
        <div className="space-y-3 p-5">
          <p className="text-sm text-muted-foreground">
            „{zurueck?.titel}“ geht mit deinem Hinweis zurück an den Agenten. Danach steht die Karte wieder in der Prüfung.
          </p>
          <textarea value={hinweis} onChange={(e) => setHinweis(e.target.value)} rows={4} autoFocus
            placeholder="Was soll anders werden?"
            className="w-full resize-none rounded-lg border border-foreground/[0.1] bg-background/80 px-3 py-2 text-sm outline-none focus:border-primary/50" />
          <div className="flex justify-end gap-2">
            <button type="button" onClick={() => setZurueck(null)} className="rounded-lg px-3 py-2 text-sm text-muted-foreground hover:bg-foreground/[0.04]">Abbrechen</button>
            <button
              type="button"
              disabled={!hinweis.trim() || sendet}
              onClick={async () => {
                if (!zurueck) return;
                setSendet(true);
                try {
                  await aktionen.zurueckgeben(zurueck, hinweis.trim());
                  setZurueck(null);
                } finally {
                  setSendet(false);
                }
              }}
              className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
            >
              {sendet ? <Loader2 className="h-4 w-4 animate-spin" /> : <CornerUpLeft className="h-4 w-4" />}
              Zurückgeben
            </button>
          </div>
        </div>
      </Fenster>
    </>
  );
}

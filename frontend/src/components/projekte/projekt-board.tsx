"use client";

import Link from "next/link";
import { useState } from "react";
import { Check, CornerUpLeft, Loader2, TriangleAlert } from "lucide-react";
import type { Agent } from "@/lib/types";
import { SPALTEN, wochentag, type ProjektBoard, type ProjektKarte, type ProjektKennzahlen } from "@/lib/projekte";
import { AgentAvatar } from "@/components/agents/agent-avatar";
import { Fenster } from "@/components/ui/fenster";
import { useMoney } from "@/hooks/use-money";
import { cn, timeAgo } from "@/lib/utils";

/** Fertige Aufträge der letzten sieben Tage als kleine Balken. */
export function ProjektVerlauf({ kennzahlen, farbe }: { kennzahlen: ProjektKennzahlen; farbe: string }) {
  const max = Math.max(1, ...kennzahlen.verlauf.map((v) => v.anzahl));
  return (
    <div className="flex h-16 items-end gap-1.5">
      {kennzahlen.verlauf.map((v, i) => (
        <div key={v.tag} className="flex flex-1 flex-col items-center gap-1" title={`${v.anzahl} fertig`}>
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

function Karte({
  karte,
  agent,
  onAbnehmen,
  onZurueckgeben,
}: {
  karte: ProjektKarte;
  agent?: Agent;
  onAbnehmen?: (k: ProjektKarte) => Promise<void>;
  onZurueckgeben?: (k: ProjektKarte) => void;
}) {
  const money = useMoney();
  const [busy, setBusy] = useState(false);
  const gescheitert = karte.status === "failed";
  const wann = karte.abgenommen ?? karte.fertig ?? karte.gestartet ?? karte.erstellt;
  return (
    <div
      className={cn(
        "rounded-xl border bg-card p-3 text-sm shadow-sm",
        gescheitert ? "border-red-500/30" : "border-foreground/[0.08]",
      )}
    >
      <Link href={`/tasks/${karte.id}`} className="line-clamp-2 font-medium hover:underline">
        {karte.titel}
      </Link>
      <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-muted-foreground">
        {agent && (
          <span className="inline-flex items-center gap-1">
            <AgentAvatar config={agent.config} size="sm" className="h-4 w-4 rounded" />
            {agent.name}
          </span>
        )}
        {wann && <span>{timeAgo(wann)}</span>}
        {karte.teilauftrag && <span className="rounded bg-foreground/[0.06] px-1">Teilarbeit</span>}
        {karte.zurueckgegeben_als && <span className="rounded bg-foreground/[0.06] px-1">Zurückgegeben</span>}
        {karte.status === "cancelled" && <span className="rounded bg-foreground/[0.06] px-1">Abgebrochen</span>}
        {karte.kosten_usd ? <span title={money.title(karte.kosten_usd)}>{money.fmt(karte.kosten_usd)}</span> : null}
      </div>
      {karte.spalte === "zur_abnahme" && (
        <>
          {(karte.fehler || karte.ergebnis) && (
            <p className={cn("mt-2 line-clamp-4 whitespace-pre-line text-xs", gescheitert ? "text-red-500" : "text-muted-foreground")}>
              {gescheitert && <TriangleAlert className="mr-1 inline h-3 w-3" />}
              {karte.fehler ?? karte.ergebnis}
            </p>
          )}
          <div className="mt-2.5 flex gap-2">
            <button
              type="button"
              disabled={busy}
              onClick={async () => {
                setBusy(true);
                try {
                  await onAbnehmen?.(karte);
                } finally {
                  setBusy(false);
                }
              }}
              className="inline-flex items-center gap-1 rounded-lg bg-primary px-2.5 py-1 text-xs font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
            >
              {busy ? <Loader2 className="h-3 w-3 animate-spin" /> : <Check className="h-3 w-3" />}
              Abnehmen
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => onZurueckgeben?.(karte)}
              className="inline-flex items-center gap-1 rounded-lg border border-foreground/[0.1] px-2.5 py-1 text-xs font-medium hover:bg-foreground/[0.04] disabled:opacity-50"
            >
              <CornerUpLeft className="h-3 w-3" />
              Zurückgeben
            </button>
          </div>
        </>
      )}
    </div>
  );
}

export function ProjektBoardAnsicht({
  board,
  agents,
  onAbnehmen,
  onZurueckgeben,
}: {
  board: ProjektBoard;
  agents: Agent[];
  onAbnehmen: (k: ProjektKarte) => Promise<void>;
  onZurueckgeben: (k: ProjektKarte, hinweis: string) => Promise<void>;
}) {
  const [zurueck, setZurueck] = useState<ProjektKarte | null>(null);
  const [hinweis, setHinweis] = useState("");
  const [sendet, setSendet] = useState(false);
  const agentById = new Map(agents.map((a) => [a.id, a]));

  return (
    <>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {SPALTEN.map((sp) => {
          const karten = board.spalten[sp.key] ?? [];
          return (
            <section key={sp.key} className="flex min-w-0 flex-col rounded-2xl border border-foreground/[0.06] bg-foreground/[0.02] p-3">
              <h3 className="mb-2.5 flex items-center justify-between px-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                {sp.label}
                <span className={cn("rounded-full px-2 py-0.5 text-[11px] normal-case", karten.length && sp.key === "zur_abnahme" ? "bg-orange-500/15 text-orange-600 dark:text-orange-400" : "bg-foreground/[0.06]")}>
                  {karten.length}
                </span>
              </h3>
              <div className="flex max-h-[32rem] flex-col gap-2 overflow-y-auto">
                {karten.length === 0 ? (
                  <p className="px-1 py-3 text-xs text-muted-foreground/70">{sp.leer}</p>
                ) : (
                  karten.map((k) => (
                    <Karte
                      key={k.id}
                      karte={k}
                      agent={k.agent_id ? agentById.get(k.agent_id) : undefined}
                      onAbnehmen={onAbnehmen}
                      onZurueckgeben={(karte) => {
                        setHinweis("");
                        setZurueck(karte);
                      }}
                    />
                  ))
                )}
              </div>
            </section>
          );
        })}
      </div>

      <Fenster offen={!!zurueck} schliessen={() => setZurueck(null)} titel="Zur Nacharbeit zurückgeben" breite="max-w-lg">
        <div className="space-y-3 p-5">
          <p className="text-sm text-muted-foreground">
            „{zurueck?.titel}“ geht mit deinem Hinweis zurück an den Agenten. Das Ergebnis der Nacharbeit
            landet wieder unter „Zur Abnahme“.
          </p>
          <textarea
            value={hinweis}
            onChange={(e) => setHinweis(e.target.value)}
            rows={4}
            autoFocus
            placeholder="Was soll anders werden?"
            className="w-full resize-none rounded-lg border border-foreground/[0.1] bg-background/80 px-3 py-2 text-sm outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20"
          />
          <div className="flex justify-end gap-2">
            <button type="button" onClick={() => setZurueck(null)} className="rounded-lg px-3 py-2 text-sm text-muted-foreground hover:bg-foreground/[0.04]">
              Abbrechen
            </button>
            <button
              type="button"
              disabled={!hinweis.trim() || sendet}
              onClick={async () => {
                if (!zurueck) return;
                setSendet(true);
                try {
                  await onZurueckgeben(zurueck, hinweis.trim());
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

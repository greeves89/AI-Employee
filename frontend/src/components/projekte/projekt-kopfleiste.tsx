"use client";

/**
 * Kopfleiste eines Projekts: der Fluss Eingang › Bereit › In Arbeit › Prüfung ›
 * Erledigt als Zahlen, dazu Kosten, Takt und Repo-Stand — eine Zeile statt vier
 * Kästen. Ein Klick auf eine Zahl führt zum Board.
 */

import Link from "next/link";
import { ChevronRight, GitBranch, RefreshCw, Timer, TriangleAlert, Wallet } from "lucide-react";
import { useMoney } from "@/hooks/use-money";
import { TAKTE, bis, type ProjektBoard } from "@/lib/projekte";
import { cn, timeAgo } from "@/lib/utils";

export function ProjektKopfleiste({
  board, projektId, budgetUsd, farbe, repo, repoAbgleichAt, repoFehler, gleichtAb, onAbgleichen, onBoard,
}: {
  board: ProjektBoard;
  projektId: string;
  budgetUsd: number | null | undefined;
  farbe: string;
  repo: string | null | undefined;
  repoAbgleichAt: string | null | undefined;
  repoFehler: string | null | undefined;
  gleichtAb: boolean;
  onAbgleichen?: () => void;
  onBoard: () => void;
}) {
  const money = useMoney();
  const { kennzahlen: k, takt } = board;
  const kosten = k.kosten_monat_usd ?? 0;
  const ueberBudget = !!budgetUsd && kosten >= budgetUsd;
  const taktLabel = TAKTE.find((t) => t.minuten === (takt.minuten ?? 0))?.label ?? "Aus";
  const fluss = [
    { label: "Eingang", wert: k.eingang },
    { label: "Bereit", wert: k.bereit },
    { label: "In Arbeit", wert: k.in_arbeit, aktiv: k.in_arbeit > 0 },
    { label: "Prüfung", wert: k.pruefung, achtung: k.pruefung > 0 },
    { label: "Erledigt · 7 Tage", wert: k.fertig_7_tage },
  ];

  return (
    <div className="rounded-2xl border border-foreground/[0.08] bg-card">
      <div className="flex flex-col gap-3 p-3 lg:flex-row lg:items-center lg:gap-0">
        <div className="grid min-w-0 flex-1 grid-cols-5 items-center gap-1">
          {fluss.map((f, i) => (
            <button key={f.label} type="button" onClick={onBoard}
              className="group relative flex min-w-0 flex-col items-center rounded-xl px-1 py-1.5 hover:bg-foreground/[0.04]">
              <span className={cn("text-xl font-semibold tabular-nums leading-none sm:text-2xl",
                f.achtung && "text-orange-600 dark:text-orange-400")}
                style={f.aktiv ? { color: farbe } : undefined}>
                {f.wert}
              </span>
              <span className="mt-1 max-w-full truncate text-[10px] text-muted-foreground sm:text-[11px]">{f.label}</span>
              {i < fluss.length - 1 && (
                <ChevronRight className="absolute -right-2 top-1/2 hidden h-3.5 w-3.5 -translate-y-1/2 text-foreground/20 sm:block" />
              )}
            </button>
          ))}
        </div>

        <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 border-t border-foreground/[0.06] pt-2.5 text-xs text-muted-foreground lg:ml-3 lg:border-l lg:border-t-0 lg:pl-4 lg:pt-0">
          <Link href={`/finanzen?projekt=${projektId}`} title={money.title(kosten)}
            className="inline-flex items-center gap-1.5 hover:text-foreground">
            <Wallet className="h-3.5 w-3.5" />
            <span className={cn("tabular-nums text-foreground", ueberBudget && "font-semibold text-red-500")}>
              {money.fmt(kosten)}{budgetUsd ? ` / ${money.fmt(budgetUsd)}` : ""}
            </span>
            <span>im Monat</span>
          </Link>
          <span className="inline-flex items-center gap-1.5" title="Arbeitstakt des Projekts">
            <Timer className="h-3.5 w-3.5" />
            {takt.aktiv ? `${taktLabel} · nächster ${bis(takt.naechster_lauf) ?? "—"}` : "Takt aus"}
          </span>
          {repo && (
            <span className={cn("inline-flex min-w-0 items-center gap-1.5", repoFehler && "text-red-600 dark:text-red-400")}
              title={repoFehler ?? "Issues und Pull Requests kommen alle 10 Minuten von selbst aufs Board."}>
              {repoFehler ? <TriangleAlert className="h-3.5 w-3.5 shrink-0" /> : <GitBranch className="h-3.5 w-3.5 shrink-0" />}
              <a href={`https://github.com/${repo}`} target="_blank" rel="noopener noreferrer"
                className="max-w-[12rem] truncate text-foreground hover:underline">{repo}</a>
              <span className="whitespace-nowrap">{repoAbgleichAt ? timeAgo(repoAbgleichAt) : "noch nie"}</span>
              {onAbgleichen && (
                <button type="button" onClick={onAbgleichen} disabled={gleichtAb} title="Jetzt abgleichen" aria-label="Jetzt abgleichen"
                  className="rounded-md p-1 hover:bg-foreground/[0.06] hover:text-foreground disabled:opacity-50">
                  <RefreshCw className={cn("h-3.5 w-3.5", gleichtAb && "animate-spin")} />
                </button>
              )}
            </span>
          )}
        </div>
      </div>
      {repoFehler && (
        <p className="border-t border-red-500/20 bg-red-500/5 px-3 py-1.5 text-xs text-red-600 dark:text-red-400">{repoFehler}</p>
      )}
    </div>
  );
}

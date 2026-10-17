"use client";

/**
 * Finanzen: was die Agenten kosten — je Monat, je Projekt, je Agent (orchestrator: core/finanzen).
 * Beträge kommen in USD und werden nur für die Anzeige umgerechnet (useMoney).
 * Projektbudgets setzt, wer das Projekt angelegt hat; ist eins aufgebraucht, starten
 * keine neuen Läufe des Projekts mehr (core/budget.projekt_budget_pruefen).
 */

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { Bar, CartesianGrid, ComposedChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Download, FolderKanban, Loader2, Pencil, Wallet } from "lucide-react";
import { Header } from "@/components/layout/header";
import { useToast } from "@/components/ui/dialog-provider";
import { useMoney } from "@/hooks/use-money";
import * as api from "@/lib/api";
import { apiFehlertext } from "@/lib/api-fehler";
import { projektFarbe } from "@/lib/projekte";
import { cn } from "@/lib/utils";

const MONATSNAMEN = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"];

function monatKurz(schluessel: string): string {
  const [jahr, monat] = schluessel.split("-");
  return `${MONATSNAMEN[Number(monat) - 1] ?? monat} ${jahr.slice(2)}`;
}

function tokensKurz(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1).replace(".", ",")} Mio.`;
  if (n >= 1_000) return `${Math.round(n / 1_000)} Tsd.`;
  return String(n);
}

function Kachel({ label, wert, titel, sub }: { label: string; wert: string; titel?: string; sub?: React.ReactNode }) {
  return (
    <div className="rounded-2xl border border-foreground/[0.08] bg-card p-4">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums" title={titel}>{wert}</p>
      {sub && <p className="mt-1 text-[11px] text-muted-foreground">{sub}</p>}
    </div>
  );
}

function BudgetBalken({ anteil }: { anteil: number }) {
  const prozent = Math.min(100, Math.round(anteil * 100));
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-foreground/[0.08]">
      <div className={cn("h-full rounded-full", anteil >= 1 ? "bg-red-500" : anteil >= 0.8 ? "bg-amber-500" : "bg-emerald-500")}
        style={{ width: `${prozent}%` }} />
    </div>
  );
}

function ProjektBudget({ projekt, onGespeichert }: { projekt: api.FinanzenProjekt; onGespeichert: () => void }) {
  const money = useMoney();
  const toast = useToast();
  const [offen, setOffen] = useState(false);
  const [wert, setWert] = useState("");
  const [speichert, setSpeichert] = useState(false);

  const speichern = async () => {
    const usd = money.fromInput(wert);
    if (usd === undefined) {
      toast.error("Ungültiger Betrag", "Bitte eine Zahl eintragen — oder leer lassen für kein Budget.");
      return;
    }
    setSpeichert(true);
    try {
      await api.setProjektBudget(projekt.id, usd);
      setOffen(false);
      onGespeichert();
    } catch (e) {
      toast.error("Budget nicht gespeichert", apiFehlertext(e));
    } finally {
      setSpeichert(false);
    }
  };

  if (offen) {
    return (
      <div className="flex items-center gap-1.5">
        <input value={wert} onChange={(e) => setWert(e.target.value)} autoFocus inputMode="decimal"
          onKeyDown={(e) => { if (e.key === "Enter") speichern(); if (e.key === "Escape") setOffen(false); }}
          placeholder={`kein Budget (${money.symbol})`} aria-label="Monatsbudget"
          className="w-28 rounded-lg border border-foreground/[0.1] bg-background/80 px-2 py-1 text-xs" />
        <button type="button" onClick={speichern} disabled={speichert}
          className="rounded-lg bg-primary px-2 py-1 text-xs font-medium text-primary-foreground disabled:opacity-50">
          {speichert ? <Loader2 className="h-3 w-3 animate-spin" /> : "Speichern"}
        </button>
      </div>
    );
  }
  return (
    <div className="min-w-[8rem] space-y-1">
      <div className="flex items-center justify-between gap-2 text-xs">
        <span className="tabular-nums">{projekt.budget_usd ? money.fmt(projekt.budget_usd) : "—"}</span>
        {projekt.darf_budget && (
          <button type="button" onClick={() => { setWert(money.toInput(projekt.budget_usd)); setOffen(true); }}
            aria-label={`Budget für ${projekt.name} ändern`} title="Monatsbudget ändern"
            className="rounded p-0.5 text-muted-foreground hover:bg-foreground/[0.06] hover:text-foreground">
            <Pencil className="h-3 w-3" />
          </button>
        )}
      </div>
      {projekt.anteil_budget !== null && <BudgetBalken anteil={projekt.anteil_budget} />}
    </div>
  );
}

function FinanzenSeite() {
  const money = useMoney();
  const toast = useToast();
  const toastRef = useRef(toast);
  toastRef.current = toast;
  const hervor = useSearchParams().get("projekt");
  const [monate, setMonate] = useState(6);
  const [daten, setDaten] = useState<api.Finanzen | null>(null);
  const [laedt, setLaedt] = useState(false);
  const zeile = useRef<HTMLTableRowElement>(null);

  const laden = useCallback(async () => {
    try {
      setDaten(await api.getFinanzen(monate));
    } catch (e) {
      toastRef.current.error("Finanzen nicht geladen", apiFehlertext(e));
    }
  }, [monate]);

  useEffect(() => { laden(); }, [laden]);
  useEffect(() => {
    if (daten && hervor) zeile.current?.scrollIntoView({ block: "center" });
  }, [daten, hervor]);

  const exportieren = async () => {
    setLaedt(true);
    try {
      await api.exportFinanzen(Math.max(monate, 12));
    } catch (e) {
      toast.error("Export fehlgeschlagen", apiFehlertext(e));
    } finally {
      setLaedt(false);
    }
  };

  const s = daten?.summe;
  const diagramm = (daten?.verlauf ?? []).map((m) => ({
    monat: monatKurz(m.monat), Aufgaben: money.value(m.aufgaben_usd), Chat: money.value(m.chat_usd),
  }));

  return (
    <div>
      <Header
        title="Finanzen"
        subtitle="Was deine Agenten kosten — je Monat, Projekt und Agent"
        actions={
          <div className="flex items-center gap-2">
            <select value={monate} onChange={(e) => setMonate(Number(e.target.value))} aria-label="Zeitraum"
              className="rounded-lg border border-foreground/[0.1] bg-background/80 px-2 py-2 text-sm">
              <option value={3}>3 Monate</option>
              <option value={6}>6 Monate</option>
              <option value={12}>12 Monate</option>
            </select>
            <button type="button" onClick={exportieren} disabled={laedt}
              className="inline-flex items-center gap-1.5 rounded-lg border border-foreground/[0.1] px-3 py-2 text-sm hover:bg-foreground/[0.04] disabled:opacity-50">
              {laedt ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />} CSV
            </button>
          </div>
        }
      />
      <div className="space-y-6 p-4 sm:p-6">
        {!daten || !s ? (
          <div className="flex justify-center py-16 text-muted-foreground"><Loader2 className="h-5 w-5 animate-spin" /></div>
        ) : (
          <>
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              <Kachel label="Dieser Monat" wert={money.fmt(s.monat_usd)} titel={money.title(s.monat_usd)}
                sub={<>Vormonat {money.fmt(s.vormonat_usd)}</>} />
              <Kachel label="Aufgaben und Projekte" wert={money.fmt(s.aufgaben_usd)} titel={money.title(s.aufgaben_usd)}
                sub={<>
                  davon ohne Projekt {money.fmt(s.ohne_projekt_aufgaben_usd)}
                  {(s.andere_projekte_aufgaben_usd ?? 0) > 0 && <> · in gelöschten oder fremden Projekten {money.fmt(s.andere_projekte_aufgaben_usd ?? 0)}</>}
                </>} />
              <Kachel label="Chat" wert={money.fmt(s.chat_usd)} titel={money.title(s.chat_usd)}
                sub="Gespräche mit den Agenten, keinem Projekt zugeordnet" />
              {daten.budget ? (
                <div className="rounded-2xl border border-foreground/[0.08] bg-card p-4">
                  <p className="text-xs text-muted-foreground">Dein Monatsbudget</p>
                  <p className="mt-1 text-2xl font-semibold tabular-nums">
                    {money.fmt(daten.budget.verbraucht_usd)}
                    <span className="text-sm font-normal text-muted-foreground"> von {money.fmt(daten.budget.budget_usd)}</span>
                  </p>
                  <div className="mt-2"><BudgetBalken anteil={daten.budget.verbraucht_usd / daten.budget.budget_usd} /></div>
                </div>
              ) : (
                <Kachel label="Gelöschte Agenten und Verläufe" wert={money.fmt(s.geloescht_usd)} titel={money.title(s.geloescht_usd)}
                  sub="bleiben in der Summe, auch wenn es sie nicht mehr gibt" />
              )}
            </div>

            <section className="rounded-2xl border border-foreground/[0.08] bg-card p-4">
              <h3 className="text-sm font-semibold">Verlauf je Monat</h3>
              <div className="mt-3 h-56">
                <ResponsiveContainer width="100%" height="100%">
                  <ComposedChart data={diagramm} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="currentColor" strokeOpacity={0.08} />
                    <XAxis dataKey="monat" tick={{ fontSize: 11 }} stroke="currentColor" strokeOpacity={0.4} />
                    <YAxis tick={{ fontSize: 11 }} stroke="currentColor" strokeOpacity={0.4} width={48}
                      tickFormatter={(v: number) => `${v.toFixed(0)} ${money.symbol}`} />
                    <Tooltip formatter={(v) => `${Number(v).toFixed(2).replace(".", ",")} ${money.symbol}`}
                      contentStyle={{ fontSize: 12, borderRadius: 8 }} />
                    <Bar dataKey="Aufgaben" stackId="k" fill="#6366f1" radius={[0, 0, 0, 0]} />
                    <Bar dataKey="Chat" stackId="k" fill="#22c55e" radius={[4, 4, 0, 0]} />
                  </ComposedChart>
                </ResponsiveContainer>
              </div>
            </section>

            <section className="rounded-2xl border border-foreground/[0.08] bg-card p-4">
              <div className="flex items-baseline justify-between gap-2">
                <h3 className="text-sm font-semibold">Projekte</h3>
                <span className="text-[11px] text-muted-foreground">Läufe des Projekts: Karten, Takt, Gespräch</span>
              </div>
              {daten.je_projekt.length === 0 ? (
                <p className="mt-3 text-xs text-muted-foreground">
                  Noch kein Projekt. <Link href="/projekte" className="underline">Projekt anlegen</Link>
                </p>
              ) : (
                <div className="mt-3 overflow-x-auto">
                  <table className="w-full min-w-[40rem] text-sm">
                    <thead className="text-left text-[11px] text-muted-foreground">
                      <tr>
                        <th className="pb-2 font-medium">Projekt</th>
                        <th className="pb-2 text-right font-medium">Dieser Monat</th>
                        <th className="pb-2 text-right font-medium">Vormonat</th>
                        <th className="pb-2 text-right font-medium">Insgesamt</th>
                        <th className="pb-2 text-right font-medium">Tokens</th>
                        <th className="pb-2 pl-4 font-medium">Monatsbudget</th>
                      </tr>
                    </thead>
                    <tbody>
                      {daten.je_projekt.map((p) => (
                        <tr key={p.id} ref={p.id === hervor ? zeile : undefined}
                          className={cn("border-t border-foreground/[0.06]", p.id === hervor && "bg-primary/[0.06]")}>
                          <td className="py-2">
                            <Link href={`/projekte/${p.id}`} className="inline-flex items-center gap-2 hover:underline">
                              <span className="h-2.5 w-2.5 rounded-full" style={{ background: projektFarbe(p.farbe) }} />
                              {p.name}
                            </Link>
                          </td>
                          <td className="py-2 text-right tabular-nums" title={money.title(p.monat_usd)}>{money.fmt(p.monat_usd)}</td>
                          <td className="py-2 text-right tabular-nums text-muted-foreground">{money.fmt(p.vormonat_usd)}</td>
                          <td className="py-2 text-right tabular-nums text-muted-foreground">{money.fmt(p.gesamt_usd)}</td>
                          <td className="py-2 text-right tabular-nums text-muted-foreground"
                            title={`${p.tokens_ein} ein / ${p.tokens_aus} aus`}>{tokensKurz(p.tokens_ein + p.tokens_aus)}</td>
                          <td className="py-2 pl-4"><ProjektBudget projekt={p} onGespeichert={laden} /></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>

            <section className="rounded-2xl border border-foreground/[0.08] bg-card p-4">
              <h3 className="text-sm font-semibold">Agenten</h3>
              {daten.je_agent.length === 0 ? (
                <p className="mt-3 text-xs text-muted-foreground">In diesem Monat noch keine Kosten.</p>
              ) : (
                <div className="mt-3 overflow-x-auto">
                  <table className="w-full min-w-[36rem] text-sm">
                    <thead className="text-left text-[11px] text-muted-foreground">
                      <tr>
                        <th className="pb-2 font-medium">Agent</th>
                        <th className="pb-2 text-right font-medium">Aufgaben</th>
                        <th className="pb-2 text-right font-medium">Chat</th>
                        <th className="pb-2 text-right font-medium">Dieser Monat</th>
                        <th className="pb-2 text-right font-medium">Vormonat</th>
                        <th className="pb-2 text-right font-medium">Tokens</th>
                      </tr>
                    </thead>
                    <tbody>
                      {daten.je_agent.map((a) => (
                        <tr key={a.id} className="border-t border-foreground/[0.06]">
                          <td className="py-2"><Link href={`/agents/${a.id}`} className="hover:underline">{a.name}</Link></td>
                          <td className="py-2 text-right tabular-nums text-muted-foreground">{money.fmt(a.aufgaben_usd)}</td>
                          <td className="py-2 text-right tabular-nums text-muted-foreground">{money.fmt(a.chat_usd)}</td>
                          <td className="py-2 text-right tabular-nums"
                            title={a.preis_unbekannt
                              ? "Für das Modell dieses Agenten ist kein Preis hinterlegt. Der Verbrauch steht in der Spalte Tokens."
                              : money.title(a.monat_usd)}>
                            {a.preis_unbekannt ? <span className="text-muted-foreground">Preis unbekannt</span> : money.fmt(a.monat_usd)}
                            {a.budget_usd ? <span className="text-[11px] text-muted-foreground"> / {money.fmt(a.budget_usd)}</span> : null}
                          </td>
                          <td className="py-2 text-right tabular-nums text-muted-foreground">{money.fmt(a.vormonat_usd)}</td>
                          <td className="py-2 text-right tabular-nums text-muted-foreground"
                            title={`${a.tokens_ein} ein / ${a.tokens_aus} aus`}>{tokensKurz(a.tokens_ein + a.tokens_aus)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>

            <p className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
              <Wallet className="h-3 w-3" /> Abgerechnet wird in US-Dollar; angezeigt in der Währung der Anlage.
              <FolderKanban className="ml-2 h-3 w-3" /> Ist ein Projektbudget aufgebraucht, starten bis Monatsende keine neuen Läufe des Projekts.
            </p>
          </>
        )}
      </div>
    </div>
  );
}

export default function FinanzenPage() {
  return (
    <Suspense fallback={null}>
      <FinanzenSeite />
    </Suspense>
  );
}

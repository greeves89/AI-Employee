"use client";

/**
 * Monatsbudget je Nutzer (Admin → Kosten & Budget).
 *
 * Ein Standard für alle Nutzer, dazu je Nutzer eine eigene Grenze, die ihn
 * ersetzt. Gezählt werden die Kosten ALLER Agenten eines Nutzers im laufenden
 * Monat, auch gelöschter. Durchgesetzt wird die Grenze serverseitig an der einen
 * Budget-Prüfung (Chat, Aufgaben, Zeitpläne, Kanäle, Sprache) — hier wird sie nur
 * eingestellt. Beträge werden in USD gespeichert und in der Anzeigewährung
 * eingegeben, wie beim Agentenbudget.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { Check, Loader2, Pencil, Search, Users, X } from "lucide-react";

import * as api from "@/lib/api";
import type { AdminUser } from "@/lib/types";
import { useMoney } from "@/hooks/use-money";
import { useToast } from "@/components/ui/dialog-provider";
import { cn } from "@/lib/utils";

type Art = "standard" | "eigene" | "keine";

function artVon(u: AdminUser): Art {
  if (u.budget_usd === null || u.budget_usd === undefined) return "standard";
  return u.budget_usd > 0 ? "eigene" : "keine";
}

export function NutzerBudget() {
  const toast = useToast();
  const money = useMoney();
  const [nutzer, setNutzer] = useState<AdminUser[] | null>(null);
  const [standard, setStandard] = useState<number>(0);
  const [standardText, setStandardText] = useState("");
  const [standardSpeichert, setStandardSpeichert] = useState(false);
  const [suche, setSuche] = useState("");
  const [bearbeitet, setBearbeitet] = useState<string | null>(null);
  const [art, setArt] = useState<Art>("standard");
  const [betrag, setBetrag] = useState("");
  const [speichert, setSpeichert] = useState<string | null>(null);

  const laden = useCallback(async () => {
    try {
      const [n, s] = await Promise.all([api.getUsers(), api.getSettings()]);
      setNutzer(n.users);
      const std = s.nutzer_budget_standard_usd ?? 0;
      setStandard(std);
      setStandardText(std > 0 ? money.toInput(std) : "");
    } catch (e) {
      toast.error("Nutzerbudgets nicht ladbar", String(e));
      setNutzer([]);
    }
    // money.toInput hängt nur an der Währung; neu laden, wenn sie da ist.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [money.cfg]);

  useEffect(() => {
    laden();
  }, [laden]);

  const standardSpeichern = async () => {
    const wert = money.fromInput(standardText);
    if (wert === undefined) {
      toast.error("Ungültiger Betrag", "Bitte eine Zahl ab 0 eingeben oder leer lassen.");
      return;
    }
    setStandardSpeichert(true);
    try {
      await api.updateSettings({ nutzer_budget_standard_usd: wert ?? 0 });
      setStandard(wert ?? 0);
      toast.success(wert ? `Standard: ${money.fmt(wert)} je Nutzer und Monat` : "Kein Standard-Budget mehr");
    } catch (e) {
      toast.error("Konnte den Standard nicht speichern", String(e));
    } finally {
      setStandardSpeichert(false);
    }
  };

  const bearbeiten = (u: AdminUser) => {
    setBearbeitet(u.id);
    setArt(artVon(u));
    setBetrag(u.budget_usd && u.budget_usd > 0 ? money.toInput(u.budget_usd) : "");
  };

  const speichern = async (u: AdminUser) => {
    let wert: number | null;
    if (art === "standard") wert = null;
    else if (art === "keine") wert = 0;
    else {
      const eingabe = money.fromInput(betrag);
      if (!eingabe || eingabe <= 0) {
        toast.error("Ungültiger Betrag", "Bitte einen Betrag größer als 0 eingeben.");
        return;
      }
      wert = eingabe;
    }
    setSpeichert(u.id);
    try {
      await api.setUserBudget(u.id, wert);
      setNutzer((alt) => (alt ?? []).map((x) => (x.id === u.id ? { ...x, budget_usd: wert } : x)));
      setBearbeitet(null);
      toast.success(`Budget von ${u.name || u.email} gespeichert`);
    } catch (e) {
      toast.error("Konnte das Budget nicht speichern", String(e));
    } finally {
      setSpeichert(null);
    }
  };

  const gefiltert = useMemo(() => {
    const q = suche.trim().toLowerCase();
    const liste = nutzer ?? [];
    return q ? liste.filter((u) => `${u.name} ${u.email}`.toLowerCase().includes(q)) : liste;
  }, [nutzer, suche]);

  const grenzeVon = (u: AdminUser): number | null => {
    if (u.budget_usd === null || u.budget_usd === undefined) return standard > 0 ? standard : null;
    return u.budget_usd > 0 ? u.budget_usd : null;
  };

  return (
    <div id="nutzer-budget" className="scroll-mt-24 rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-5">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <h3 className="text-sm font-semibold mb-1 flex items-center gap-2">
            <Users className="h-4 w-4 text-muted-foreground" />
            Budget je Nutzer
          </h3>
          <p className="text-xs text-muted-foreground leading-relaxed max-w-xl">
            Höchstbetrag, den die Agenten eines Nutzers zusammen im Monat verbrauchen dürfen. Ist er
            erreicht, nehmen sie bis Monatsende keine Nachrichten und Aufgaben mehr an; der Nutzer
            bekommt einen Hinweis, Admins eine Benachrichtigung. Eine eigene Grenze beim Nutzer
            ersetzt den Standard.
          </p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <label htmlFor="nutzer-budget-standard" className="text-xs text-muted-foreground">
            Standard
          </label>
          <div className="flex items-center gap-1 rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-2 py-1.5">
            <span className="text-xs text-muted-foreground">{money.symbol}</span>
            <input
              id="nutzer-budget-standard"
              type="number"
              min={0}
              step="0.01"
              value={standardText}
              onChange={(e) => setStandardText(e.target.value)}
              placeholder="keine Grenze"
              aria-label={`Standard-Monatsbudget je Nutzer in ${money.code}`}
              className="w-24 bg-transparent text-sm text-right outline-none"
            />
          </div>
          <button
            onClick={standardSpeichern}
            disabled={standardSpeichert || standardText === (standard > 0 ? money.toInput(standard) : "")}
            className="rounded-xl bg-primary px-3 py-2 text-xs font-medium text-primary-foreground shadow-lg shadow-primary/20 disabled:opacity-40"
          >
            {standardSpeichert ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : "Speichern"}
          </button>
        </div>
      </div>

      <div className="mt-4 flex items-center gap-2 rounded-lg border border-foreground/[0.06] bg-foreground/[0.02] px-3 py-1.5">
        <Search className="h-3.5 w-3.5 text-muted-foreground" />
        <input
          value={suche}
          onChange={(e) => setSuche(e.target.value)}
          placeholder="Nutzer suchen …"
          aria-label="Nutzer suchen"
          className="w-full bg-transparent text-xs outline-none"
        />
      </div>

      {nutzer === null ? (
        <div className="flex justify-center py-6"><Loader2 className="h-4 w-4 animate-spin text-muted-foreground" /></div>
      ) : (
        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left text-xs text-muted-foreground/70">
                <th className="py-2 pr-3 font-medium">Nutzer</th>
                <th className="py-2 pr-3 font-medium text-right">Diesen Monat</th>
                <th className="py-2 pr-3 font-medium">Grenze</th>
                <th className="py-2 font-medium" />
              </tr>
            </thead>
            <tbody>
              {gefiltert.map((u) => {
                const grenze = grenzeVon(u);
                const kosten = u.monthly_cost_usd ?? 0;
                const anteil = grenze ? Math.min(kosten / grenze, 1) : 0;
                const offen = bearbeitet === u.id;
                return (
                  <tr key={u.id} className="border-t border-foreground/[0.04] align-middle">
                    <td className="py-2 pr-3">
                      <div className="font-medium text-foreground">{u.name || u.email}</div>
                      <div className="text-xs text-muted-foreground/70">{u.email}</div>
                    </td>
                    <td className="py-2 pr-3 text-right tabular-nums" title={money.title(kosten)}>
                      {money.fmt(kosten)}
                      {grenze ? (
                        <div className="mt-1 ml-auto h-1 w-24 rounded-full bg-foreground/[0.06]">
                          <div
                            className={cn("h-1 rounded-full", anteil >= 1 ? "bg-red-500" : anteil >= 0.75 ? "bg-amber-500" : "bg-emerald-500")}
                            style={{ width: `${Math.round(anteil * 100)}%` }}
                          />
                        </div>
                      ) : null}
                    </td>
                    <td className="py-2 pr-3">
                      {offen ? (
                        <div className="flex flex-wrap items-center gap-2">
                          <select
                            value={art}
                            onChange={(e) => setArt(e.target.value as Art)}
                            aria-label="Art der Grenze"
                            className="rounded-lg border border-foreground/[0.12] bg-foreground/[0.04] px-2 py-1 text-xs outline-none"
                          >
                            <option value="standard">Standard</option>
                            <option value="eigene">Eigene Grenze</option>
                            <option value="keine">Keine Grenze</option>
                          </select>
                          {art === "eigene" ? (
                            <div className="flex items-center gap-1 rounded-lg border border-foreground/[0.12] bg-foreground/[0.04] px-2 py-1">
                              <span className="text-xs text-muted-foreground">{money.symbol}</span>
                              <input
                                type="number"
                                min={0}
                                step="0.01"
                                value={betrag}
                                onChange={(e) => setBetrag(e.target.value)}
                                aria-label={`Monatsbudget in ${money.code}`}
                                className="w-20 bg-transparent text-sm outline-none"
                                onKeyDown={(e) => {
                                  if (e.key === "Enter") speichern(u);
                                  if (e.key === "Escape") setBearbeitet(null);
                                }}
                                autoFocus
                              />
                            </div>
                          ) : null}
                        </div>
                      ) : (
                        <span className={cn(grenze ? "text-foreground" : "text-muted-foreground")}>
                          {artVon(u) === "keine"
                            ? "keine Grenze"
                            : grenze
                              ? `${money.fmt(grenze)}${artVon(u) === "standard" ? " (Standard)" : ""}`
                              : "keine Grenze"}
                        </span>
                      )}
                    </td>
                    <td className="py-2 text-right">
                      {offen ? (
                        <div className="flex justify-end gap-1">
                          <button
                            onClick={() => speichern(u)}
                            disabled={speichert === u.id}
                            title="Speichern"
                            className="rounded-lg p-1.5 text-emerald-500 hover:bg-emerald-500/10 disabled:opacity-40"
                          >
                            {speichert === u.id ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}
                          </button>
                          <button
                            onClick={() => setBearbeitet(null)}
                            title="Abbrechen"
                            className="rounded-lg p-1.5 text-muted-foreground hover:bg-foreground/[0.06]"
                          >
                            <X className="h-3.5 w-3.5" />
                          </button>
                        </div>
                      ) : (
                        <button
                          onClick={() => bearbeiten(u)}
                          title="Grenze ändern"
                          aria-label={`Grenze von ${u.name || u.email} ändern`}
                          className="rounded-lg p-1.5 text-muted-foreground hover:bg-foreground/[0.06] hover:text-foreground"
                        >
                          <Pencil className="h-3.5 w-3.5" />
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
              {gefiltert.length === 0 ? (
                <tr>
                  <td colSpan={4} className="py-4 text-center text-muted-foreground">Kein Nutzer gefunden.</td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

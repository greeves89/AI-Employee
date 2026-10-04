"use client";

/** Das eine Formular für neue Zeitpläne (#901).
 *
 *  „Wie oft?“ statt Cron: alle X Minuten/Stunden, täglich, werktags,
 *  wöchentlich an ausgewählten Tagen, monatlich — oder in eigenen Worten.
 *  Alles außer dem Intervall wird hier zu einem ``cron_expression``; der
 *  Freitext läuft über ``POST /schedules/parse-timing`` und landet im selben
 *  Feld. Angelegt wird danach ausschließlich über ``POST /schedules/`` — es
 *  gibt also nur einen Weg, wie ein Zeitplan entsteht.
 *
 *  Die Uhrzeit gilt in der Zeitzone des Browsers; sie wird mitgeschickt, damit
 *  „08:00“ auch 08:00 Ortszeit bedeutet (sonst rechnete der Server ohne Agent
 *  in UTC).
 */

import { useMemo, useState } from "react";
import { Loader2, Sparkles, Wand2 } from "lucide-react";
import * as api from "@/lib/api";
import { apiFehlertext } from "@/lib/api-fehler";
import { cn } from "@/lib/utils";
import { useToast } from "@/components/ui/dialog-provider";

export type Haeufigkeit = "intervall" | "taeglich" | "werktags" | "woechentlich" | "monatlich" | "freitext";

export interface ZeitplanTakt {
  art: Haeufigkeit;
  intervallWert: number;
  intervallEinheit: "min" | "std";
  /** "HH:MM" */
  uhrzeit: string;
  /** Cron-Wochentage: 1 = Mo … 6 = Sa, 0 = So. */
  wochentage: number[];
  monatstag: number;
  /** Ergebnis der Freitext-Übersetzung. */
  freitextCron: string;
}

export const STANDARD_TAKT: ZeitplanTakt = {
  art: "taeglich",
  intervallWert: 1,
  intervallEinheit: "std",
  uhrzeit: "08:00",
  wochentage: [1],
  monatstag: 1,
  freitextCron: "",
};

const HAEUFIGKEITEN: { art: Haeufigkeit; label: string }[] = [
  { art: "intervall", label: "Alle X Min./Std." },
  { art: "taeglich", label: "Täglich" },
  { art: "werktags", label: "Werktags" },
  { art: "woechentlich", label: "Wöchentlich" },
  { art: "monatlich", label: "Monatlich" },
  { art: "freitext", label: "In eigenen Worten" },
];

/** Montag zuerst, wie im deutschen Kalender. */
const WOCHENTAGE: { wert: number; kurz: string }[] = [
  { wert: 1, kurz: "Mo" },
  { wert: 2, kurz: "Di" },
  { wert: 3, kurz: "Mi" },
  { wert: 4, kurz: "Do" },
  { wert: 5, kurz: "Fr" },
  { wert: 6, kurz: "Sa" },
  { wert: 0, kurz: "So" },
];

function sortiereWochentage(tage: number[]): number[] {
  const reihenfolge = WOCHENTAGE.map((t) => t.wert);
  return [...new Set(tage)].sort((a, b) => reihenfolge.indexOf(a) - reihenfolge.indexOf(b));
}

function zerlegeUhrzeit(uhrzeit: string): { h: number; m: number } | null {
  const treffer = /^(\d{1,2}):(\d{2})$/.exec(uhrzeit.trim());
  if (!treffer) return null;
  const h = Number(treffer[1]);
  const m = Number(treffer[2]);
  if (h > 23 || m > 59) return null;
  return { h, m };
}

/** „Wie oft?“ → das, was ``POST /schedules/`` erwartet. ``null`` = unvollständig. */
export function taktZuZeitplan(takt: ZeitplanTakt): { interval_seconds?: number; cron_expression?: string } | null {
  if (takt.art === "intervall") {
    const wert = Math.floor(takt.intervallWert);
    if (!Number.isFinite(wert) || wert < 1) return null;
    return { interval_seconds: wert * (takt.intervallEinheit === "std" ? 3600 : 60) };
  }
  if (takt.art === "freitext") {
    return takt.freitextCron.trim() ? { cron_expression: takt.freitextCron.trim() } : null;
  }
  const zeit = zerlegeUhrzeit(takt.uhrzeit);
  if (!zeit) return null;
  const { h, m } = zeit;
  switch (takt.art) {
    case "taeglich":
      return { cron_expression: `${m} ${h} * * *` };
    case "werktags":
      return { cron_expression: `${m} ${h} * * 1-5` };
    case "woechentlich": {
      const tage = sortiereWochentage(takt.wochentage);
      if (tage.length === 0) return null;
      return { cron_expression: `${m} ${h} * * ${tage.join(",")}` };
    }
    case "monatlich": {
      const tag = Math.floor(takt.monatstag);
      if (tag < 1 || tag > 31) return null;
      return { cron_expression: `${m} ${h} ${tag} * *` };
    }
  }
  return null;
}

/** Eine lesbare Zeile zur Auswahl, z. B. „Wöchentlich am Mo, Mi um 07:30“. */
export function taktBeschreibung(takt: ZeitplanTakt): string {
  const uhr = takt.uhrzeit;
  switch (takt.art) {
    case "intervall":
      return `Alle ${takt.intervallWert} ${takt.intervallEinheit === "std" ? "Std." : "Min."}`;
    case "taeglich":
      return `Täglich um ${uhr}`;
    case "werktags":
      return `Werktags (Mo–Fr) um ${uhr}`;
    case "woechentlich": {
      const tage = sortiereWochentage(takt.wochentage)
        .map((w) => WOCHENTAGE.find((t) => t.wert === w)?.kurz)
        .filter(Boolean);
      return tage.length ? `Wöchentlich am ${tage.join(", ")} um ${uhr}` : "Wöchentlich — bitte mindestens einen Tag wählen";
    }
    case "monatlich":
      return `Monatlich am ${takt.monatstag}. um ${uhr}`;
    case "freitext":
      return "";
  }
}

function browserZeitzone(): string | undefined {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || undefined;
  } catch {
    return undefined;
  }
}

const eingabe =
  "w-full rounded-xl border border-foreground/[0.06] bg-foreground/[0.03] px-4 py-2.5 text-sm placeholder:text-muted-foreground/40 focus:border-primary/50 focus:outline-none focus:ring-1 focus:ring-primary/25";

function chip(aktiv: boolean) {
  return cn(
    "rounded-lg px-3 py-2 text-xs font-medium transition-colors border",
    aktiv
      ? "bg-primary/20 text-primary border-primary/30"
      : "bg-foreground/[0.04] text-muted-foreground border-foreground/[0.06] hover:bg-foreground/[0.08]"
  );
}

const PRIORITAETEN = [
  { value: 0, label: "Niedrig", color: "text-slate-400" },
  { value: 1, label: "Normal", color: "text-blue-400" },
  { value: 2, label: "Hoch", color: "text-amber-700 dark:text-amber-400" },
  { value: 3, label: "Dringend", color: "text-red-400" },
];

export interface ZeitplanFormularProps {
  agents: { id: string; name: string; state?: string }[];
  onAngelegt: () => void | Promise<void>;
  onAbbrechen: () => void;
}

export function ZeitplanFormular({ agents, onAngelegt, onAbbrechen }: ZeitplanFormularProps) {
  const toast = useToast();
  const [name, setName] = useState("");
  const [auftrag, setAuftrag] = useState("");
  const [prioritaet, setPrioritaet] = useState(1);
  const [agentId, setAgentId] = useState("");
  const [takt, setTakt] = useState<ZeitplanTakt>(STANDARD_TAKT);
  const [anlegen, setAnlegen] = useState(false);

  const [freitext, setFreitext] = useState("");
  const [deuten, setDeuten] = useState(false);
  const [deutenFehler, setDeutenFehler] = useState("");
  const [vorschau, setVorschau] = useState<{ explanation: string; next_runs: string[] } | null>(null);

  const zeitzone = useMemo(browserZeitzone, []);
  const zeitplan = taktZuZeitplan(takt);
  const bereit = name.trim() !== "" && auftrag.trim() !== "" && zeitplan !== null && !anlegen;

  const aendere = (teil: Partial<ZeitplanTakt>) => setTakt((t) => ({ ...t, ...teil }));

  const freitextDeuten = async () => {
    if (!freitext.trim()) return;
    setDeuten(true);
    setDeutenFehler("");
    try {
      const ergebnis = await api.parseScheduleTiming({
        text: freitext.trim(),
        agent_id: agentId || undefined,
        timezone: zeitzone,
      });
      aendere({ freitextCron: ergebnis.cron_expression });
      setVorschau({ explanation: ergebnis.explanation, next_runs: ergebnis.next_runs });
    } catch (e) {
      aendere({ freitextCron: "" });
      setVorschau(null);
      setDeutenFehler(apiFehlertext(e, "Die Zeitangabe ließ sich nicht deuten."));
    } finally {
      setDeuten(false);
    }
  };

  const absenden = async () => {
    if (!bereit || !zeitplan) return;
    setAnlegen(true);
    try {
      await api.createSchedule({
        name: name.trim(),
        prompt: auftrag.trim(),
        interval_seconds: zeitplan.interval_seconds ?? 0,
        cron_expression: zeitplan.cron_expression,
        timezone: zeitzone,
        priority: prioritaet,
        agent_id: agentId || undefined,
      });
      await onAngelegt();
    } catch (e) {
      toast.error("Zeitplan konnte nicht angelegt werden", apiFehlertext(e));
    } finally {
      setAnlegen(false);
    }
  };

  return (
    <div className="rounded-2xl border border-foreground/[0.06] bg-card/80 p-4 sm:p-6 backdrop-blur-sm">
      <div className="mb-4 flex items-center gap-2">
        <Sparkles className="h-4 w-4 text-primary" />
        <h3 className="text-sm font-semibold">Neuer Zeitplan</h3>
      </div>
      <div className="space-y-4">
        <div>
          <label className="mb-1.5 block text-xs font-medium text-muted-foreground">Name</label>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="z. B. Wochenbericht"
            className={eingabe}
          />
        </div>
        <div>
          <label className="mb-1.5 block text-xs font-medium text-muted-foreground">Auftrag</label>
          <textarea
            value={auftrag}
            onChange={(e) => setAuftrag(e.target.value)}
            rows={3}
            placeholder="Was soll der Agent jedes Mal tun?"
            className={cn(eingabe, "resize-none")}
          />
        </div>

        {/* Wie oft? */}
        <div>
          <label className="mb-1.5 block text-xs font-medium text-muted-foreground">Wie oft?</label>
          <div className="mb-3 flex flex-wrap gap-2">
            {HAEUFIGKEITEN.map((h) => (
              <button
                key={h.art}
                type="button"
                onClick={() => aendere({ art: h.art })}
                className={chip(takt.art === h.art)}
              >
                {h.label}
              </button>
            ))}
          </div>

          {takt.art === "intervall" && (
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs text-muted-foreground">Alle</span>
              <input
                type="number"
                min={1}
                inputMode="numeric"
                value={takt.intervallWert}
                onChange={(e) => aendere({ intervallWert: Number(e.target.value) || 0 })}
                className={cn(eingabe, "w-24")}
                aria-label="Abstand"
              />
              <select
                value={takt.intervallEinheit}
                onChange={(e) => aendere({ intervallEinheit: e.target.value as "min" | "std" })}
                className={cn(eingabe, "w-auto")}
                aria-label="Einheit"
              >
                <option value="min">Minuten</option>
                <option value="std">Stunden</option>
              </select>
            </div>
          )}

          {takt.art === "woechentlich" && (
            <div className="mb-3 flex flex-wrap gap-1.5" role="group" aria-label="Wochentage">
              {WOCHENTAGE.map((t) => {
                const an = takt.wochentage.includes(t.wert);
                return (
                  <button
                    key={t.wert}
                    type="button"
                    aria-pressed={an}
                    onClick={() =>
                      aendere({
                        wochentage: an
                          ? takt.wochentage.filter((w) => w !== t.wert)
                          : [...takt.wochentage, t.wert],
                      })
                    }
                    className={cn(chip(an), "min-w-[2.75rem]")}
                  >
                    {t.kurz}
                  </button>
                );
              })}
            </div>
          )}

          {takt.art === "monatlich" && (
            <div className="mb-3 flex flex-wrap items-center gap-2">
              <span className="text-xs text-muted-foreground">Am</span>
              <select
                value={takt.monatstag}
                onChange={(e) => aendere({ monatstag: Number(e.target.value) })}
                className={cn(eingabe, "w-auto")}
                aria-label="Tag im Monat"
              >
                {Array.from({ length: 31 }, (_, i) => i + 1).map((tag) => (
                  <option key={tag} value={tag}>{tag}.</option>
                ))}
              </select>
              <span className="text-xs text-muted-foreground">des Monats</span>
              {takt.monatstag > 28 && (
                <span className="w-full text-[11px] text-muted-foreground/70">
                  In Monaten ohne diesen Tag fällt der Lauf aus.
                </span>
              )}
            </div>
          )}

          {(takt.art === "taeglich" || takt.art === "werktags" || takt.art === "woechentlich" || takt.art === "monatlich") && (
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs text-muted-foreground">um</span>
              <input
                type="time"
                value={takt.uhrzeit}
                onChange={(e) => aendere({ uhrzeit: e.target.value })}
                className={cn(eingabe, "w-auto")}
                aria-label="Uhrzeit"
              />
              <span className="text-xs text-muted-foreground">Uhr</span>
            </div>
          )}

          {takt.art === "freitext" && (
            <div className="space-y-2">
              <div className="flex flex-col gap-2 sm:flex-row">
                <input
                  value={freitext}
                  onChange={(e) => {
                    setFreitext(e.target.value);
                    aendere({ freitextCron: "" });
                    setVorschau(null);
                    setDeutenFehler("");
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      freitextDeuten();
                    }
                  }}
                  placeholder="z. B. „jeden Freitag um 17 Uhr“"
                  className={cn(eingabe, "flex-1")}
                />
                <button
                  type="button"
                  onClick={freitextDeuten}
                  disabled={!freitext.trim() || deuten}
                  className="inline-flex items-center justify-center gap-1.5 rounded-xl bg-foreground/[0.06] px-3 py-2.5 text-xs font-medium text-foreground transition-colors hover:bg-foreground/[0.1] disabled:opacity-40"
                >
                  {deuten ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Wand2 className="h-3.5 w-3.5" />}
                  Übernehmen
                </button>
              </div>
              {deutenFehler && <p className="text-[11px] text-red-400">{deutenFehler}</p>}
              {vorschau && (
                <div className="space-y-1 rounded-xl border border-primary/20 bg-primary/[0.04] px-4 py-3">
                  <p className="text-xs font-medium text-primary">{vorschau.explanation}</p>
                  {vorschau.next_runs.length > 0 && (
                    <p className="text-[11px] text-muted-foreground/70">
                      Nächste Läufe:{" "}
                      {vorschau.next_runs
                        .map((r) =>
                          new Date(r).toLocaleString("de-DE", {
                            weekday: "short", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
                          })
                        )
                        .join(" · ")}
                    </p>
                  )}
                </div>
              )}
            </div>
          )}

          {takt.art !== "freitext" && (
            <p className="mt-2 text-[11px] text-muted-foreground/70">
              {taktBeschreibung(takt)}
              {takt.art !== "intervall" && zeitzone ? ` (Zeitzone ${zeitzone})` : ""}
            </p>
          )}
        </div>

        {/* Priorität + Agent */}
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div>
            <label className="mb-1.5 block text-xs font-medium text-muted-foreground">Priorität</label>
            <div className="grid grid-cols-4 gap-2">
              {PRIORITAETEN.map((p) => (
                <button
                  key={p.value}
                  type="button"
                  onClick={() => setPrioritaet(p.value)}
                  className={cn(
                    "rounded-lg px-2 py-2 text-xs font-medium transition-colors border",
                    prioritaet === p.value
                      ? `bg-foreground/[0.08] ${p.color} border-foreground/[0.12]`
                      : "bg-foreground/[0.03] text-muted-foreground border-foreground/[0.06] hover:bg-foreground/[0.06]"
                  )}
                >
                  {p.label}
                </button>
              ))}
            </div>
          </div>
          <div>
            <label className="mb-1.5 block text-xs font-medium text-muted-foreground">Agent</label>
            {/* Alle eigenen Agenten, auch gestoppte: der Scheduler startet einen
                gestoppten Agenten zum Lauf (#632). Vorher standen hier nur laufende —
                ein Mitglied, dessen Agent gerade ruhte, sah nur „Automatisch“ (#901). */}
            <select value={agentId} onChange={(e) => setAgentId(e.target.value)} className={eingabe}>
              <option value="">Automatisch zuweisen</option>
              {agents.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.state === "stopped" ? `${a.name} (gestoppt, startet zum Lauf)` : a.name}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="flex flex-col-reverse gap-2 pt-2 sm:flex-row sm:justify-end sm:gap-3">
          <button
            type="button"
            onClick={onAbbrechen}
            className="rounded-xl px-4 py-2.5 text-sm text-muted-foreground transition-colors hover:text-foreground"
          >
            Abbrechen
          </button>
          <button
            type="button"
            onClick={absenden}
            disabled={!bereit}
            className="inline-flex items-center justify-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground transition-all hover:brightness-110 disabled:opacity-50"
          >
            {anlegen && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            Zeitplan anlegen
          </button>
        </div>
      </div>
    </div>
  );
}

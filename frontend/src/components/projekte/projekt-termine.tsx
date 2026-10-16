"use client";

/**
 * Termine eines Projekts: Daily, Weekly oder einmalig — als Besprechung der Mitglieder
 * (orchestrator: core/projekt_termine). Das Protokoll landet im Projektwissen, die
 * Aufgaben daraus als Karten unter „Bereit“.
 */

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { CalendarClock, ExternalLink, Loader2, Plus, Trash2 } from "lucide-react";
import * as api from "@/lib/api";
import { apiFehlertext } from "@/lib/api-fehler";
import { WOCHENTAGE, type ProjektBesprechung, type ProjektTermin } from "@/lib/projekte";
import { useConfirm, useToast } from "@/components/ui/dialog-provider";
import { MarkdownContent } from "@/components/ui/markdown-content";
import { timeAgo } from "@/lib/utils";

const ART_LABEL: Record<string, string> = { daily: "Daily (Mo–Fr)", weekly: "Wöchentlich", einmalig: "Einmalig" };

function wann(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("de-DE", { weekday: "short", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

export function ProjektTermine({ projektId, darfVerwalten, anzahlAgenten }: { projektId: string; darfVerwalten: boolean; anzahlAgenten: number }) {
  const toast = useToast();
  const toastRef = useRef(toast);
  toastRef.current = toast;
  const confirm = useConfirm();
  const [termine, setTermine] = useState<ProjektTermin[] | null>(null);
  const [besprechungen, setBesprechungen] = useState<ProjektBesprechung[]>([]);
  const [art, setArt] = useState<"daily" | "weekly" | "einmalig">("daily");
  const [uhrzeit, setUhrzeit] = useState("09:00");
  const [wochentag, setWochentag] = useState("fr");
  const [datum, setDatum] = useState("");
  const [thema, setThema] = useState("");
  const [legtAn, setLegtAn] = useState(false);

  const laden = useCallback(async () => {
    try {
      const r = await api.projektTermine(projektId);
      setTermine(r.termine);
      setBesprechungen(r.besprechungen);
    } catch (e) {
      toastRef.current.error("Termine nicht geladen", apiFehlertext(e));
    }
  }, [projektId]);

  useEffect(() => { laden(); }, [laden]);

  const anlegen = async () => {
    setLegtAn(true);
    try {
      await api.projektTerminAnlegen(projektId, {
        art, uhrzeit,
        ...(art === "weekly" ? { wochentag } : {}),
        ...(art === "einmalig" ? { datum } : {}),
        ...(thema.trim() ? { thema: thema.trim() } : {}),
      });
      toastRef.current.success("Termin angelegt", `${ART_LABEL[art]} ${uhrzeit}`);
      setThema("");
      await laden();
    } catch (e) {
      toastRef.current.error("Nicht angelegt", apiFehlertext(e));
    } finally {
      setLegtAn(false);
    }
  };

  const entfernen = async (t: ProjektTermin) => {
    const ok = await confirm({ title: "Termin entfernen?", message: `${ART_LABEL[t.art]} ${t.uhrzeit ?? ""} — ${t.thema}`, variant: "destructive", confirmLabel: "Entfernen" });
    if (!ok) return;
    try {
      await api.projektTerminEntfernen(projektId, t.id);
      await laden();
    } catch (e) {
      toastRef.current.error("Nicht entfernt", apiFehlertext(e));
    }
  };

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <section className="min-w-0 space-y-3 rounded-2xl border border-foreground/[0.08] bg-card p-4">
        <h3 className="text-sm font-semibold">Feste Termine</h3>
        {termine === null ? (
          <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
        ) : termine.length === 0 ? (
          <p className="text-xs text-muted-foreground">
            Noch kein Termin. Ein Daily hält mehrere Agenten im Takt — der Lead kann es auch selbst anlegen.
          </p>
        ) : (
          <ul className="space-y-2">
            {termine.map((t) => (
              <li key={t.id} className="flex items-start gap-2.5 rounded-xl bg-foreground/[0.03] px-3 py-2">
                <CalendarClock className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium">
                    {ART_LABEL[t.art] ?? t.art} · {t.uhrzeit}
                    {t.wochentag ? ` · ${WOCHENTAGE.find((w) => w.key === t.wochentag)?.label ?? t.wochentag}` : ""}
                  </p>
                  <p className="truncate text-xs text-muted-foreground" title={t.thema}>{t.thema}</p>
                  <p className="text-[11px] text-muted-foreground">Nächster: {t.aktiv ? wann(t.naechster) : "—"}</p>
                </div>
                {darfVerwalten && (
                  <button type="button" onClick={() => entfernen(t)} aria-label="Termin entfernen" title="Termin entfernen"
                    className="rounded-md p-1 text-muted-foreground hover:bg-red-500/10 hover:text-red-500">
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}

        {darfVerwalten && (
          <div className="space-y-2 border-t border-foreground/[0.06] pt-3">
            <p className="text-xs font-medium text-muted-foreground">Neuer Termin</p>
            {anzahlAgenten < 2 ? (
              <p className="text-xs text-muted-foreground">Eine Besprechung braucht mindestens zwei Agenten im Projekt.</p>
            ) : (
              <>
                <div className="flex flex-wrap gap-2">
                  <select value={art} onChange={(e) => setArt(e.target.value as typeof art)} aria-label="Art"
                    className="rounded-lg border border-foreground/[0.1] bg-background/80 px-2 py-1.5 text-sm">
                    <option value="daily">Daily (Mo–Fr)</option>
                    <option value="weekly">Wöchentlich</option>
                    <option value="einmalig">Einmalig</option>
                  </select>
                  {art === "weekly" && (
                    <select value={wochentag} onChange={(e) => setWochentag(e.target.value)} aria-label="Wochentag"
                      className="rounded-lg border border-foreground/[0.1] bg-background/80 px-2 py-1.5 text-sm">
                      {WOCHENTAGE.map((w) => <option key={w.key} value={w.key}>{w.label}</option>)}
                    </select>
                  )}
                  {art === "einmalig" && (
                    <input type="date" value={datum} onChange={(e) => setDatum(e.target.value)} aria-label="Datum"
                      className="rounded-lg border border-foreground/[0.1] bg-background/80 px-2 py-1.5 text-sm" />
                  )}
                  <input type="time" value={uhrzeit} onChange={(e) => setUhrzeit(e.target.value)} aria-label="Uhrzeit"
                    className="rounded-lg border border-foreground/[0.1] bg-background/80 px-2 py-1.5 text-sm" />
                </div>
                <input value={thema} onChange={(e) => setThema(e.target.value)} placeholder="Thema (optional) — sonst Stand, Hindernisse, nächste Schritte"
                  className="w-full rounded-lg border border-foreground/[0.1] bg-background/80 px-3 py-1.5 text-sm" />
                <button type="button" onClick={anlegen} disabled={legtAn || (art === "einmalig" && !datum)}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50">
                  {legtAn ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />} Termin anlegen
                </button>
              </>
            )}
          </div>
        )}
      </section>

      <section className="min-w-0 space-y-3 rounded-2xl border border-foreground/[0.08] bg-card p-4">
        <h3 className="text-sm font-semibold">Letzte Besprechungen</h3>
        {besprechungen.length === 0 ? (
          <p className="text-xs text-muted-foreground">Noch keine. Protokolle stehen danach hier, im Gespräch und im Projektwissen (protokolle/).</p>
        ) : (
          <ul className="space-y-2">
            {besprechungen.map((b) => (
              <li key={b.id} className="rounded-xl bg-foreground/[0.03] px-3 py-2">
                <div className="flex items-center justify-between gap-2">
                  <p className="truncate text-sm font-medium">{b.name}</p>
                  <Link href={`/meeting-rooms/${b.id}`} className="inline-flex shrink-0 items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground">
                    {b.zustand === "running" ? "läuft" : b.erstellt ? timeAgo(b.erstellt) : ""} <ExternalLink className="h-3 w-3" />
                  </Link>
                </div>
                {b.zusammenfassung && (
                  <div className="mt-1 line-clamp-6 text-xs text-muted-foreground"><MarkdownContent content={b.zusammenfassung} /></div>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

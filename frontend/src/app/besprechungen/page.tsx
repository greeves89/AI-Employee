"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { CalendarClock, CheckSquare, FileText, Loader2, Mic, Send, Trash2, TriangleAlert, UserX } from "lucide-react";
import { Header } from "@/components/layout/header";
import { MarkdownContent } from "@/components/ui/markdown-content";
import { useConfirm, useToast } from "@/components/ui/dialog-provider";
import * as api from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * Besprechungen: Teams-Termine der eigenen Agenten (services/teams_meetings) und Mitschnitte
 * aus dem Browser — dieselbe Tabelle ``meetings``, nur der Besitzer sieht sie.
 * Plan: docs/plaene/teams-besprechungen.md.
 */

const STATUS: Record<string, string> = {
  geplant: "geplant",
  laeuft: "läuft gerade",
  beendet: "vorbei, Protokoll folgt",
  protokoll: "Protokoll fertig",
};

const HINWEIS: Record<string, string> = {
  gesendet: "Hinweis „schreibt mit“ im Chat gesendet",
  freigabe: "Hinweis wartet auf deine Freigabe",
  aus: "Kein Hinweis: Der Agent darf in Microsoft 365 nur lesen",
  fehler: "Hinweis konnte nicht gesendet werden, wird erneut versucht",
  zu_viele: "Hinweis zurückgestellt: zu viele offene Freigaben",
};

function zeit(b: api.Besprechung): string {
  const d = new Date(b.beginn || b.created_at);
  return d.toLocaleString("de-DE", { weekday: "short", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

export default function BesprechungenPage() {
  const [liste, setListe] = useState<api.Besprechung[] | null>(null);
  const [agenten, setAgenten] = useState<Record<string, string>>({});
  const [gewaehlt, setGewaehlt] = useState<string | null>(null);
  const [ansicht, setAnsicht] = useState<"protokoll" | "transkript">("protokoll");
  const [fehler, setFehler] = useState<string | null>(null);
  const confirm = useConfirm();
  const toast = useToast();
  const [schickAgent, setSchickAgent] = useState("");
  const [schickLink, setSchickLink] = useState("");
  const [schickTitel, setSchickTitel] = useState("");
  const [schickt, setSchickt] = useState(false);

  const schicken = async () => {
    if (!schickAgent || !schickLink.trim()) return;
    setSchickt(true);
    try {
      const neu = await api.agentInBesprechungSchicken(schickAgent, schickLink.trim(), schickTitel.trim());
      toast.success("Agent ist unterwegs", "Lass ihn in Teams aus dem Wartebereich herein.");
      setSchickLink("");
      setSchickTitel("");
      setListe((l) => [neu, ...(l || []).filter((x) => x.id !== neu.id)]);
      setGewaehlt(neu.id);
    } catch (e) {
      toast.error("Nicht geschickt", e instanceof Error ? e.message : undefined);
    } finally {
      setSchickt(false);
    }
  };

  const laden = useCallback(async () => {
    try {
      const [b, a] = await Promise.all([api.getBesprechungen(), api.getAgents("own").catch(() => ({ agents: [] }))]);
      setListe(b.meetings);
      setAgenten(Object.fromEntries(a.agents.map((x) => [x.id, x.name])));
      setGewaehlt((alt) => alt ?? b.meetings[0]?.id ?? null);
    } catch (e) {
      setFehler(e instanceof Error ? e.message : "Besprechungen ließen sich nicht laden");
    }
  }, []);

  useEffect(() => { laden(); }, [laden]);

  const b = useMemo(() => liste?.find((x) => x.id === gewaehlt) ?? null, [liste, gewaehlt]);

  const loeschen = async (x: api.Besprechung) => {
    const ok = await confirm({
      title: `„${x.title}“ löschen?`,
      message: "Protokoll und Transkript dieser Besprechung werden entfernt. Ein Wissenseintrag dazu bleibt.",
      variant: "destructive",
      confirmLabel: "Löschen",
    });
    if (!ok) return;
    try {
      await api.loescheBesprechung(x.id);
      setListe((l) => (l || []).filter((y) => y.id !== x.id));
      setGewaehlt(null);
      toast.success("Gelöscht");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Löschen fehlgeschlagen");
    }
  };

  return (
    <div>
      <Header title="Teams-Besprechungen" subtitle="Teams-Termine deiner Agenten und Mitschnitte: Protokoll, Aufgaben, Transkript" />
      <div className="p-6">
        {Object.keys(agenten).length > 0 && (
          <div className="mb-6 rounded-xl border border-foreground/10 p-4">
            <p className="text-sm font-medium">Agent in eine Teams-Besprechung schicken</p>
            <p className="mb-3 text-xs text-muted-foreground">
              Link aus Teams einfügen („Jetzt an der Besprechung teilnehmen“). Der Agent tritt als Gast bei,
              hört zu und schreibt das Protokoll. In Teams lässt du ihn aus dem Wartebereich herein.
            </p>
            <div className="flex flex-wrap gap-2">
              <select value={schickAgent} onChange={(e) => setSchickAgent(e.target.value)} aria-label="Agent"
                className="rounded-lg border border-foreground/10 bg-background px-3 py-2 text-sm">
                <option value="">Agent wählen</option>
                {Object.entries(agenten).map(([id, name]) => <option key={id} value={id}>{name}</option>)}
              </select>
              <input value={schickLink} onChange={(e) => setSchickLink(e.target.value)} placeholder="https://teams.microsoft.com/…"
                aria-label="Teams-Link" className="min-w-[16rem] flex-1 rounded-lg border border-foreground/10 bg-background px-3 py-2 text-sm" />
              <input value={schickTitel} onChange={(e) => setSchickTitel(e.target.value)} placeholder="Titel (optional)"
                aria-label="Titel" className="w-48 rounded-lg border border-foreground/10 bg-background px-3 py-2 text-sm" />
              <button type="button" onClick={schicken} disabled={schickt || !schickAgent || !schickLink.trim()}
                className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50">
                {schickt ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
                Schicken
              </button>
            </div>
            {/* Warum „Schicken“ noch grau ist (UI-Bewertung 14.11.2026). */}
            {!schickt && (!schickAgent || !schickLink.trim()) && (
              <p className="mt-2 text-xs text-muted-foreground">
                {!schickAgent && !schickLink.trim() ? "Agent wählen und Teams-Link einfügen, dann „Schicken“."
                  : !schickAgent ? "Noch einen Agenten wählen." : "Noch den Teams-Link einfügen."}
              </p>
            )}
          </div>
        )}
        {fehler && <div className="mb-4 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-600 dark:text-red-400">{fehler}</div>}
        {liste === null && !fehler && <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />}
        {liste && liste.length === 0 && (
          <div className="rounded-xl border border-foreground/10 p-5 text-sm text-muted-foreground">
            Noch keine Besprechungen. Schalte im Chat eines Agenten unter <b>Einstellungen → Konten &amp; Dienste → Microsoft Teams →
            Teams-Besprechungen</b> die Teilnahme ein und lade ihn zu einem Teams-Termin ein. Nach dem Termin
            steht hier sein Protokoll.
          </div>
        )}
        {liste && liste.length > 0 && (
          <div className="grid gap-6 lg:grid-cols-[320px_1fr]">
            <ul className="space-y-1">
              {liste.map((x) => (
                <li key={x.id}>
                  <button type="button" onClick={() => setGewaehlt(x.id)}
                    className={cn("w-full rounded-lg px-3 py-2 text-left transition-colors hover:bg-foreground/5",
                      x.id === gewaehlt && "bg-foreground/[0.07]")}>
                    <div className="flex items-center gap-2">
                      {x.quelle === "teams" ? <CalendarClock className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                        : <Mic className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />}
                      <span className="truncate text-sm font-medium">{x.title}</span>
                    </div>
                    <div className="mt-0.5 pl-5 text-xs text-muted-foreground">
                      {zeit(x)}
                      {x.agent_id && agenten[x.agent_id] && ` · ${agenten[x.agent_id]}`}
                      {x.status && ` · ${STATUS[x.status] || x.status}`}
                    </div>
                  </button>
                </li>
              ))}
            </ul>

            {b && (
              <section className="min-w-0 space-y-4">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <h2 className="text-lg font-semibold">{b.title}</h2>
                    <p className="text-xs text-muted-foreground">
                      {zeit(b)}
                      {b.agent_id && agenten[b.agent_id] && ` · Agent: ${agenten[b.agent_id]}`}
                      {b.participants.length > 0 && ` · ${b.participants.length} Teilnehmende`}
                    </p>
                  </div>
                  <button type="button" onClick={() => loeschen(b)} aria-label="Besprechung löschen"
                    className="rounded-lg p-2 text-muted-foreground hover:bg-foreground/5 hover:text-red-500">
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>

                {b.gaeste_extern && (
                  <div className="flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-400">
                    <UserX className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                    Gäste von außen eingeladen. Der Agent hält sich heraus, außer es ist bei ihm erlaubt.
                  </div>
                )}
                {b.hinweis && (
                  <div className={cn("flex items-start gap-2 text-xs",
                    b.hinweis === "gesendet" ? "text-muted-foreground" : "text-amber-700 dark:text-amber-400")}>
                    {b.hinweis !== "gesendet" && <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />}
                    {HINWEIS[b.hinweis] || b.hinweis}
                  </div>
                )}

                {b.aufgaben.length > 0 && (
                  <div className="rounded-xl border border-foreground/10 p-4">
                    <h3 className="mb-2 flex items-center gap-2 text-sm font-medium"><CheckSquare className="h-4 w-4" /> Aufgaben</h3>
                    <ul className="space-y-1 text-sm">
                      {b.aufgaben.map((a, i) => <li key={i} className="flex gap-2"><span className="text-muted-foreground">–</span>{a}</li>)}
                    </ul>
                  </div>
                )}

                <div className="flex gap-1 border-b border-foreground/10">
                  {(["protokoll", "transkript"] as const).map((t) => (
                    <button key={t} type="button" onClick={() => setAnsicht(t)}
                      className={cn("-mb-px border-b-2 px-3 py-1.5 text-sm",
                        ansicht === t ? "border-primary font-medium" : "border-transparent text-muted-foreground")}>
                      {t === "protokoll" ? "Protokoll" : "Transkript"}
                    </button>
                  ))}
                </div>
                {ansicht === "protokoll" && (b.protokoll
                  ? <MarkdownContent content={b.protokoll} ohneBilder className="text-sm" />
                  : <p className="flex items-center gap-2 text-sm text-muted-foreground"><FileText className="h-4 w-4" />
                      {b.quelle === "teams" ? "Das Protokoll schreibt der Agent, sobald das Teams-Transkript vorliegt."
                        : "Kein Protokoll gespeichert."}</p>)}
                {ansicht === "transkript" && (b.transcript
                  ? <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap rounded-lg bg-foreground/[0.03] p-3 text-xs">{b.transcript}</pre>
                  : <p className="text-sm text-muted-foreground">Noch kein Transkript. Teams stellt es kurz nach dem Termin bereit, wenn die Aufzeichnung mit Transkription lief.</p>)}
              </section>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

"use client";

/**
 * Heute: das Sekretariat (orchestrator: core/sekretariat). Termine und ungelesene Mails
 * direkt aus Microsoft 365, Entwürfe, die auf Freigabe warten (Freigeben sendet die Mail —
 * vom Server, core/mail_freigabe), Erinnerungen, feste Regeln („Merke: …“), Tagesbriefing.
 * Ohne Sekretariat: der Einrichten-Assistent.
 */

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  Bell, CalendarDays, Check, Inbox, Loader2, Mail, Plus, Send, ShieldCheck, Sparkles, Star, Sun, Trash2, Video, X,
} from "lucide-react";
import { Header } from "@/components/layout/header";
import { AgentView } from "@/components/agents/agent-views";
import { AgentAvatar } from "@/components/agents/agent-avatar";
import { MarkdownContent } from "@/components/ui/markdown-content";
import { useToast } from "@/components/ui/dialog-provider";
import { useAgents } from "@/hooks/use-agents";
import * as api from "@/lib/api";
import { apiFehlertext } from "@/lib/api-fehler";
import { setVisibleInterval } from "@/lib/visible-interval";
import { cn, timeAgo } from "@/lib/utils";

const ROLLEN = ["Sichten (Lead)", "Entwerfen", "Prüfen", "Ablegen"];

function uhrzeit(iso: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
}

function Kasten({ titel, icon: Icon, rechts, children }: { titel: string; icon: React.ElementType; rechts?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="min-w-0 rounded-2xl border border-foreground/[0.08] bg-card p-4">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h3 className="inline-flex items-center gap-1.5 text-sm font-semibold"><Icon className="h-4 w-4 text-muted-foreground" /> {titel}</h3>
        {rechts}
      </div>
      {children}
    </section>
  );
}

function Einrichten({ onFertig, entwurfFrei }: { onFertig: () => void; entwurfFrei: boolean }) {
  const { agents } = useAgents();
  const toast = useToast();
  const [gewaehlt, setGewaehlt] = useState<string[]>([]);
  const [takt, setTakt] = useState(30);
  const [briefing, setBriefing] = useState("07:30");
  const [laeuft, setLaeuft] = useState(false);

  const umschalten = (id: string) =>
    setGewaehlt((g) => (g.includes(id) ? g.filter((x) => x !== id) : g.length < 4 ? [...g, id] : g));

  const anlegen = async () => {
    setLaeuft(true);
    try {
      const r = await api.sekretariatEinrichten({ agent_ids: gewaehlt, sichten_minuten: takt, briefing });
      toast.success("Sekretariat eingerichtet", r.hinweise.join(" ") || "Die Agenten fangen mit dem nächsten Takt an.");
      onFertig();
    } catch (e) {
      toast.error("Nicht eingerichtet", apiFehlertext(e));
    } finally {
      setLaeuft(false);
    }
  };

  return (
    <div className="mx-auto max-w-3xl space-y-4 rounded-2xl border border-foreground/[0.08] bg-card p-5">
      <div>
        <h3 className="text-base font-semibold">Sekretariat einrichten</h3>
        <p className="mt-1 text-sm text-muted-foreground">
          Ein bis vier deiner Agenten betreuen Postfach und Kalender: Sie sichten neue Mails, entwerfen Antworten,
          legen ab und behalten Fristen im Blick. <strong>Gesendet wird nichts ohne dich</strong> — jede Antwort
          liegt hier unter „Wartet auf dich“, bis du sie freigibst.
        </p>
        {!entwurfFrei && (
          <p className="mt-2 rounded-lg border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs text-amber-800 dark:text-amber-300">
            Microsoft ist auf dieser Anlage gesperrt (nur lesend). Das Sekretariat liest dann Postfach und Kalender, entwirft
            aber keine Antworten — aufheben kann das nur ein Administrator.
          </p>
        )}
      </div>
      <div>
        <p className="mb-2 text-xs font-medium text-muted-foreground">1. Agenten wählen (der erste ist der Lead und sichtet das Postfach)</p>
        <div className="grid gap-2 sm:grid-cols-2">
          {agents.map((a) => {
            const platz = gewaehlt.indexOf(a.id);
            return (
              <button key={a.id} type="button" onClick={() => umschalten(a.id)}
                className={cn("flex items-center gap-2.5 rounded-xl border px-3 py-2 text-left text-sm transition-colors",
                  platz >= 0 ? "border-primary/40 bg-primary/[0.06]" : "border-foreground/[0.08] hover:bg-foreground/[0.03]")}>
                <AgentAvatar config={a.config} size="sm" />
                <span className="min-w-0 flex-1 truncate">{a.name}</span>
                {platz >= 0 && <span className="shrink-0 rounded bg-primary/15 px-1.5 py-0.5 text-[10px] font-medium text-primary">{ROLLEN[platz]}</span>}
              </button>
            );
          })}
        </div>
      </div>
      <div className="flex flex-wrap items-end gap-3">
        <label className="text-xs font-medium text-muted-foreground">
          2. Postfach sichten (werktags 7–18 Uhr)
          <select value={takt} onChange={(e) => setTakt(Number(e.target.value))}
            className="mt-1 block rounded-lg border border-foreground/[0.1] bg-background/80 px-2 py-1.5 text-sm text-foreground">
            <option value={15}>alle 15 Minuten</option>
            <option value={30}>alle 30 Minuten</option>
            <option value={60}>stündlich</option>
            <option value={120}>alle 2 Stunden</option>
          </select>
        </label>
        <label className="text-xs font-medium text-muted-foreground">
          3. Tagesbriefing (Mo–Fr)
          <input type="time" value={briefing} onChange={(e) => setBriefing(e.target.value)}
            className="mt-1 block rounded-lg border border-foreground/[0.1] bg-background/80 px-2 py-1.5 text-sm text-foreground" />
        </label>
        <button type="button" onClick={anlegen} disabled={!gewaehlt.length || laeuft}
          className="ml-auto inline-flex items-center gap-1.5 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50">
          {laeuft ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />} Sekretariat einrichten
        </button>
      </div>
      <p className="text-[11px] text-muted-foreground">
        Die Agenten bekommen dabei Microsoft 365 im Modus „Entwürfe + Freigabe“ und werden einmal neu gestartet.
        Dein Microsoft-Konto verbindest du unter Integrationen.
      </p>
    </div>
  );
}

function Erinnerungen({ liste, onNeu }: { liste: api.Erinnerung[]; onNeu: () => void }) {
  const toast = useToast();
  const [text, setText] = useState("");
  const [wann, setWann] = useState("");
  const anlegen = async () => {
    try {
      await api.erinnerungAnlegen(text.trim(), wann);
      setText("");
      setWann("");
      onNeu();
    } catch (e) {
      toast.error("Nicht angelegt", apiFehlertext(e));
    }
  };
  const jetzt = Date.now();
  return (
    <div className="space-y-2">
      {liste.length === 0 && <p className="text-xs text-muted-foreground">Keine offenen Erinnerungen.</p>}
      <ul className="space-y-1.5">
        {liste.map((e) => {
          const faellig = new Date(e.faellig).getTime() <= jetzt;
          return (
            <li key={e.id} className={cn("flex items-start gap-2 rounded-lg px-2.5 py-1.5 text-sm", faellig ? "bg-amber-500/10" : "bg-foreground/[0.03]")}>
              <div className="min-w-0 flex-1">
                <p className="break-words">{e.text}</p>
                <p className="text-[11px] text-muted-foreground">
                  {new Date(e.faellig).toLocaleString("de-DE", { weekday: "short", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}
                  {e.von !== "nutzer" ? " · vom Sekretariat" : ""}
                </p>
              </div>
              <button type="button" aria-label="Erledigt" title="Erledigt"
                onClick={async () => { await api.erinnerungErledigt(e.id).catch(() => {}); onNeu(); }}
                className="rounded p-1 text-muted-foreground hover:bg-emerald-500/10 hover:text-emerald-600"><Check className="h-3.5 w-3.5" /></button>
            </li>
          );
        })}
      </ul>
      <div className="flex flex-wrap gap-1.5 border-t border-foreground/[0.06] pt-2">
        <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Woran erinnern?"
          className="min-w-0 flex-1 rounded-lg border border-foreground/[0.1] bg-background/80 px-2 py-1 text-xs" />
        <input type="datetime-local" value={wann} onChange={(e) => setWann(e.target.value)} aria-label="Wann"
          className="rounded-lg border border-foreground/[0.1] bg-background/80 px-2 py-1 text-xs" />
        <button type="button" onClick={anlegen} disabled={!text.trim() || !wann} aria-label="Erinnerung anlegen"
          className="rounded-lg bg-primary px-2 py-1 text-xs text-primary-foreground disabled:opacity-50"><Plus className="h-3.5 w-3.5" /></button>
      </div>
    </div>
  );
}

export default function HeutePage() {
  const toast = useToast();
  const toastRef = useRef(toast);
  toastRef.current = toast;
  const { agents } = useAgents();
  const [daten, setDaten] = useState<api.Heute | null>(null);
  const [eingabe, setEingabe] = useState("");
  const [sendet, setSendet] = useState(false);
  const [entscheidet, setEntscheidet] = useState<string | null>(null);
  const agentById = new Map(agents.map((a) => [a.id, a]));

  const laden = useCallback(async () => {
    try {
      setDaten(await api.getHeute());
    } catch (e) {
      toastRef.current.error("Heute nicht geladen", apiFehlertext(e));
    }
  }, []);

  useEffect(() => {
    laden();
    return setVisibleInterval(laden, 30000);
  }, [laden]);

  const abschicken = async () => {
    const text = eingabe.trim();
    if (!text || !daten?.team) return;
    setSendet(true);
    try {
      if (/^merke\s*:/i.test(text)) {
        await api.sekretariatRegelAnlegen(text);
        toast.success("Regel gespeichert", "Alle Agenten des Sekretariats halten sich ab jetzt daran.");
      } else {
        await api.projektAuftrag(daten.team.id, { text });
        toast.success("Ans Sekretariat gegeben", "Der Lead kümmert sich darum.");
      }
      setEingabe("");
      await laden();
    } catch (e) {
      toast.error("Nicht angekommen", apiFehlertext(e));
    } finally {
      setSendet(false);
    }
  };

  const entscheiden = async (w: api.HeuteWartet, freigeben: boolean) => {
    setEntscheidet(w.approval_id);
    try {
      if (freigeben) await api.approveCommand(w.approval_id);
      else await api.denyCommand(w.approval_id, "Abgelehnt auf der Seite Heute");
      toast.success(freigeben ? (w.art === "mail_senden" ? "Gesendet" : "Freigegeben") : "Abgelehnt",
        freigeben && w.art === "mail_senden" ? "Die Mail ist raus." : undefined);
      await laden();
    } catch (e) {
      toast.error("Nicht entschieden", apiFehlertext(e));
    } finally {
      setEntscheidet(null);
    }
  };

  const datum = new Date().toLocaleDateString("de-DE", { weekday: "long", day: "numeric", month: "long" });

  return (
    <div>
      <Header title="Heute" subtitle={`${datum} — dein Sekretariat`} />
      <div className="space-y-5 p-4 sm:p-6">
        {!daten ? (
          <div className="flex justify-center py-16 text-muted-foreground"><Loader2 className="h-5 w-5 animate-spin" /></div>
        ) : !daten.eingerichtet ? (
          <Einrichten onFertig={laden} entwurfFrei={daten.m365_entwurf_frei} />
        ) : (
          <>
            {!daten.m365_verbunden && (
              <p className="rounded-xl border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs text-amber-800 dark:text-amber-300">
                Dein Microsoft-Konto ist nicht verbunden — ohne es sieht das Sekretariat weder Postfach noch Kalender.{" "}
                <Link href="/integrations" className="underline">Unter Integrationen verbinden</Link>
              </p>
            )}

            <div className="flex items-end gap-2 rounded-2xl border border-foreground/[0.08] bg-card p-3">
              <textarea value={eingabe} onChange={(e) => setEingabe(e.target.value)} rows={2}
                onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) abschicken(); }}
                placeholder="Was soll das Sekretariat tun? — „Merke: …“ wird eine feste Regel, z. B. „Merke: Mails von der Steuerkanzlei immer sofort an mich“."
                className="min-w-0 flex-1 resize-none rounded-lg border border-foreground/[0.1] bg-background/80 px-3 py-2 text-sm outline-none focus:border-primary/50" />
              <button type="button" onClick={abschicken} disabled={sendet || !eingabe.trim()} aria-label="Abschicken"
                className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50">
                {sendet ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
              </button>
            </div>

            <div className="grid gap-4 lg:grid-cols-2">
              <Kasten titel="Wartet auf dich" icon={ShieldCheck}
                rechts={daten.wartet.length > 0 ? <span className="rounded-full bg-amber-500/15 px-2 text-xs font-semibold text-amber-700 dark:text-amber-300">{daten.wartet.length}</span> : null}>
                {daten.wartet.length === 0 ? (
                  <p className="text-xs text-muted-foreground">Nichts offen. Antworten, die das Sekretariat entwirft, erscheinen hier — gesendet wird erst, wenn du freigibst.</p>
                ) : (
                  <ul className="space-y-3">
                    {daten.wartet.map((w) => (
                      <li key={w.approval_id} className="space-y-2 rounded-xl bg-foreground/[0.03] p-3">
                        <p className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
                          {w.art === "mail_senden" ? <Mail className="h-3 w-3" /> : <Bell className="h-3 w-3" />}
                          {agentById.get(w.agent_id)?.name ?? "Agent"} · {w.erstellt ? timeAgo(w.erstellt) : ""}
                        </p>
                        {w.view ? (
                          <AgentView name={w.view.name} agentId={w.agent_id} data={w.view.data || {}} antworten={() => {}} />
                        ) : (
                          <p className="text-sm">{w.frage}</p>
                        )}
                        <div className="flex gap-2">
                          <button type="button" disabled={entscheidet === w.approval_id} onClick={() => entscheiden(w, true)}
                            className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-600/90 disabled:opacity-50">
                            {entscheidet === w.approval_id ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Send className="h-3.5 w-3.5" />}
                            {w.art === "mail_senden" ? "Freigeben und senden" : "Freigeben"}
                          </button>
                          <button type="button" disabled={entscheidet === w.approval_id} onClick={() => entscheiden(w, false)}
                            className="inline-flex items-center gap-1.5 rounded-lg border border-foreground/[0.1] px-3 py-1.5 text-xs hover:bg-foreground/[0.04] disabled:opacity-50">
                            <X className="h-3.5 w-3.5" /> Ablehnen
                          </button>
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </Kasten>

              <Kasten titel="Termine heute" icon={CalendarDays}>
                {daten.termine === null ? (
                  <p className="text-xs text-muted-foreground">Kalender nicht erreichbar.</p>
                ) : daten.termine.length === 0 ? (
                  <p className="text-xs text-muted-foreground">Heute keine Termine.</p>
                ) : (
                  <ul className="space-y-1.5">
                    {daten.termine.map((t, i) => (
                      <li key={i} className="flex items-baseline gap-3 text-sm">
                        <span className="w-24 shrink-0 tabular-nums text-muted-foreground">
                          {t.ganztags ? "ganztags" : `${uhrzeit(t.beginn)}–${uhrzeit(t.ende)}`}
                        </span>
                        <span className="min-w-0 flex-1 truncate" title={t.betreff}>{t.betreff}</span>
                        {t.online ? <Video className="h-3.5 w-3.5 shrink-0 text-muted-foreground" /> : t.ort ? <span className="truncate text-[11px] text-muted-foreground">{t.ort}</span> : null}
                      </li>
                    ))}
                  </ul>
                )}
              </Kasten>

              <Kasten titel="Posteingang" icon={Inbox}
                rechts={daten.ungelesen ? <span className="text-[11px] text-muted-foreground">{daten.ungelesen.length} ungelesen</span> : null}>
                {daten.ungelesen === null ? (
                  <p className="text-xs text-muted-foreground">Postfach nicht erreichbar.</p>
                ) : daten.ungelesen.length === 0 ? (
                  <p className="text-xs text-muted-foreground">Alles gelesen.</p>
                ) : (
                  <ul className="space-y-1.5">
                    {daten.ungelesen.map((m, i) => (
                      <li key={i} className="flex items-start gap-2 text-sm">
                        <Star className={cn("mt-0.5 h-3.5 w-3.5 shrink-0", m.wichtig ? "fill-amber-400 text-amber-500" : "text-transparent")} />
                        <div className="min-w-0 flex-1">
                          <p className="truncate"><span className="font-medium">{m.von}</span> — {m.betreff}</p>
                          <p className="truncate text-[11px] text-muted-foreground">{m.vorschau}</p>
                        </div>
                        <span className="shrink-0 text-[11px] text-muted-foreground">{m.eingang ? timeAgo(m.eingang) : ""}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </Kasten>

              <Kasten titel="Erinnerungen" icon={Bell}>
                <Erinnerungen liste={daten.erinnerungen} onNeu={laden} />
              </Kasten>

              <Kasten titel="Tagesbriefing" icon={Sun}>
                {daten.briefing ? (
                  <div className="max-h-80 overflow-y-auto text-sm"><MarkdownContent content={daten.briefing.text} ohneBilder /></div>
                ) : (
                  <p className="text-xs text-muted-foreground">Das Briefing von heute liegt noch nicht vor — es kommt zur eingestellten Uhrzeit.</p>
                )}
              </Kasten>

              <Kasten titel="Regeln" icon={ShieldCheck}>
                {daten.regeln.length === 0 ? (
                  <p className="text-xs text-muted-foreground">Noch keine. Schreib oben „Merke: …“ — daran halten sich ab dann alle Agenten des Sekretariats.</p>
                ) : (
                  <ul className="space-y-1.5">
                    {daten.regeln.map((r) => (
                      <li key={r.schluessel} className="flex items-start gap-2 rounded-lg bg-foreground/[0.03] px-2.5 py-1.5 text-sm">
                        <span className="min-w-0 flex-1 break-words">{r.text}</span>
                        <button type="button" aria-label="Regel löschen" title="Regel löschen"
                          onClick={async () => { await api.sekretariatRegelLoeschen(r.schluessel).catch(() => {}); laden(); }}
                          className="rounded p-1 text-muted-foreground hover:bg-red-500/10 hover:text-red-500"><Trash2 className="h-3.5 w-3.5" /></button>
                      </li>
                    ))}
                  </ul>
                )}
              </Kasten>
            </div>

            {daten.team && (
              <p className="text-[11px] text-muted-foreground">
                Das Sekretariat ist ein Team wie ein Projekt: <Link href={`/projekte/${daten.team.id}`} className="underline">Raum, Board und Gespräch</Link>
                {" "}· Kosten unter <Link href={`/finanzen?projekt=${daten.team.id}`} className="underline">Finanzen</Link>
              </p>
            )}
          </>
        )}
      </div>
    </div>
  );
}

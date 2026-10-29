"use client";

/**
 * Heute: das Sekretariat (orchestrator: core/sekretariat). Termine von heute und morgen
 * und ungelesene Mails direkt aus Microsoft 365 (fehlt eine Quelle, steht der Grund da),
 * der Posteingang in Gruppen und mit Aktionen (Öffnen, Antwort entwerfen, Erinnern,
 * Erledigt), Entwürfe, die auf Freigabe warten (bearbeiten; Freigeben sendet die Mail —
 * vom Server, core/mail_freigabe; Ablehnen mit Begründung ergibt einen Regel-Vorschlag),
 * Erinnerungen mit „Später“ und Fristen aus Mails, feste Regeln („Merke: …“), das
 * Tagesbriefing und das Protokoll des Tages. Ohne Sekretariat: der Einrichten-Assistent.
 */

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlarmClock, Archive, Bell, BellPlus, CalendarDays, Check, ChevronRight, Clock, ExternalLink, Inbox, Lightbulb, Loader2,
  Lock, Mail, MessageCircleQuestionMark, Pencil, Plus, RefreshCw, Reply, Save, ScrollText, Send, ShieldCheck, Sparkles,
  Star, Sun, Trash2, Video, X,
} from "lucide-react";
import { Header } from "@/components/layout/header";
import { AgentView } from "@/components/agents/agent-views";
import { AgentAvatar } from "@/components/agents/agent-avatar";
import { MarkdownContent } from "@/components/ui/markdown-content";
import { useConfirm, useToast } from "@/components/ui/dialog-provider";
import { useAgents } from "@/hooks/use-agents";
import * as api from "@/lib/api";
import { apiFehlertext } from "@/lib/api-fehler";
import { setVisibleInterval } from "@/lib/visible-interval";
import { cn, timeAgo } from "@/lib/utils";

const ROLLEN = ["Sichten (Lead)", "Entwerfen", "Prüfen", "Ablegen"];
const MAIL_ART = "mail_senden";

function uhrzeit(iso: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
}

function datumKurz(iso: string): string {
  return new Date(iso).toLocaleString("de-DE", { weekday: "short", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

function lokalesDatum(d: Date): string {
  const zwei = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${zwei(d.getMonth() + 1)}-${zwei(d.getDate())}`;
}

/** Zeitpunkt für eine neue Erinnerung. Ohne Zeitzone gilt beim Server die der Anlage. */
function zeitpunkt(wann: api.SpaeterOption): string {
  const jetzt = new Date();
  if (wann === "1h") return new Date(jetzt.getTime() + 3600_000).toISOString();
  const tag = new Date(jetzt);
  if (wann === "morgen") tag.setDate(tag.getDate() + 1);
  else tag.setDate(tag.getDate() + (((8 - tag.getDay()) % 7) || 7)); // nächster Montag
  return `${lokalesDatum(tag)}T09:00`;
}

const SPAETER: { wert: api.SpaeterOption; label: string }[] = [
  { wert: "1h", label: "in 1 Std." },
  { wert: "morgen", label: "morgen 9:00" },
  { wert: "woche", label: "nächste Woche" },
];

function Kasten({ titel, icon: Icon, rechts, children, className }: {
  titel: string; icon: React.ElementType; rechts?: React.ReactNode; children: React.ReactNode; className?: string;
}) {
  return (
    <section className={cn("min-w-0 rounded-2xl border border-foreground/[0.08] bg-card p-4", className)}>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h3 className="inline-flex items-center gap-1.5 text-sm font-semibold"><Icon className="h-4 w-4 text-muted-foreground" /> {titel}</h3>
        {rechts}
      </div>
      {children}
    </section>
  );
}

function KleinerKnopf({ onClick, children, disabled, titel, gefahr }: {
  onClick: () => void; children: React.ReactNode; disabled?: boolean; titel?: string; gefahr?: boolean;
}) {
  return (
    <button type="button" onClick={onClick} disabled={disabled} title={titel}
      className={cn("inline-flex items-center gap-1 rounded-md border border-foreground/[0.08] px-2 py-0.5 text-[11px] text-muted-foreground transition-colors hover:bg-foreground/[0.05] hover:text-foreground disabled:opacity-50",
        gefahr && "hover:bg-red-500/10 hover:text-red-600")}>
      {children}
    </button>
  );
}

/** Zeile „Grund + Erneut versuchen (+ Verbinden)“, wenn Termine oder Posteingang fehlen. */
function QuellenFehler({ fehler, onErneut }: { fehler: api.HeuteQuellenFehler; onErneut: () => void }) {
  const verbinden = fehler.code === "nicht_verbunden" || fehler.code === "abgelaufen" || fehler.code === "berechtigung";
  return (
    <div className="space-y-2 rounded-lg border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs text-amber-800 dark:text-amber-300">
      <p>{fehler.grund}</p>
      <div className="flex flex-wrap gap-2">
        <button type="button" onClick={onErneut}
          className="inline-flex items-center gap-1 rounded-md border border-amber-500/30 px-2 py-1 hover:bg-amber-500/10">
          <RefreshCw className="h-3 w-3" /> Erneut versuchen
        </button>
        {verbinden && (
          <Link href="/integrations" className="inline-flex items-center gap-1 rounded-md bg-amber-600 px-2 py-1 text-white hover:bg-amber-600/90">
            <ExternalLink className="h-3 w-3" /> Verbinden
          </Link>
        )}
      </div>
    </div>
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
        Dein Microsoft-Konto verbindest du unter Verbindungen.
      </p>
    </div>
  );
}

// ─── Erinnerungen ───────────────────────────────────────────────

function Erinnerungen({ liste, fristen, onNeu }: {
  liste: api.Erinnerung[]; fristen: api.Heute["frist_vorschlaege"]; onNeu: () => void;
}) {
  const toast = useToast();
  const [text, setText] = useState("");
  const [wann, setWann] = useState("");
  const [spaeterOffen, setSpaeterOffen] = useState<number | null>(null);
  const [beschaeftigt, setBeschaeftigt] = useState<string | null>(null);

  const machen = async (schluessel: string, aufruf: () => Promise<unknown>, fehlerTitel: string) => {
    setBeschaeftigt(schluessel);
    try {
      await aufruf();
      onNeu();
    } catch (e) {
      toast.error(fehlerTitel, apiFehlertext(e));
    } finally {
      setBeschaeftigt(null);
    }
  };

  const anlegen = () => machen("neu", async () => {
    await api.erinnerungAnlegen(text.trim(), wann);
    setText("");
    setWann("");
  }, "Nicht angelegt");

  const jetzt = Date.now();
  return (
    <div className="space-y-2">
      {liste.length === 0 && <p className="text-xs text-muted-foreground">Keine offenen Erinnerungen.</p>}
      <ul className="space-y-1.5">
        {liste.map((e) => {
          const faellig = new Date(e.faellig).getTime() <= jetzt;
          return (
            <li key={e.id} className={cn("rounded-lg px-2.5 py-1.5 text-sm", faellig ? "bg-amber-500/10" : "bg-foreground/[0.03]")}>
              <div className="flex items-start gap-2">
                <div className="min-w-0 flex-1">
                  <p className="break-words">{e.text}</p>
                  <p className="text-[11px] text-muted-foreground">
                    {datumKurz(e.faellig)}
                    {e.von !== "nutzer" ? " · vom Sekretariat" : ""}
                    {e.bezug?.link && (
                      <> · <a href={e.bezug.link} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-0.5 underline">
                        Mail öffnen <ExternalLink className="h-2.5 w-2.5" /></a></>
                    )}
                  </p>
                </div>
                <button type="button" aria-label="Später" title="Später"
                  onClick={() => setSpaeterOffen(spaeterOffen === e.id ? null : e.id)}
                  className="rounded p-1 text-muted-foreground hover:bg-foreground/[0.06] hover:text-foreground"><Clock className="h-3.5 w-3.5" /></button>
                <button type="button" aria-label="Erledigt" title="Erledigt" disabled={beschaeftigt === `e${e.id}`}
                  onClick={() => machen(`e${e.id}`, () => api.erinnerungErledigt(e.id), "Nicht abgehakt")}
                  className="rounded p-1 text-muted-foreground hover:bg-emerald-500/10 hover:text-emerald-600"><Check className="h-3.5 w-3.5" /></button>
              </div>
              {spaeterOffen === e.id && (
                <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                  <span className="text-[11px] text-muted-foreground">Später:</span>
                  {SPAETER.map((o) => (
                    <KleinerKnopf key={o.wert} disabled={beschaeftigt === `s${e.id}`}
                      onClick={() => machen(`s${e.id}`, async () => { await api.erinnerungSpaeter(e.id, o.wert); setSpaeterOffen(null); }, "Nicht verschoben")}>
                      {o.label}
                    </KleinerKnopf>
                  ))}
                </div>
              )}
            </li>
          );
        })}
      </ul>
      {fristen.length > 0 && (
        <div className="space-y-1 rounded-lg border border-dashed border-foreground/[0.12] p-2">
          <p className="inline-flex items-center gap-1 text-[11px] font-medium text-muted-foreground"><CalendarDays className="h-3 w-3" /> Frist aus Mail übernehmen</p>
          <ul className="space-y-1">
            {fristen.map((f) => (
              <li key={f.mail_id} className="flex items-center gap-2 text-xs">
                <span className="min-w-0 flex-1 truncate" title={f.betreff}>
                  <span className="font-medium">{new Date(`${f.frist}T09:00`).toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit" })}</span>{" "}
                  „{f.betreff}“ <span className="text-muted-foreground">von {f.von}</span>
                </span>
                <KleinerKnopf disabled={beschaeftigt === `f${f.mail_id}`}
                  onClick={() => machen(`f${f.mail_id}`, () => api.erinnerungAnlegen(`Frist: „${f.betreff}“ (von ${f.von})`, `${f.frist}T09:00`, f.mail_id), "Nicht übernommen")}>
                  <Plus className="h-3 w-3" /> Übernehmen
                </KleinerKnopf>
              </li>
            ))}
          </ul>
        </div>
      )}
      <div className="flex flex-wrap gap-1.5 border-t border-foreground/[0.06] pt-2">
        <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Woran erinnern?"
          className="min-w-0 flex-1 rounded-lg border border-foreground/[0.1] bg-background/80 px-2 py-1 text-xs" />
        <input type="datetime-local" value={wann} onChange={(e) => setWann(e.target.value)} aria-label="Wann"
          className="rounded-lg border border-foreground/[0.1] bg-background/80 px-2 py-1 text-xs" />
        <button type="button" onClick={anlegen} disabled={!text.trim() || !wann || beschaeftigt === "neu"} aria-label="Erinnerung anlegen"
          className="rounded-lg bg-primary px-2 py-1 text-xs text-primary-foreground disabled:opacity-50"><Plus className="h-3.5 w-3.5" /></button>
      </div>
    </div>
  );
}

// ─── Wartet auf dich ────────────────────────────────────────────

function EntwurfBearbeiten({ w, onFertig, onAbbrechen }: { w: api.HeuteWartet; onFertig: () => void; onAbbrechen: () => void }) {
  const toast = useToast();
  const daten = w.view?.data || {};
  const liste = (wert: unknown) => (Array.isArray(wert) ? wert.map(String).filter(Boolean) : []);
  const [an, setAn] = useState(liste(daten.an).join(", "));
  const [cc, setCc] = useState(liste(daten.cc).join(", "));
  const [betreff, setBetreff] = useState(String(daten.betreff ?? ""));
  const [text, setText] = useState(String(daten.text ?? ""));
  const [speichert, setSpeichert] = useState(false);
  const adressen = (s: string) => s.split(/[,;\s]+/).map((x) => x.trim()).filter(Boolean);

  const speichern = async () => {
    setSpeichert(true);
    try {
      await api.sekretariatEntwurfAendern(w.approval_id, { an: adressen(an), cc: adressen(cc), betreff, text });
      toast.success("Entwurf gespeichert", "Freigeben sendet jetzt genau diesen Stand.");
      onFertig();
    } catch (e) {
      toast.error("Nicht gespeichert", apiFehlertext(e));
    } finally {
      setSpeichert(false);
    }
  };

  const feld = "w-full rounded-lg border border-foreground/[0.1] bg-background/80 px-2 py-1 text-xs";
  return (
    <div className="space-y-1.5 rounded-xl border border-primary/30 bg-background/60 p-2.5">
      <label className="block text-[11px] text-muted-foreground">An<input value={an} onChange={(e) => setAn(e.target.value)} className={feld} /></label>
      <label className="block text-[11px] text-muted-foreground">Cc<input value={cc} onChange={(e) => setCc(e.target.value)} className={feld} /></label>
      <label className="block text-[11px] text-muted-foreground">Betreff<input value={betreff} onChange={(e) => setBetreff(e.target.value)} className={feld} /></label>
      <label className="block text-[11px] text-muted-foreground">Text
        <textarea value={text} onChange={(e) => setText(e.target.value)} rows={8} className={cn(feld, "resize-y font-[inherit] text-[13px]")} />
      </label>
      <div className="flex gap-2">
        <button type="button" onClick={speichern} disabled={speichert || !adressen(an).length}
          className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground disabled:opacity-50">
          {speichert ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />} Speichern
        </button>
        <button type="button" onClick={onAbbrechen} className="rounded-lg border border-foreground/[0.1] px-3 py-1.5 text-xs">Abbrechen</button>
      </div>
    </div>
  );
}

/** „Nachfassen, wenn keine Antwort bis …“ — Erinnerung aus einer gesendeten Freigabe. */
function Nachfassen({ approvalId, onFertig, onSchliessen }: {
  approvalId: string; onFertig: () => void; onSchliessen: () => void;
}) {
  const toast = useToast();
  const [datum, setDatum] = useState("");
  const [laeuft, setLaeuft] = useState(false);
  const anlegen = async (faellig: string) => {
    setLaeuft(true);
    try {
      await api.sekretariatNachfassen(approvalId, faellig);
      toast.success("Nachfassen vorgemerkt", "Die Erinnerung steht unter Erinnerungen.");
      onFertig();
    } catch (e) {
      toast.error("Nicht angelegt", apiFehlertext(e));
    } finally {
      setLaeuft(false);
    }
  };
  const inTagen = (n: number) => {
    const d = new Date();
    d.setDate(d.getDate() + n);
    return `${lokalesDatum(d)}T09:00`;
  };
  return (
    <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
      <span className="text-muted-foreground">Nachfassen, wenn keine Antwort bis</span>
      <KleinerKnopf disabled={laeuft} onClick={() => anlegen(inTagen(3))}>in 3 Tagen</KleinerKnopf>
      <KleinerKnopf disabled={laeuft} onClick={() => anlegen(zeitpunkt("woche"))}>nächste Woche</KleinerKnopf>
      <input type="date" value={datum} onChange={(e) => setDatum(e.target.value)} aria-label="Datum"
        className="rounded-md border border-foreground/[0.1] bg-background/80 px-1.5 py-0.5" />
      <KleinerKnopf disabled={laeuft || !datum} onClick={() => anlegen(`${datum}T09:00`)}><AlarmClock className="h-3 w-3" /> Vormerken</KleinerKnopf>
      <button type="button" aria-label="Schließen" onClick={onSchliessen} className="rounded p-0.5 text-muted-foreground hover:text-foreground"><X className="h-3 w-3" /></button>
    </div>
  );
}

function WartetEintrag({ w, agentName, onNeu, onGesendet }: {
  w: api.HeuteWartet; agentName: string; onNeu: () => void; onGesendet: (approvalId: string, betreff: string) => void;
}) {
  const toast = useToast();
  const [laeuft, setLaeuft] = useState(false);
  const [bearbeiten, setBearbeiten] = useState(false);
  const [ablehnen, setAblehnen] = useState(false);
  const [grund, setGrund] = useState("");
  const istMail = w.art === MAIL_ART;
  const betreff = String(w.view?.data?.betreff ?? "Mail");

  const freigeben = async () => {
    setLaeuft(true);
    try {
      await api.approveCommand(w.approval_id);
      toast.success(istMail ? "Gesendet" : "Freigegeben", istMail ? "Die Mail ist raus." : undefined);
      if (istMail) onGesendet(w.approval_id, betreff);
      onNeu();
    } catch (e) {
      toast.error("Nicht entschieden", apiFehlertext(e));
    } finally {
      setLaeuft(false);
    }
  };

  const ablehnenJetzt = async () => {
    setLaeuft(true);
    try {
      const r = await api.sekretariatAblehnen(w.approval_id, grund.trim() || undefined);
      toast.success("Abgelehnt", r.regel_vorschlag
        ? "Die Begründung geht an den Agenten. Unter Regeln steht ein Vorschlag daraus — übernehmen musst du ihn selbst."
        : undefined);
      setAblehnen(false);
      onNeu();
    } catch (e) {
      toast.error("Nicht abgelehnt", apiFehlertext(e));
    } finally {
      setLaeuft(false);
    }
  };

  return (
    <li className={cn("space-y-2 rounded-xl p-3", istMail ? "bg-foreground/[0.03]" : "border border-sky-500/25 bg-sky-500/[0.05]")}>
      <p className="flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
        {istMail ? (
          <span className="inline-flex items-center gap-1 rounded bg-emerald-500/15 px-1.5 py-0.5 font-medium text-emerald-700 dark:text-emerald-300">
            <Mail className="h-3 w-3" /> Mail-Freigabe
          </span>
        ) : (
          <span className="inline-flex items-center gap-1 rounded bg-sky-500/15 px-1.5 py-0.5 font-medium text-sky-700 dark:text-sky-300">
            <MessageCircleQuestionMark className="h-3 w-3" /> Rückfrage
          </span>
        )}
        {agentName} · {w.erstellt ? timeAgo(w.erstellt) : ""}
        {w.bearbeitet && <span className="rounded bg-primary/10 px-1 text-primary">von dir bearbeitet</span>}
      </p>
      {bearbeiten ? (
        <EntwurfBearbeiten w={w} onAbbrechen={() => setBearbeiten(false)} onFertig={() => { setBearbeiten(false); onNeu(); }} />
      ) : w.view ? (
        <AgentView name={w.view.name} agentId={w.agent_id} data={w.view.data || {}} antworten={() => {}} />
      ) : (
        <p className="text-sm">{w.frage}</p>
      )}
      {ablehnen ? (
        <div className="space-y-1.5">
          <textarea value={grund} onChange={(e) => setGrund(e.target.value)} rows={2} autoFocus
            placeholder="Warum? (optional) — geht an den Agenten zurück und wird ein Regel-Vorschlag"
            className="w-full resize-none rounded-lg border border-foreground/[0.1] bg-background/80 px-2 py-1 text-xs" />
          <div className="flex gap-2">
            <button type="button" disabled={laeuft} onClick={ablehnenJetzt}
              className="inline-flex items-center gap-1.5 rounded-lg bg-red-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-red-600/90 disabled:opacity-50">
              {laeuft ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <X className="h-3.5 w-3.5" />} Ablehnen
            </button>
            <button type="button" onClick={() => setAblehnen(false)} className="rounded-lg border border-foreground/[0.1] px-3 py-1.5 text-xs">Abbrechen</button>
          </div>
        </div>
      ) : !bearbeiten && (
        <div className="flex flex-wrap gap-2">
          <button type="button" disabled={laeuft} onClick={freigeben}
            className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-600/90 disabled:opacity-50">
            {laeuft ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Send className="h-3.5 w-3.5" />}
            {istMail ? "Freigeben und senden" : "Freigeben"}
          </button>
          {istMail && w.bearbeitbar && (
            <button type="button" disabled={laeuft} onClick={() => setBearbeiten(true)}
              className="inline-flex items-center gap-1.5 rounded-lg border border-foreground/[0.1] px-3 py-1.5 text-xs hover:bg-foreground/[0.04] disabled:opacity-50">
              <Pencil className="h-3.5 w-3.5" /> Bearbeiten
            </button>
          )}
          <button type="button" disabled={laeuft} onClick={() => setAblehnen(true)}
            className="inline-flex items-center gap-1.5 rounded-lg border border-foreground/[0.1] px-3 py-1.5 text-xs hover:bg-foreground/[0.04] disabled:opacity-50">
            <X className="h-3.5 w-3.5" /> Ablehnen
          </button>
        </div>
      )}
    </li>
  );
}

// ─── Termine ────────────────────────────────────────────────────

function TerminListe({ termine }: { termine: api.HeuteTermin[] }) {
  return (
    <ul className="space-y-1.5">
      {termine.map((t, i) => (
        <li key={i} className="flex items-baseline gap-3 text-sm">
          <span className="w-24 shrink-0 tabular-nums text-muted-foreground">
            {t.ganztags ? "ganztags" : `${uhrzeit(t.beginn)}–${uhrzeit(t.ende)}`}
          </span>
          <span className="min-w-0 flex-1 truncate" title={t.betreff}>
            {t.link ? <a href={t.link} target="_blank" rel="noopener noreferrer" className="hover:underline">{t.betreff}</a> : t.betreff}
          </span>
          {t.online ? <Video className="h-3.5 w-3.5 shrink-0 text-muted-foreground" /> : t.ort ? <span className="truncate text-[11px] text-muted-foreground">{t.ort}</span> : null}
        </li>
      ))}
    </ul>
  );
}

function Termine({ daten, onErneut }: { daten: api.Heute; onErneut: () => void }) {
  const [morgenOffen, setMorgenOffen] = useState(false);
  if (daten.fehler.termine) return <QuellenFehler fehler={daten.fehler.termine} onErneut={onErneut} />;
  const heute = daten.termine || [];
  const morgen = daten.termine_morgen || [];
  return (
    <div className="space-y-2">
      {heute.length === 0 ? <p className="text-xs text-muted-foreground">Heute keine Termine.</p> : <TerminListe termine={heute} />}
      <div className="border-t border-foreground/[0.06] pt-2">
        <button type="button" onClick={() => setMorgenOffen(!morgenOffen)} aria-expanded={morgenOffen}
          className="inline-flex items-center gap-1 text-xs font-medium text-muted-foreground hover:text-foreground">
          <ChevronRight className={cn("h-3.5 w-3.5 transition-transform", morgenOffen && "rotate-90")} /> Morgen ({morgen.length})
        </button>
        {morgenOffen && (
          <div className="mt-1.5">{morgen.length === 0 ? <p className="text-xs text-muted-foreground">Morgen keine Termine.</p> : <TerminListe termine={morgen} />}</div>
        )}
      </div>
    </div>
  );
}

// ─── Posteingang ────────────────────────────────────────────────

type Ordner = { id: string; name: string };

function MailZeile({ m, ordnerLaden, onWeg, onNeu }: {
  m: api.HeuteMail; ordnerLaden: () => Promise<Ordner[]>; onWeg: (id: string) => void; onNeu: () => void;
}) {
  const toast = useToast();
  const [offen, setOffen] = useState<"antwort" | "erinnern" | "erledigt" | null>(null);
  const [hinweis, setHinweis] = useState("");
  const [ordner, setOrdner] = useState<Ordner[] | null>(null);
  const [ziel, setZiel] = useState("");
  const [laeuft, setLaeuft] = useState(false);
  const id = m.id;

  const machen = async (aufruf: () => Promise<unknown>, ok: string, fehlerTitel: string, okText?: string) => {
    setLaeuft(true);
    try {
      await aufruf();
      toast.success(ok, okText);
      setOffen(null);
      onNeu();
    } catch (e) {
      toast.error(fehlerTitel, apiFehlertext(e));
    } finally {
      setLaeuft(false);
    }
  };

  const umschalten = async (was: "antwort" | "erinnern" | "erledigt") => {
    setOffen(offen === was ? null : was);
    if (was === "erledigt" && ordner === null) {
      try { setOrdner(await ordnerLaden()); } catch { setOrdner([]); }
    }
  };

  const erledigt = (ordnerId: string | null, name: string | null) => {
    if (!id) return;
    void machen(async () => { await api.sekretariatMailErledigt(id, ordnerId, name); onWeg(id); },
      "Erledigt", "Nicht erledigt", ordnerId ? `Abgelegt in „${name}“.` : "Als gelesen markiert.");
  };

  return (
    <li className="rounded-lg px-1 py-1 text-sm hover:bg-foreground/[0.02]">
      <div className="flex items-start gap-2">
        <Star className={cn("mt-0.5 h-3.5 w-3.5 shrink-0", m.wichtig ? "fill-amber-400 text-amber-500" : "text-transparent")} />
        <div className="min-w-0 flex-1" title={m.gruende.length ? `Eingeordnet: ${m.gruende.join(", ")}` : undefined}>
          <p className="truncate"><span className="font-medium">{m.von}</span> — {m.betreff}</p>
          <p className="truncate text-[11px] text-muted-foreground">{m.vorschau}</p>
        </div>
        <span className="shrink-0 text-[11px] text-muted-foreground">{m.eingang ? timeAgo(m.eingang) : ""}</span>
      </div>
      {id && (
        <div className="ml-5 mt-1 flex flex-wrap gap-1">
          {m.link && (
            <a href={m.link} target="_blank" rel="noopener noreferrer"
              className="inline-flex items-center gap-1 rounded-md border border-foreground/[0.08] px-2 py-0.5 text-[11px] text-muted-foreground hover:bg-foreground/[0.05] hover:text-foreground">
              <ExternalLink className="h-3 w-3" /> Öffnen
            </a>
          )}
          <KleinerKnopf onClick={() => umschalten("antwort")}><Reply className="h-3 w-3" /> Antwort entwerfen</KleinerKnopf>
          <KleinerKnopf onClick={() => umschalten("erinnern")}><BellPlus className="h-3 w-3" /> Erinnern</KleinerKnopf>
          <KleinerKnopf onClick={() => umschalten("erledigt")}><Archive className="h-3 w-3" /> Erledigt</KleinerKnopf>
        </div>
      )}
      {id && offen === "antwort" && (
        <div className="ml-5 mt-1.5 flex flex-wrap gap-1.5">
          <input value={hinweis} onChange={(e) => setHinweis(e.target.value)} autoFocus
            placeholder="Hinweis fürs Sekretariat (optional), z. B. „zusagen, Termin nächste Woche vorschlagen“"
            className="min-w-0 flex-1 rounded-lg border border-foreground/[0.1] bg-background/80 px-2 py-1 text-xs" />
          <button type="button" disabled={laeuft}
            onClick={() => machen(() => api.sekretariatAntwortEntwerfen(id, hinweis.trim()), "Ans Sekretariat gegeben",
              "Nicht angekommen", "Der Entwurf erscheint unter „Wartet auf dich“.")}
            className="inline-flex items-center gap-1 rounded-lg bg-primary px-2.5 py-1 text-xs text-primary-foreground disabled:opacity-50">
            {laeuft ? <Loader2 className="h-3 w-3 animate-spin" /> : <Send className="h-3 w-3" />} Auftrag geben
          </button>
        </div>
      )}
      {id && offen === "erinnern" && (
        <div className="ml-5 mt-1.5 flex flex-wrap items-center gap-1.5">
          <span className="text-[11px] text-muted-foreground">Erinnern</span>
          {SPAETER.map((o) => (
            <KleinerKnopf key={o.wert} disabled={laeuft}
              onClick={() => machen(() => api.erinnerungAnlegen(`Mail „${m.betreff}“ von ${m.von}`, zeitpunkt(o.wert), id),
                "Erinnerung angelegt", "Nicht angelegt")}>
              {o.label}
            </KleinerKnopf>
          ))}
        </div>
      )}
      {id && offen === "erledigt" && (
        <div className="ml-5 mt-1.5 flex flex-wrap items-center gap-1.5">
          <KleinerKnopf disabled={laeuft} onClick={() => erledigt(null, null)}><Check className="h-3 w-3" /> Nur als gelesen</KleinerKnopf>
          <KleinerKnopf disabled={laeuft} onClick={() => erledigt("archive", "Archiv")}><Archive className="h-3 w-3" /> Archivieren</KleinerKnopf>
          {ordner === null ? (
            <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" />
          ) : ordner.length > 0 && (
            <>
              <select value={ziel} onChange={(e) => setZiel(e.target.value)} aria-label="Ordner"
                className="max-w-[12rem] rounded-md border border-foreground/[0.1] bg-background/80 px-1.5 py-0.5 text-[11px]">
                <option value="">In Ordner …</option>
                {ordner.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
              </select>
              <KleinerKnopf disabled={laeuft || !ziel} onClick={() => erledigt(ziel, ordner.find((o) => o.id === ziel)?.name ?? null)}>
                Ablegen
              </KleinerKnopf>
            </>
          )}
        </div>
      )}
    </li>
  );
}

const GRUPPEN: { schluessel: api.HeuteMailGruppe; titel: string; zu: boolean }[] = [
  { schluessel: "braucht_dich", titel: "Braucht dich", zu: false },
  { schluessel: "weitere", titel: "Weitere", zu: false },
  { schluessel: "automatisch", titel: "Automatisch", zu: true },
];

function Posteingang({ daten, onNeu, onErneut }: { daten: api.Heute; onNeu: () => void; onErneut: () => void }) {
  const [zu, setZu] = useState<Record<string, boolean>>({ automatisch: true });
  const [weg, setWeg] = useState<Set<string>>(new Set());
  const ordnerRef = useRef<Promise<Ordner[]> | null>(null);
  const ordnerLaden = () => {
    if (!ordnerRef.current) {
      ordnerRef.current = api.sekretariatMailOrdner().then((r) => r.ordner).catch((e) => { ordnerRef.current = null; throw e; });
    }
    return ordnerRef.current;
  };

  if (daten.fehler.posteingang) return <QuellenFehler fehler={daten.fehler.posteingang} onErneut={onErneut} />;
  const mails = (daten.ungelesen || []).filter((m) => !m.id || !weg.has(m.id));
  if (mails.length === 0) return <p className="text-xs text-muted-foreground">Alles gelesen.</p>;
  return (
    <div className="space-y-2">
      {GRUPPEN.map((g) => {
        const liste = mails.filter((m) => m.gruppe === g.schluessel);
        if (!liste.length) return null;
        const geschlossen = zu[g.schluessel] ?? g.zu;
        return (
          <div key={g.schluessel}>
            <button type="button" onClick={() => setZu({ ...zu, [g.schluessel]: !geschlossen })} aria-expanded={!geschlossen}
              className="mb-1 inline-flex items-center gap-1 text-xs font-medium text-muted-foreground hover:text-foreground">
              <ChevronRight className={cn("h-3.5 w-3.5 transition-transform", !geschlossen && "rotate-90")} />
              {g.titel} <span className="rounded-full bg-foreground/[0.06] px-1.5 tabular-nums">{liste.length}</span>
            </button>
            {!geschlossen && (
              <ul className="space-y-1">
                {liste.map((m, i) => (
                  <MailZeile key={m.id ?? i} m={m} ordnerLaden={ordnerLaden} onNeu={onNeu}
                    onWeg={(id) => setWeg((s) => new Set(s).add(id))} />
                ))}
              </ul>
            )}
          </div>
        );
      })}
      <p className="text-[11px] text-muted-foreground">
        Eingeordnet nach Absender und Kopfzeilen (kein KI-Aufruf) — fahr über eine Mail, um den Grund zu sehen.
      </p>
    </div>
  );
}

// ─── Regeln ─────────────────────────────────────────────────────

function Regeln({ regeln, vorschlaege, onNeu }: { regeln: api.SekretariatRegel[]; vorschlaege: api.HeuteRegelVorschlag[]; onNeu: () => void }) {
  const toast = useToast();
  const confirm = useConfirm();
  const [bearbeitet, setBearbeitet] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [laeuft, setLaeuft] = useState<string | null>(null);

  const machen = async (schluessel: string, aufruf: () => Promise<unknown>, ok: string, fehlerTitel: string) => {
    setLaeuft(schluessel);
    try {
      await aufruf();
      toast.success(ok);
      onNeu();
    } catch (e) {
      toast.error(fehlerTitel, apiFehlertext(e));
    } finally {
      setLaeuft(null);
    }
  };

  const loeschen = async (r: api.SekretariatRegel) => {
    const ok = await confirm({ title: "Regel löschen?", message: `„${r.text}“ — die Agenten des Sekretariats halten sich danach nicht mehr daran.`,
      confirmLabel: "Löschen", variant: "destructive" });
    if (ok) await machen(r.schluessel, () => api.sekretariatRegelLoeschen(r.schluessel), "Regel gelöscht", "Nicht gelöscht");
  };

  const speichern = (r: api.SekretariatRegel) =>
    machen(r.schluessel, async () => { await api.sekretariatRegelAendern(r.schluessel, text.trim()); setBearbeitet(null); },
      "Regel geändert", "Nicht geändert");

  return (
    <div className="space-y-2">
      {regeln.length === 0 ? (
        <p className="text-xs text-muted-foreground">Noch keine. Schreib oben „Merke: …“ oder übernimm einen Vorschlag — daran halten sich ab dann alle Agenten des Sekretariats.</p>
      ) : (
        <ul className="space-y-1.5">
          {regeln.map((r) => (
            <li key={r.schluessel} className="rounded-lg bg-foreground/[0.03] px-2.5 py-1.5 text-sm">
              {bearbeitet === r.schluessel ? (
                <div className="space-y-1.5">
                  <textarea value={text} onChange={(e) => setText(e.target.value)} rows={2} autoFocus
                    className="w-full resize-y rounded-lg border border-foreground/[0.1] bg-background/80 px-2 py-1 text-xs" />
                  <div className="flex gap-1.5">
                    <KleinerKnopf disabled={!text.trim() || laeuft === r.schluessel} onClick={() => speichern(r)}><Save className="h-3 w-3" /> Speichern</KleinerKnopf>
                    <KleinerKnopf onClick={() => setBearbeitet(null)}>Abbrechen</KleinerKnopf>
                  </div>
                </div>
              ) : (
                <div className="flex items-start gap-2">
                  <span className="min-w-0 flex-1 break-words">{r.text}</span>
                  <button type="button" aria-label="Regel bearbeiten" title="Regel bearbeiten"
                    onClick={() => { setBearbeitet(r.schluessel); setText(r.text); }}
                    className="rounded p-1 text-muted-foreground hover:bg-foreground/[0.06] hover:text-foreground"><Pencil className="h-3.5 w-3.5" /></button>
                  <button type="button" aria-label="Regel löschen" title="Regel löschen" disabled={laeuft === r.schluessel}
                    onClick={() => loeschen(r)}
                    className="rounded p-1 text-muted-foreground hover:bg-red-500/10 hover:text-red-500"><Trash2 className="h-3.5 w-3.5" /></button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
      {vorschlaege.length > 0 && (
        <div className="space-y-1.5 rounded-lg border border-dashed border-foreground/[0.12] p-2">
          <p className="inline-flex items-center gap-1 text-[11px] font-medium text-muted-foreground"><Lightbulb className="h-3 w-3" /> Vorschläge — werden erst mit deinem Klick zur Regel</p>
          <ul className="space-y-1.5">
            {vorschlaege.map((v) => {
              const schluessel = v.approval_id ?? v.text;
              return (
                <li key={schluessel} className="space-y-1 text-xs">
                  <p className="break-words">{v.text}</p>
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="text-[11px] text-muted-foreground">{v.anlass}</span>
                    <KleinerKnopf disabled={laeuft === schluessel}
                      onClick={() => machen(schluessel, () => (v.approval_id ? api.sekretariatRegelVorschlag(v.approval_id, true) : api.sekretariatRegelAnlegen(v.text)),
                        "Regel übernommen", "Nicht übernommen")}>
                      <Check className="h-3 w-3" /> Übernehmen
                    </KleinerKnopf>
                    {v.approval_id && (
                      <KleinerKnopf disabled={laeuft === schluessel}
                        onClick={() => machen(schluessel, () => api.sekretariatRegelVorschlag(v.approval_id as string, false), "Vorschlag verworfen", "Nicht verworfen")}>
                        <X className="h-3 w-3" /> Verwerfen
                      </KleinerKnopf>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}

// ─── Protokoll ──────────────────────────────────────────────────

const PROTOKOLL_ICON: Record<string, React.ElementType> = {
  verschoben: Archive, erledigt: Check, antwort_beauftragt: Reply, entwurf_bearbeitet: Pencil, entwurf: Mail,
  gesendet: Send, abgelehnt: X, erinnerung: Bell, lauf: Inbox, lauf_fehler: X,
};

function Protokoll({ eintraege, agentName, onNeu }: { eintraege: api.HeuteProtokollEintrag[]; agentName: (id: string | null) => string; onNeu: () => void }) {
  const [offen, setOffen] = useState(false);
  const [nachfassen, setNachfassen] = useState<string | null>(null);
  return (
    <section className="rounded-2xl border border-foreground/[0.08] bg-card p-4">
      <button type="button" onClick={() => setOffen(!offen)} aria-expanded={offen}
        className="inline-flex items-center gap-1.5 text-sm font-semibold">
        <ChevronRight className={cn("h-4 w-4 text-muted-foreground transition-transform", offen && "rotate-90")} />
        <ScrollText className="h-4 w-4 text-muted-foreground" /> Was das Sekretariat heute getan hat
        <span className="rounded-full bg-foreground/[0.06] px-1.5 text-xs tabular-nums text-muted-foreground">{eintraege.length}</span>
      </button>
      {offen && (
        eintraege.length === 0 ? (
          <p className="mt-2 text-xs text-muted-foreground">Heute noch nichts.</p>
        ) : (
          <ul className="mt-2 space-y-1">
            {eintraege.map((e, i) => {
              const Icon = PROTOKOLL_ICON[e.art] ?? Inbox;
              return (
                <li key={i} className="text-sm">
                  <div className="flex items-start gap-2">
                    <span className="w-11 shrink-0 tabular-nums text-xs text-muted-foreground">{uhrzeit(e.zeit)}</span>
                    <Icon className={cn("mt-0.5 h-3.5 w-3.5 shrink-0", e.art === "lauf_fehler" ? "text-red-500" : "text-muted-foreground")} />
                    <span className="min-w-0 flex-1 break-words">{e.text}</span>
                    <span className="shrink-0 text-[11px] text-muted-foreground">{e.agent_id ? agentName(e.agent_id) : "du"}</span>
                    {e.art === "gesendet" && e.approval_id && (
                      <KleinerKnopf onClick={() => setNachfassen(nachfassen === e.approval_id ? null : e.approval_id ?? null)}>
                        <AlarmClock className="h-3 w-3" /> Nachfassen
                      </KleinerKnopf>
                    )}
                  </div>
                  {nachfassen && nachfassen === e.approval_id && (
                    <div className="ml-[4.25rem] mt-1">
                      <Nachfassen approvalId={nachfassen} onSchliessen={() => setNachfassen(null)}
                        onFertig={() => { setNachfassen(null); onNeu(); }} />
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        )
      )}
    </section>
  );
}

// ─── Seite ──────────────────────────────────────────────────────

export default function HeutePage() {
  const toast = useToast();
  const toastRef = useRef(toast);
  toastRef.current = toast;
  const { agents } = useAgents();
  const [daten, setDaten] = useState<api.Heute | null>(null);
  const [eingabe, setEingabe] = useState("");
  const [sendet, setSendet] = useState(false);
  const [briefingStartet, setBriefingStartet] = useState(false);
  const [gesendet, setGesendet] = useState<{ approvalId: string; betreff: string } | null>(null);
  const agentById = new Map(agents.map((a) => [a.id, a]));
  const agentName = (id: string | null) => (id ? agentById.get(id)?.name ?? "Agent" : "du");

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

  const briefingNeu = async () => {
    setBriefingStartet(true);
    try {
      await api.sekretariatBriefingNeu();
      toast.success("Briefing wird erstellt", "Es erscheint hier, sobald der Lead fertig ist.");
      await laden();
    } catch (e) {
      toast.error("Nicht gestartet", apiFehlertext(e));
    } finally {
      setBriefingStartet(false);
    }
  };

  const datum = new Date().toLocaleDateString("de-DE", { weekday: "long", day: "numeric", month: "long" });
  const ungelesen = daten?.ungelesen ?? null;
  const gesamt = daten?.ungelesen_gesamt ?? null;

  return (
    <div>
      <Header title="Start" subtitle={`${datum} — dein Sekretariat`} />
      <div className="space-y-5 p-4 sm:p-6">
        {!daten ? (
          <div className="flex justify-center py-16 text-muted-foreground"><Loader2 className="h-5 w-5 animate-spin" /></div>
        ) : !daten.eingerichtet ? (
          <Einrichten onFertig={laden} entwurfFrei={daten.m365_entwurf_frei} />
        ) : (
          <>
            <div className="space-y-2 rounded-2xl border border-foreground/[0.08] bg-card p-3">
              <div className="flex items-end gap-2">
                <textarea value={eingabe} onChange={(e) => setEingabe(e.target.value)} rows={2}
                  onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) abschicken(); }}
                  placeholder="Was soll das Sekretariat tun? — „Merke: …“ wird eine feste Regel, z. B. „Merke: Mails von der Steuerkanzlei immer sofort an mich“."
                  className="min-w-0 flex-1 resize-none rounded-lg border border-foreground/[0.1] bg-background/80 px-3 py-2 text-sm outline-none focus:border-primary/50" />
                <button type="button" onClick={abschicken} disabled={sendet || !eingabe.trim()} aria-label="Abschicken"
                  className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50">
                  {sendet ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
                </button>
              </div>
              {daten.verarbeitung.length > 0 && (
                <p className="flex items-start gap-1.5 text-[11px] text-muted-foreground">
                  <Lock className="mt-0.5 h-3 w-3 shrink-0" />
                  <span>
                    Datenschutz: Mails und Termine liest das Sekretariat über dein Microsoft-Konto. Verarbeitet werden die Inhalte von{" "}
                    {daten.verarbeitung.map((v, i) => (
                      <span key={v.agent_id}>{i > 0 ? "; " : ""}<strong className="font-medium text-foreground/80">{v.name}</strong> ({v.laufzeit} · {v.anbieter}{v.modell ? ` · ${v.modell}` : ""})</span>
                    ))}.
                  </span>
                </p>
              )}
            </div>

            <div className="grid items-start gap-4 lg:grid-cols-2">
              <Kasten titel="Wartet auf dich" icon={ShieldCheck}
                rechts={daten.wartet.length > 0 ? <span className="rounded-full bg-amber-500/15 px-2 text-xs font-semibold text-amber-700 dark:text-amber-300">{daten.wartet.length}</span> : null}>
                {gesendet && (
                  <div className="mb-3 space-y-1 rounded-lg border border-emerald-500/25 bg-emerald-500/5 px-2.5 py-2">
                    <p className="text-xs">Gesendet: „{gesendet.betreff}“</p>
                    <Nachfassen approvalId={gesendet.approvalId}
                      onSchliessen={() => setGesendet(null)} onFertig={() => { setGesendet(null); laden(); }} />
                  </div>
                )}
                {daten.wartet.length === 0 ? (
                  <p className="text-xs text-muted-foreground">Nichts offen. Antworten, die das Sekretariat entwirft, erscheinen hier — gesendet wird erst, wenn du freigibst.</p>
                ) : (
                  <ul className="space-y-3">
                    {daten.wartet.map((w) => (
                      <WartetEintrag key={w.approval_id} w={w} agentName={agentName(w.agent_id)} onNeu={laden}
                        onGesendet={(approvalId, betreff) => setGesendet({ approvalId, betreff })} />
                    ))}
                  </ul>
                )}
              </Kasten>

              <Kasten titel="Termine heute" icon={CalendarDays}>
                <Termine daten={daten} onErneut={laden} />
              </Kasten>

              <Kasten titel="Posteingang" icon={Inbox}
                rechts={ungelesen && !daten.fehler.posteingang ? (
                  <span className="text-[11px] text-muted-foreground">
                    {gesamt ?? ungelesen.length} ungelesen{gesamt !== null && gesamt > ungelesen.length ? ` · die neuesten ${ungelesen.length} hier` : ""}
                  </span>
                ) : null}>
                <Posteingang daten={daten} onNeu={laden} onErneut={laden} />
              </Kasten>

              <Kasten titel="Erinnerungen" icon={Bell}>
                <Erinnerungen liste={daten.erinnerungen} fristen={daten.frist_vorschlaege} onNeu={laden} />
              </Kasten>

              <Kasten titel="Tagesbriefing" icon={Sun}
                rechts={
                  <div className="flex items-center gap-2">
                    {daten.briefing?.erstellt && <span className="text-[11px] text-muted-foreground">erstellt um {uhrzeit(daten.briefing.erstellt)}</span>}
                    <KleinerKnopf onClick={briefingNeu} disabled={briefingStartet || daten.briefing_laeuft}
                      titel={daten.briefing_laeuft ? "Das Briefing wird gerade erstellt." : "Briefing jetzt neu erstellen"}>
                      {briefingStartet || daten.briefing_laeuft ? <Loader2 className="h-3 w-3 animate-spin" /> : <RefreshCw className="h-3 w-3" />} Neu erstellen
                    </KleinerKnopf>
                  </div>
                }>
                {daten.briefing ? (
                  <div className="max-h-96 overflow-y-auto text-sm"><MarkdownContent content={daten.briefing.text} ohneBilder /></div>
                ) : (
                  <p className="text-xs text-muted-foreground">
                    {daten.briefing_laeuft ? "Das Briefing wird gerade erstellt …" : "Das Briefing von heute liegt noch nicht vor — es kommt zur eingestellten Uhrzeit, oder du erstellst es jetzt."}
                  </p>
                )}
              </Kasten>

              <Kasten titel="Regeln" icon={ShieldCheck}>
                <Regeln regeln={daten.regeln} vorschlaege={daten.regel_vorschlaege} onNeu={laden} />
              </Kasten>
            </div>

            <Protokoll eintraege={daten.protokoll} agentName={agentName} onNeu={laden} />

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

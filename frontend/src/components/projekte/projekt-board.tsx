"use client";

/**
 * Board eines Projekts: Inbox → Bereit → In Arbeit → Triage → Review → Erledigt
 * (und Verworfen). Karten werden per Klick weitergeschoben: freigeben (nur ein
 * Mensch — Agenten nehmen nur Freigegebenes), Priorität setzen, vergeben („Los“),
 * Rückfragen beantworten (Triage → Freigeben), abnehmen oder zurückgeben.
 */

import { useEffect, useState } from "react";
import { UserRound,
  ArrowLeft, ArrowRight, ArrowRightLeft, Bot, Check, CircleDot, CornerUpLeft, ExternalLink, FoldHorizontal, GitBranch,
  GitPullRequest, Globe, Loader2, MessageCircleQuestion, Play, Plus, ScrollText, Server, Terminal, Trash2, TriangleAlert,
  UnfoldHorizontal, Unlock,
} from "lucide-react";
import type { Agent } from "@/lib/types";
import {
  BEARBEITER_ART, PRIORITAETEN, PR_ZUSTAND, SPALTEN, wochentag,
  type BearbeiterArt, type ProjektBoard, type ProjektKarte, type ProjektKennzahlen,
} from "@/lib/projekte";
import { AgentAvatar } from "@/components/agents/agent-avatar";
import { Fenster } from "@/components/ui/fenster";
import { MarkdownContent } from "@/components/ui/markdown-content";
import { useAufgabeFenster } from "@/components/tasks/aufgabe-fenster";
import * as api from "@/lib/api";
import { apiFehlertext } from "@/lib/api-fehler";
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
  /** Fremde Übernahme lösen (Tag + Zuständige im Repo weg, Karte nach Bereit). */
  freigeben: (k: ProjektKarte) => Promise<void>;
  /** „Übergeben an …“: an einen Agenten des Projekts (agent_id) oder jemanden außerhalb (an). */
  uebergeben: (k: ProjektKarte, body: { agent_id?: string; an?: string; stand: string; naechster_schritt: string }) => Promise<void>;
  /** Öffnet das Übergabe-Fenster — setzt die Board-Ansicht selbst. */
  uebergabeOeffnen?: (k: ProjektKarte) => void;
  // agentId leer: der bisherige Bearbeiter — ist er nicht mehr im Projekt, der Lead.
  zurueckgeben: (k: ProjektKarte, hinweis: string, agentId: string | null) => Promise<void>;
  anlegen: (titel: string, text: string) => Promise<void>;
}

function Knopf({
  onClick, children, titel, haupt = false, gefahr = false, leise = false,
}: { onClick: () => Promise<void> | void; children: React.ReactNode; titel?: string; haupt?: boolean; gefahr?: boolean; leise?: boolean }) {
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
            : leise ? "text-muted-foreground hover:bg-foreground/[0.06] hover:text-foreground"
            : "border border-foreground/[0.1] hover:bg-foreground/[0.04]",
      )}
    >
      {busy ? <Loader2 className="h-3 w-3 animate-spin" /> : null}
      {children}
    </button>
  );
}

/** Stand einer Übernahme von außen: übernommen → Branch → PR, dazu das letzte Lebenszeichen. */
function ExternStand({ karte, farbe, onFreigeben }: { karte: ProjektKarte; farbe: string; onFreigeben: () => Promise<void> }) {
  const schritte = [
    { label: "Übernommen", da: true },
    { label: "Branch", da: !!karte.extern_branch },
    { label: "PR", da: !!karte.pr_nr },
  ];
  return (
    <div className="mt-2 space-y-1.5">
      <div className="flex items-center gap-1 text-[10px]">
        {schritte.map((s, i) => (
          <span key={s.label} className="inline-flex items-center gap-1">
            {i > 0 && <span className="h-px w-3 bg-foreground/15" />}
            <span
              className={cn("rounded-full px-1.5 py-px", s.da ? "font-medium text-foreground" : "text-muted-foreground/70 ring-1 ring-inset ring-foreground/10")}
              style={s.da ? { background: `${farbe}22` } : undefined}
            >
              {s.label}
            </span>
          </span>
        ))}
      </div>
      {karte.extern_branch && (
        <p className="flex items-center gap-1 truncate text-[10px] text-muted-foreground" title={karte.extern_branch}>
          <GitBranch className="h-3 w-3 shrink-0" /> {karte.extern_branch}
        </p>
      )}
      {karte.extern_stockt ? (
        <div className="rounded-md bg-amber-500/10 px-2 py-1.5 text-[11px] text-amber-700 dark:text-amber-300">
          <p className="flex items-start gap-1">
            <TriangleAlert className="mt-px h-3 w-3 shrink-0" />
            <span>
              Abgelaufen: {karte.extern_regung ? `letzte Bewegung ${timeAgo(karte.extern_regung)}` : "keine Bewegung"} — kein Commit,
              keine erneuerte Übernahme.
            </span>
          </p>
          <div className="mt-1.5">
            <Knopf titel="Übernahme im Repo lösen, Karte nach Bereit" onClick={onFreigeben}>
              <Unlock className="h-3 w-3" /> Freigeben
            </Knopf>
          </div>
        </div>
      ) : (
        (karte.extern_regung || karte.extern_bis) && (
          <p className="text-[10px] text-muted-foreground">
            {karte.extern_regung && <>Letzte Bewegung {timeAgo(karte.extern_regung)}</>}
            {karte.extern_regung && karte.extern_bis && " · "}
            {karte.extern_bis && <>gilt bis {new Date(karte.extern_bis).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" })}</>}
          </p>
        )
      )}
      {!!karte.bearbeiter_uebernahmen && (
        <p className="flex items-start gap-1 text-[10px] text-amber-700 dark:text-amber-300">
          <TriangleAlert className="mt-px h-3 w-3 shrink-0" />
          Dieser Bearbeiter hält {karte.bearbeiter_uebernahmen} Übernahmen — mehr als im Projekt erlaubt.
        </p>
      )}
    </div>
  );
}

/** Triage: ein Agent hat gefragt und arbeitet nicht weiter — die Karte wartet auf dich. */
function WartetAufDich({ karte, gross = false }: { karte: ProjektKarte; gross?: boolean }) {
  if (karte.spalte !== "triage") return null;
  return (
    <p className={cn("flex items-start gap-1 rounded-md bg-amber-500/10 text-amber-700 dark:text-amber-300",
      gross ? "px-3 py-2 text-xs" : "mt-1.5 px-2 py-1 text-[11px]")}>
      <MessageCircleQuestion className={cn("mt-px shrink-0", gross ? "h-3.5 w-3.5" : "h-3 w-3")} />
      <span>
        <span className="font-medium">Wartet auf dich:</span>{" "}
        {karte.quelle === "issue"
          ? "Rückfrage im Issue beantworten, dann freigeben."
          : "Rückfrage in der Notiz beantworten, dann freigeben."}
      </span>
    </p>
  );
}

/** Rot: zwei gültige Übernahmen am selben Issue oder ein fremder Commit auf dem Branch. */
function Konflikt({ karte }: { karte: ProjektKarte }) {
  if (!karte.konflikt) return null;
  return (
    <p className="mt-1.5 flex items-start gap-1 rounded-md bg-red-500/10 px-2 py-1 text-[11px] text-red-700 dark:text-red-300">
      <TriangleAlert className="mt-px h-3 w-3 shrink-0" />
      <span><span className="font-medium">Konflikt:</span> {karte.konflikt}</span>
    </p>
  );
}

/** „Übergeben an …“: Empfänger, Stand und nächster Schritt — landet als Kommentar im Issue. */
function UebergabeFenster({
  karte, agents, mitglieder, aktionen, schliessen,
}: {
  karte: ProjektKarte | null;
  agents: Map<string, Agent>;
  mitglieder: string[];
  aktionen: BoardAktionen;
  schliessen: () => void;
}) {
  const [ziel, setZiel] = useState("");
  const [aussen, setAussen] = useState("");
  const [stand, setStand] = useState("");
  const [naechster, setNaechster] = useState("");
  const kartenId = karte?.id ?? null;
  useEffect(() => {
    // Nur beim Öffnen leeren — das Board lädt regelmäßig neu, die Karte ist dann ein neues Objekt.
    if (kartenId) { setZiel(""); setAussen(""); setStand(""); setNaechster(""); }
  }, [kartenId]);
  const istAussen = ziel === "__aussen";
  const bereit = !!karte && !!stand.trim() && !!naechster.trim() && (istAussen ? !!aussen.trim() : !!ziel);
  const senden = async () => {
    if (!karte || !bereit) return;
    try {
      await aktionen.uebergeben(karte, {
        ...(istAussen ? { an: aussen.trim() } : { agent_id: ziel }),
        stand: stand.trim(), naechster_schritt: naechster.trim(),
      });
      schliessen();
    } catch {
      // Die Meldung zeigt die Aktion selbst; das Fenster bleibt offen.
    }
  };
  const feld = "w-full rounded-lg border border-foreground/[0.1] bg-background/80 px-3 py-2 text-sm outline-none focus:border-primary/50";
  return (
    <Fenster offen={!!karte} schliessen={schliessen} titel={karte ? `Übergeben: #${karte.extern_nr} ${karte.titel}` : ""} breite="max-w-lg">
      <div className="space-y-3 p-5 text-sm">
        <p className="text-xs text-muted-foreground">
          Stand und nächster Schritt kommen als Kommentar ins Issue; die Übernahme geht an den Empfänger.
          Ein Agent des Projekts bekommt die Karte als neuen Lauf.
        </p>
        <label className="block text-xs font-medium text-muted-foreground">
          An
          <select value={ziel} onChange={(e) => setZiel(e.target.value)} className={cn(feld, "mt-1")}>
            <option value="">Empfänger wählen …</option>
            {mitglieder.filter((m) => m !== karte?.agent_id).map((m) => (
              <option key={m} value={m}>{agents.get(m)?.name ?? m}</option>
            ))}
            <option value="__aussen">Jemand außerhalb der Plattform …</option>
          </select>
        </label>
        {istAussen && (
          <label className="block text-xs font-medium text-muted-foreground">
            Wer genau
            <input value={aussen} onChange={(e) => setAussen(e.target.value)} className={cn(feld, "mt-1")}
              placeholder="claude-code:Laptop · mensch:login · ai-employee:anlage/agent" />
          </label>
        )}
        <label className="block text-xs font-medium text-muted-foreground">
          Stand
          <textarea value={stand} onChange={(e) => setStand(e.target.value)} rows={3} className={cn(feld, "mt-1 resize-none")}
            placeholder="Was ist erledigt, was offen, wo liegt die Arbeit (Branch, PR)?" />
        </label>
        <label className="block text-xs font-medium text-muted-foreground">
          Nächster Schritt
          <textarea value={naechster} onChange={(e) => setNaechster(e.target.value)} rows={2} className={cn(feld, "mt-1 resize-none")} />
        </label>
        <div className="flex justify-end">
          <Knopf haupt onClick={senden} titel={bereit ? undefined : "Empfänger, Stand und nächsten Schritt ausfüllen"}>
            <ArrowRightLeft className="h-3 w-3" /> Übergeben
          </Knopf>
        </div>
      </div>
    </Fenster>
  );
}

/** Was man mit einer Karte tun kann — je Spalte. Auf der Karte und im Karten-Fenster gleich. */
function KartenAktionen({
  karte, agents, mitglieder, leadId, aktionen, onZurueck, kompakt = false,
}: {
  karte: ProjektKarte;
  agents: Map<string, Agent>;
  mitglieder: string[];
  leadId: string | null;
  aktionen: BoardAktionen;
  onZurueck: (k: ProjektKarte) => void;
  kompakt?: boolean;
}) {
  // Leer = an den Lead (er verteilt); ohne Lead muss man wählen (außer bei einem Mitglied).
  const [ziel, setZiel] = useState<string>(!leadId && mitglieder.length === 1 ? mitglieder[0] : "");
  const prOffen = !!karte.pr_url && !["gemergt", "geschlossen"].includes(karte.pr_zustand ?? "");
  return (
    <div className={cn("flex flex-wrap items-center gap-1.5", kompakt ? "mt-1.5 -mb-1 -ml-1" : "mt-2.5")}>
      {(karte.spalte === "eingang" || karte.spalte === "triage") && (
        <>
          <Knopf haupt={karte.spalte === "triage"} leise={karte.spalte === "eingang"}
            titel={karte.spalte === "triage"
              ? "Rückfrage geklärt: nach Bereit — Agenten nehmen die Karte wieder (bei Issues: Triage-Tag weg, status/ready)"
              : "Freigeben: nach Bereit — Agenten nehmen die Karte (bei Issues: Tag status/ready)"}
            onClick={() => aktionen.verschieben(karte, { spalte: "bereit" })}>
            Freigeben <ArrowRight className="h-3 w-3" />
          </Knopf>
          <Knopf gefahr titel="Verwerfen — wird nicht umgesetzt" onClick={() => aktionen.verschieben(karte, { spalte: "verworfen" })}>
            <Trash2 className="h-3 w-3" />
          </Knopf>
        </>
      )}
      {karte.spalte === "verworfen" && (
        <Knopf leise titel="Doch umsetzen: zurück in die Inbox" onClick={() => aktionen.verschieben(karte, { spalte: "eingang" })}>
          <ArrowLeft className="h-3 w-3" /> Inbox
        </Knopf>
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
          <Knopf titel="Zurück in die Inbox" onClick={() => aktionen.verschieben(karte, { spalte: "eingang" })}>
            <ArrowLeft className="h-3 w-3" />
          </Knopf>
        </>
      )}
      {karte.spalte === "pruefung" && karte.task_id && (
        <>
          <Knopf haupt onClick={() => aktionen.abnehmen(karte)}>
            <Check className="h-3 w-3" /> Abnehmen
          </Knopf>
          <Knopf onClick={() => onZurueck(karte)}>
            <CornerUpLeft className="h-3 w-3" /> Zurückgeben
          </Knopf>
        </>
      )}
      {/* Ohne eigenen Lauf (PR aus dem Repo, Issue mit fremdem PR) gibt es nichts
          abzunehmen oder zurückzugeben — nur „erledigt“, wenn er gemergt ist. */}
      {/* Bei offenem PR kein „Erledigt“: der Knopf verschob nur die Karte, der PR
          blieb offen — und nach dem Merge zieht der Abgleich die Karte ohnehin nach. */}
      {karte.spalte === "pruefung" && !karte.task_id && prOffen && (
        <span className="text-[11px] text-muted-foreground">Wandert nach dem Merge von selbst nach Erledigt.</span>
      )}
      {karte.spalte === "in_arbeit" && karte.quelle === "issue" && aktionen.uebergabeOeffnen && (
        <Knopf leise titel="Übergeben an … — Stand und nächster Schritt kommen ins Issue"
          onClick={() => aktionen.uebergabeOeffnen?.(karte)}>
          <ArrowRightLeft className="h-3 w-3" /> Übergeben
        </Knopf>
      )}
      {karte.spalte === "pruefung" && !karte.task_id && !prOffen && (
        <Knopf titel="Nach Erledigt (z. B. nach dem Merge)" onClick={() => aktionen.verschieben(karte, { spalte: "erledigt" })}>
          <Check className="h-3 w-3" /> Erledigt
        </Knopf>
      )}
    </div>
  );
}

const BEARBEITER_SYMBOL: Record<BearbeiterArt, typeof UserRound> = {
  plattform: Bot,
  claude_code: Terminal,
  andere_anlage: Server,
  mensch: UserRound,
  extern: Globe,
};

/** Wer ein Issue übernommen hat — mit Art und Namen aus der Übernahme im Repo. Ein
 *  Agent dieser Anlage erscheint wie ein Agent mit Lauf (Bild und Name). */
function Bearbeiter({ karte, agents }: { karte: ProjektKarte; agents: Map<string, Agent> }) {
  const art: BearbeiterArt = karte.bearbeiter_art ?? (karte.extern_bearbeiter === "extern" ? "extern" : "mensch");
  const eigen = art === "plattform" && karte.extern_agent_id ? agents.get(karte.extern_agent_id) : undefined;
  const Icon = BEARBEITER_SYMBOL[art] ?? UserRound;
  const name = eigen?.name ?? (karte.extern_bearbeiter === "extern" ? "extern dran" : karte.extern_bearbeiter);
  return (
    <span
      title={`Übernommen von: ${BEARBEITER_ART[art]}. Der Branch der Übernahme gehört dem Übernehmer.`}
      className={cn(
        "inline-flex max-w-[14rem] items-center gap-1 rounded-full px-1.5 py-px font-medium text-foreground",
        art === "plattform" ? "bg-foreground/[0.06]" : "bg-primary/10",
      )}
    >
      {eigen ? (
        <AgentAvatar config={eigen.config} size="sm" className="h-4 w-4 rounded" />
      ) : (
        <Icon className="h-3 w-3 shrink-0 text-primary" />
      )}
      <span className="truncate">{name}</span>
    </span>
  );
}

/** Kopfzeile einer Karte: Ticket, Agent, Bearbeiter von außen, Zeit, Kosten. */
function KartenMeta({ karte, agents }: { karte: ProjektKarte; agents: Map<string, Agent> }) {
  const money = useMoney();
  const agent = karte.agent_id ? agents.get(karte.agent_id) : undefined;
  const wann = karte.erledigt ?? karte.aktualisiert ?? karte.erstellt;
  return (
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
      {karte.extern_bearbeiter && <Bearbeiter karte={karte} agents={agents} />}
      {wann && <span>{timeAgo(wann)}</span>}
      {karte.kosten_usd ? <span title={money.title(karte.kosten_usd)}>{money.fmt(karte.kosten_usd)}</span> : null}
    </div>
  );
}

function Tags({ labels, alle = false }: { labels: string[]; alle?: boolean }) {
  if (!labels.length) return null;
  const zeigen = alle ? labels : labels.slice(0, 6);
  return (
    <div className="mt-1.5 flex flex-wrap gap-1">
      {zeigen.map((tag) => (
        <span key={tag} className="rounded-full border border-border px-1.5 py-px text-[10px] text-muted-foreground">
          {tag}
        </span>
      ))}
      {labels.length > zeigen.length && <span className="text-[10px] text-muted-foreground">+{labels.length - zeigen.length}</span>}
    </div>
  );
}

function PrLink({ karte }: { karte: ProjektKarte }) {
  if (!karte.pr_url) return null;
  return (
    <a href={karte.pr_url} target="_blank" rel="noopener noreferrer"
      className="mt-2 inline-flex items-center gap-1 rounded-md bg-foreground/[0.04] px-1.5 py-0.5 text-[11px] hover:bg-foreground/[0.08]">
      <GitPullRequest className="h-3 w-3" /> PR #{karte.pr_nr ?? "?"} · {PR_ZUSTAND[karte.pr_zustand ?? "offen"] ?? karte.pr_zustand}
      <ExternalLink className="h-2.5 w-2.5" />
    </a>
  );
}

/** Klicks auf Knöpfe, Links und Auswahlfelder IN der Karte öffnen kein Fenster. */
function aufBedienelement(ziel: EventTarget | null): boolean {
  return ziel instanceof HTMLElement && !!ziel.closest("button, a, select, input, textarea, label");
}

function Karte({
  karte, agents, mitglieder, leadId, farbe, aktionen, onZurueck, onOeffnen, ausgabe,
}: {
  karte: ProjektKarte;
  /** Letzte Ausgabe des laufenden Agenten (nur in Arbeit). */
  ausgabe?: string | null;
  agents: Map<string, Agent>;
  mitglieder: string[];
  leadId: string | null;
  farbe: string;
  aktionen: BoardAktionen;
  onZurueck: (k: ProjektKarte) => void;
  /** Klick auf die Karte: alles zur Karte im Fenster. */
  onOeffnen: (k: ProjektKarte) => void;
}) {
  const prio = PRIORITAETEN[karte.prioritaet] ?? PRIORITAETEN[2];
  const gescheitert = karte.task_status === "failed";

  return (
    <div
      role="button"
      tabIndex={0}
      aria-label={`Karte öffnen: ${karte.titel}`}
      onClick={(e) => { if (!aufBedienelement(e.target)) onOeffnen(karte); }}
      onKeyDown={(e) => { if ((e.key === "Enter" || e.key === " ") && e.target === e.currentTarget) { e.preventDefault(); onOeffnen(karte); } }}
      className={cn("cursor-pointer rounded-xl border bg-card text-sm shadow-sm transition-colors hover:border-foreground/[0.16] focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/40",
        karte.spalte === "eingang" || karte.spalte === "verworfen" ? "p-2.5" : "p-3",
        gescheitert || karte.konflikt ? "border-red-500/40" : "border-foreground/[0.08]")}
    >
      <div className="flex items-start gap-1.5">
        <button
          type="button"
          title="Priorität ändern"
          onClick={() => aktionen.verschieben(karte, { prioritaet: (karte.prioritaet % 3) + 1 })}
          disabled={karte.spalte === "erledigt" || karte.spalte === "verworfen"}
          className={cn("mt-0.5 shrink-0 rounded px-1 text-[10px] font-semibold", prio.klasse)}
        >
          {prio.label}
        </button>
        <div className="min-w-0 flex-1">
          <p className="line-clamp-2 font-medium">{karte.titel}</p>
          <KartenMeta karte={karte} agents={agents} />
        </div>
      </div>

      <Tags labels={karte.labels} />
      <Konflikt karte={karte} />
      <WartetAufDich karte={karte} />

      {karte.notiz && <p className="mt-1.5 rounded-md bg-amber-500/10 px-2 py-1 text-[11px] text-amber-700 dark:text-amber-300">{karte.notiz}</p>}

      {karte.spalte === "in_arbeit" && karte.extern_bearbeiter && !karte.task_id && (
        <ExternStand karte={karte} farbe={farbe} onFreigeben={() => aktionen.freigeben(karte)} />
      )}

      {karte.spalte === "in_arbeit" && !(karte.extern_bearbeiter && !karte.task_id) && (
        <>
          {ausgabe && (
            <p className="mt-1.5 line-clamp-2 text-[11px] italic text-foreground/70" title={ausgabe}>„{ausgabe}“</p>
          )}
          <div className="mt-2"><StufenLeiste stufe={karte.stufe} farbe={farbe} klein /></div>
        </>
      )}

      <PrLink karte={karte} />

      {(karte.spalte === "pruefung" || karte.spalte === "triage") && (karte.fehler || karte.ergebnis) && (
        <p className={cn("mt-2 line-clamp-4 whitespace-pre-line text-xs", gescheitert ? "text-red-500" : "text-muted-foreground")}>
          {gescheitert && <TriangleAlert className="mr-1 inline h-3 w-3" />}
          {karte.fehler ?? karte.ergebnis}
        </p>
      )}

      <KartenAktionen karte={karte} agents={agents} mitglieder={mitglieder} leadId={leadId}
        aktionen={aktionen} onZurueck={onZurueck} kompakt={karte.spalte === "eingang" || karte.spalte === "verworfen"} />
    </div>
  );
}

/** Eine Karte im Fenster: ganzer Text, Tags, Ticket, PR, Lauf — und dieselben Knöpfe wie auf dem Board. */
function KartenFenster({
  projektId, karte, agents, mitglieder, leadId, farbe, aktionen, onZurueck, schliessen,
}: {
  projektId: string;
  /** Live aus dem Board — nach einer Aktion steht hier der neue Stand. */
  karte: ProjektKarte | null;
  agents: Map<string, Agent>;
  mitglieder: string[];
  leadId: string | null;
  farbe: string;
  aktionen: BoardAktionen;
  onZurueck: (k: ProjektKarte) => void;
  schliessen: () => void;
}) {
  const { oeffnen: aufgabeOeffnen } = useAufgabeFenster();
  const [voll, setVoll] = useState<(ProjektKarte & { lauf_sichtbar?: boolean }) | null>(null);
  const [fehler, setFehler] = useState<string | null>(null);
  const id = karte?.id ?? null;
  const stand = karte?.aktualisiert ?? null;

  useEffect(() => {
    if (!id) return;
    let aktiv = true;
    setFehler(null);
    api.karteHolen(projektId, id)
      .then((k) => { if (aktiv) setVoll(k); })
      .catch((e) => { if (aktiv) setFehler(apiFehlertext(e)); });
    return () => { aktiv = false; };
  }, [projektId, id, stand]);

  const k = karte ? { ...karte, ...(voll?.id === karte.id ? { text: voll.text, ergebnis: voll.ergebnis, fehler: voll.fehler } : {}) } : null;
  const spalte = SPALTEN.find((s) => s.key === k?.spalte)?.label ?? k?.spalte;
  const prio = k ? PRIORITAETEN[k.prioritaet] ?? PRIORITAETEN[2] : null;
  const gescheitert = k?.task_status === "failed";

  return (
    <Fenster offen={!!k} schliessen={schliessen} breite="max-w-3xl">
      {k && prio && (
        <div className="space-y-4 p-5">
          <div>
            <div className="flex items-start gap-2">
              <button
                type="button"
                title="Priorität ändern"
                onClick={() => aktionen.verschieben(k, { prioritaet: (k.prioritaet % 3) + 1 })}
                disabled={k.spalte === "erledigt" || k.spalte === "verworfen"}
                className={cn("mt-1 shrink-0 rounded px-1.5 text-[11px] font-semibold", prio.klasse)}
              >
                {prio.label}
              </button>
              <h2 className="min-w-0 flex-1 text-lg font-semibold leading-snug">{k.titel}</h2>
              <span className="shrink-0 rounded-full bg-foreground/[0.06] px-2 py-0.5 text-[11px] text-muted-foreground">{spalte}</span>
            </div>
            <KartenMeta karte={k} agents={agents} />
            <Tags labels={k.labels} alle />
            <Konflikt karte={k} />
          </div>

          <WartetAufDich karte={k} gross />
          {k.notiz && <p className="rounded-md bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-300">{k.notiz}</p>}

          <section>
            <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Beschreibung</h3>
            {fehler ? (
              <p className="text-sm text-red-500">{fehler}</p>
            ) : k.text ? (
              <div className="rounded-lg border border-foreground/[0.06] bg-foreground/[0.02] p-3 text-sm">
                <MarkdownContent content={k.text} />
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">Keine Beschreibung.</p>
            )}
          </section>

          {k.spalte === "in_arbeit" && k.extern_bearbeiter && !k.task_id && (
            <ExternStand karte={k} farbe={farbe} onFreigeben={() => aktionen.freigeben(k)} />
          )}
          {k.spalte === "in_arbeit" && !(k.extern_bearbeiter && !k.task_id) && (
            <StufenLeiste stufe={k.stufe} farbe={farbe} />
          )}

          <PrLink karte={k} />

          {(k.fehler || k.ergebnis) && (
            <section>
              <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                {gescheitert ? "Fehler des Laufs" : "Ergebnis des Laufs"}
              </h3>
              <div className={cn("max-h-80 overflow-y-auto rounded-lg border p-3 text-sm",
                gescheitert ? "border-red-500/20 bg-red-500/5 text-red-600 dark:text-red-400" : "border-foreground/[0.06] bg-foreground/[0.02]")}>
                <MarkdownContent content={(k.fehler ?? k.ergebnis) as string} />
              </div>
            </section>
          )}

          <div className="flex flex-wrap items-center gap-2 border-t border-foreground/[0.06] pt-3">
            <KartenAktionen karte={k} agents={agents} mitglieder={mitglieder} leadId={leadId}
              aktionen={aktionen} onZurueck={onZurueck} />
            {k.task_id && voll?.lauf_sichtbar !== false && (
              <span className="mt-2.5">
                <Knopf leise titel="Den ganzen Lauf mit Verlauf und Dateien ansehen" onClick={() => aufgabeOeffnen(k.task_id!)}>
                  <ScrollText className="h-3 w-3" /> Lauf ansehen
                </Knopf>
              </span>
            )}
          </div>
        </div>
      )}
    </Fenster>
  );
}

export function ProjektBoardAnsicht({
  projektId, board, agents, mitglieder, leadId, farbe, aktionen,
}: {
  projektId: string;
  board: ProjektBoard;
  agents: Agent[];
  mitglieder: string[];
  leadId: string | null;
  farbe: string;
  aktionen: BoardAktionen;
}) {
  const [zurueck, setZurueck] = useState<ProjektKarte | null>(null);
  // Karte im Fenster: nur die Kennung merken — gezeigt wird der LIVE-Stand aus dem Board.
  const [offenId, setOffenId] = useState<string | null>(null);
  const offeneKarte = offenId
    ? Object.values(board.spalten).flat().find((k) => k.id === offenId) ?? null
    : null;
  const [hinweis, setHinweis] = useState("");
  const [sendet, setSendet] = useState(false);
  const [neu, setNeu] = useState(false);
  const [titel, setTitel] = useState("");
  const [text, setText] = useState("");
  const agentById = new Map(agents.map((a) => [a.id, a]));
  const zurueckGeben = (karte: ProjektKarte) => { setHinweis(""); setNacharbeiter(""); setZurueck(karte); };
  // „Übergeben an …“: das Fenster gehört der Board-Ansicht, Karte und Karten-Fenster öffnen es.
  const [uebergabeId, setUebergabeId] = useState<string | null>(null);
  const uebergabeKarte = uebergabeId
    ? Object.values(board.spalten).flat().find((k) => k.id === uebergabeId) ?? null
    : null;
  const boardAktionen: BoardAktionen = { ...aktionen, uebergabeOeffnen: (k) => setUebergabeId(k.id) };
  const ausgabeJeLauf = new Map(board.laufend.map((l) => [l.task_id, l.letzte_ausgabe]));
  const [nacharbeiter, setNacharbeiter] = useState("");
  // Verworfen ist schmal eingeklappt, bis man es aufklappt.
  const [verworfenOffen, setVerworfenOffen] = useState(false);
  const imProjekt = [...new Set([...(leadId ? [leadId] : []), ...mitglieder])];
  const bisherDa = !!zurueck?.agent_id && imProjekt.includes(zurueck.agent_id);
  const standardNacharbeit = bisherDa
    ? `Wie bisher: ${agentById.get(zurueck!.agent_id!)?.name ?? "Agent"}`
    : leadId
      ? `Lead entscheidet: ${agentById.get(leadId)?.name ?? "Lead"}`
      : "Agent wählen …";

  return (
    <>
      {/* Sieben Spalten nebeneinander, feste Breite; ist der Platz zu schmal, scrollt
          das Board waagerecht. Verworfen ist eingeklappt, bis man es aufklappt. */}
      <div className="-mx-1 flex gap-3 overflow-x-auto px-1 pb-2">
        {SPALTEN.map((sp) => {
          const karten = board.spalten[sp.key] ?? [];
          const zu = sp.schmal && !verworfenOffen;
          if (zu) {
            return (
              <button key={sp.key} type="button" onClick={() => setVerworfenOffen(true)}
                title={`${sp.label} aufklappen — ${sp.beschreibung}`} aria-label={`${sp.label} aufklappen`}
                className="flex w-11 shrink-0 flex-col items-center gap-2 rounded-2xl border border-foreground/[0.06] bg-foreground/[0.02] py-3 text-muted-foreground hover:bg-foreground/[0.05] hover:text-foreground">
                <UnfoldHorizontal className="h-3.5 w-3.5" />
                <span className="rounded-full bg-foreground/[0.06] px-1.5 py-0.5 text-[11px] tabular-nums">{karten.length}</span>
                <span className="text-xs font-semibold uppercase tracking-wide [writing-mode:vertical-rl]">{sp.label}</span>
              </button>
            );
          }
          return (
            <section key={sp.key} className="flex w-[17.5rem] shrink-0 flex-col rounded-2xl border border-foreground/[0.06] bg-foreground/[0.02] p-2.5">
              <div className="mb-2 px-1">
                <h3 className="flex items-center justify-between text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  <span className="flex items-center gap-1">
                    {sp.key === "triage" && <MessageCircleQuestion className="h-3.5 w-3.5" />}
                    {sp.label}
                  </span>
                  <span className="flex items-center gap-1">
                    <span className={cn("rounded-full px-2 py-0.5 text-[11px] normal-case",
                      karten.length && sp.key === "pruefung" ? "bg-orange-500/15 text-orange-600 dark:text-orange-400"
                        : karten.length && sp.key === "triage" ? "bg-amber-500/15 text-amber-700 dark:text-amber-300"
                          : "bg-foreground/[0.06]")}>
                      {karten.length}
                    </span>
                    {sp.key === "eingang" && (
                      <button type="button" onClick={() => setNeu(true)} title="Neue Karte" aria-label="Neue Karte"
                        className="rounded-md p-0.5 hover:bg-foreground/[0.08] hover:text-foreground">
                        <Plus className="h-3.5 w-3.5" />
                      </button>
                    )}
                    {sp.schmal && (
                      <button type="button" onClick={() => setVerworfenOffen(false)} title={`${sp.label} einklappen`}
                        aria-label={`${sp.label} einklappen`} className="rounded-md p-0.5 hover:bg-foreground/[0.08] hover:text-foreground">
                        <FoldHorizontal className="h-3.5 w-3.5" />
                      </button>
                    )}
                  </span>
                </h3>
                <p className="mt-0.5 text-[11px] leading-snug text-muted-foreground/80">{sp.beschreibung}</p>
              </div>
              {/* Bis zum unteren Rand (nach „Board“-Sprung steht der Reiter oben), statt bei
                  34rem leeren Platz zu lassen. */}
              <div className="flex max-h-[34rem] flex-col gap-2 overflow-y-auto xl:max-h-none xl:h-[calc(100dvh-15rem-var(--betreiber-hinweis-h,0px))] xl:min-h-[24rem]">
                {karten.length === 0 ? (
                  <p className="px-1 py-3 text-xs text-muted-foreground/70">{sp.leer}</p>
                ) : (
                  karten.map((k) => (
                    <Karte key={k.id} karte={k} agents={agentById} ausgabe={k.task_id ? ausgabeJeLauf.get(k.task_id) : null} mitglieder={mitglieder} leadId={leadId} farbe={farbe}
                      aktionen={boardAktionen} onZurueck={zurueckGeben} onOeffnen={(karte) => setOffenId(karte.id)} />
                  ))
                )}
              </div>
            </section>
          );
        })}
      </div>

      <KartenFenster projektId={projektId} karte={offeneKarte} agents={agentById} mitglieder={mitglieder} leadId={leadId}
        farbe={farbe} aktionen={boardAktionen} onZurueck={zurueckGeben} schliessen={() => setOffenId(null)} />

      <UebergabeFenster karte={uebergabeKarte} agents={agentById} mitglieder={imProjekt} aktionen={aktionen}
        schliessen={() => setUebergabeId(null)} />

      <Fenster offen={neu} schliessen={() => setNeu(false)} titel="Neue Karte in der Inbox" breite="max-w-lg">
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
            „{zurueck?.titel}“ geht mit deinem Hinweis zur Nacharbeit. Danach steht die Karte wieder im Review.
          </p>
          <label className="block text-xs font-medium text-muted-foreground">
            Wer arbeitet nach?
            <select value={nacharbeiter} onChange={(e) => setNacharbeiter(e.target.value)}
              className="mt-1 w-full rounded-lg border border-foreground/[0.1] bg-background/80 px-2 py-1.5 text-sm text-foreground">
              <option value="">{standardNacharbeit}</option>
              {imProjekt.map((m) => (
                <option key={m} value={m}>{agentById.get(m)?.name ?? m}{m === leadId ? " (Lead)" : ""}</option>
              ))}
            </select>
          </label>
          <textarea value={hinweis} onChange={(e) => setHinweis(e.target.value)} rows={4} autoFocus
            placeholder="Was soll anders werden?"
            className="w-full resize-none rounded-lg border border-foreground/[0.1] bg-background/80 px-3 py-2 text-sm outline-none focus:border-primary/50" />
          <div className="flex justify-end gap-2">
            <button type="button" onClick={() => setZurueck(null)} className="rounded-lg px-3 py-2 text-sm text-muted-foreground hover:bg-foreground/[0.04]">Abbrechen</button>
            <button
              type="button"
              disabled={!hinweis.trim() || sendet || (!nacharbeiter && !bisherDa && !leadId)}
              onClick={async () => {
                if (!zurueck) return;
                setSendet(true);
                try {
                  await aktionen.zurueckgeben(zurueck, hinweis.trim(), nacharbeiter || null);
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

"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  ArrowRight,
  BookOpen,
  Bot,
  CalendarClock,
  Check,
  KeyRound,
  Laptop,
  Mail,
  Smartphone,
  X,
} from "lucide-react";
import { IphoneApp } from "@/components/iphone-app";
import { BridgeDownload } from "@/components/agents/bridge-download";
import { getErsteSchritte, type ErsteSchritte, type ErsterSchritt } from "@/lib/api";
import { useAuthStore } from "@/lib/auth";
import { cn } from "@/lib/utils";

const SYMBOL: Record<ErsterSchritt["id"], typeof Bot> = {
  ki_zugang: KeyRound,
  agent: Bot,
  wissen: BookOpen,
  mail_kalender: Mail,
  alltag: CalendarClock,
  iphone_app: Smartphone,
  bridge: Laptop,
};

const AUSGEBLENDET = "erste_schritte_ausgeblendet";
// Ausblenden gilt zwei Wochen — danach erinnert die Karte wieder, solange noch etwas offen ist.
const PAUSE_MS = 14 * 24 * 60 * 60 * 1000;

function ausgeblendet(uid: string | undefined): boolean {
  try {
    const seit = Number(uid ? localStorage.getItem(`${AUSGEBLENDET}:${uid}`) : 0);
    return seit > 0 && Date.now() - seit < PAUSE_MS;
  } catch {
    return false;
  }
}

/** „Erste Schritte“ (#1047): Schritte, die sich anhand echter Daten selbst abhaken.
 *  Die Bridge kommt zuletzt und bringt ihre Downloads gleich mit. */
/** Auf Start in zwei Rollen (Daniel, 14.11.2026):
 *  - ``platz="oben"``: die volle Einführung, nur solange noch nichts erledigt ist —
 *    neue Nutzer bekommen einen sauberen Einstieg.
 *  - ``platz="unten"``: ab dem ersten erledigten Schritt eine Zeile unter dem Tag,
 *    aufklappbar. Oben schob die volle Karte den Tagesüberblick unter die Falz
 *    (Personas). Ohne ``platz`` (Übersicht): immer die volle Karte. */
export function ErsteSchritteKarte({ platz }: { platz?: "oben" | "unten" } = {}) {
  const [aufgeklappt, setAufgeklappt] = useState(platz !== "unten");
  const uid = useAuthStore((s) => s.user?.id);
  const [stand, setStand] = useState<ErsteSchritte | null>(null);
  const [zu, setZu] = useState(true);
  // iPhone-App und Bridge haben keine Zielseite: ihr Inhalt klappt in der Karte auf.
  const [auf, setAuf] = useState<string | null>(null);
  // Erledigte Schritte standen durchgestrichen in voller Höhe da und schoben das
  // Offene nach unten (UI-Bewertung 14.11.2026) — jetzt eine Zeile, aufklappbar.
  const [erledigteZeigen, setErledigteZeigen] = useState(false);
  // Oben auf Start nur der nächste Schritt: die volle Liste (6 Schritte, ~1.300 px
  // am Handy) schob den Tag unter die Falz (Personas Runde 4, 14.11.2026).
  const [alleZeigen, setAlleZeigen] = useState(platz !== "oben");

  useEffect(() => {
    setZu(ausgeblendet(uid));
    let aktiv = true;
    getErsteSchritte()
      .then((s) => aktiv && setStand(s))
      .catch(() => aktiv && setStand(null));
    return () => {
      aktiv = false;
    };
  }, [uid]);

  // Fertig oder bewusst ausgeblendet: Platz für die Arbeit.
  if (!stand || zu || stand.erledigt >= stand.gesamt) return null;
  if (platz === "oben" && stand.erledigt > 0) return null;
  if (platz === "unten" && stand.erledigt === 0) return null;

  const naechster = stand.schritte.find((s) => !s.erledigt);
  const anteil = Math.round((stand.erledigt / stand.gesamt) * 100);

  const schliessen = () => {
    try {
      if (uid) localStorage.setItem(`${AUSGEBLENDET}:${uid}`, String(Date.now()));
    } catch {
      // ohne Speicher bleibt sie eben bis zum Neuladen weg
    }
    setZu(true);
  };

  if (!aufgeklappt) {
    return (
      <section aria-label="Erste Schritte" className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-xl border border-border bg-card px-4 py-3">
        {/* Am Handy volle Breite, Knöpfe darunter: daneben brach der Text in eine
            schmale Spalte um (Gestaltungsprüfung Runde 5). */}
        <div className="min-w-0 flex-1 basis-full sm:basis-0">
          <p className="text-sm font-medium">
            Einstieg: {stand.erledigt} von {stand.gesamt} erledigt
            {naechster && <span className="font-normal text-muted-foreground"> · Als Nächstes: {naechster.titel}</span>}
          </p>
          <div className="mt-2 h-1 w-full max-w-xs overflow-hidden rounded-full bg-foreground/[0.06]" aria-hidden="true">
            <div className="h-full rounded-full bg-primary" style={{ width: `${anteil}%` }} />
          </div>
        </div>
        <button type="button" onClick={() => setAufgeklappt(true)}
          className="inline-flex h-9 items-center rounded-lg border border-border px-3 text-[13px] font-medium hover:bg-foreground/[0.04]">
          Schritte zeigen
        </button>
        <button type="button" onClick={schliessen} aria-label="Erste Schritte für zwei Wochen ausblenden" title="Für zwei Wochen ausblenden"
          className="rounded-lg p-2 text-muted-foreground hover:bg-foreground/[0.06] hover:text-foreground">
          <X className="h-4 w-4" />
        </button>
      </section>
    );
  }

  return (
    <section
      aria-labelledby="erste-schritte-titel"
      className="rounded-xl border border-border bg-card p-4 sm:p-6"
    >
      <div className="flex items-start justify-between gap-4">
        <div>
          <h3 id="erste-schritte-titel" className="font-display text-lg font-semibold tracking-tight">
            In {stand.gesamt} Schritten startklar
          </h3>
          <p className="mt-1 text-sm text-muted-foreground">
            {stand.erledigt} von {stand.gesamt} erledigt. Jeder Schritt hakt sich selbst ab, sobald er
            eingerichtet ist.
          </p>
        </div>
        <button
          type="button"
          onClick={schliessen}
          aria-label="Erste Schritte für zwei Wochen ausblenden"
          title="Für zwei Wochen ausblenden"
          className="rounded-lg p-1.5 text-muted-foreground hover:bg-foreground/[0.06] hover:text-foreground transition-colors"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      <div
        className="mt-4 h-1.5 w-full overflow-hidden rounded-full bg-foreground/[0.06]"
        role="progressbar"
        aria-valuenow={stand.erledigt}
        aria-valuemin={0}
        aria-valuemax={stand.gesamt}
      >
        <div className="h-full rounded-full bg-primary transition-all duration-500" style={{ width: `${anteil}%` }} />
      </div>

      {stand.erledigt > 0 && (
        <button
          type="button"
          onClick={() => setErledigteZeigen((v) => !v)}
          aria-expanded={erledigteZeigen}
          className="mt-4 inline-flex items-center gap-1.5 text-[13px] text-muted-foreground hover:text-foreground"
        >
          <Check className="h-3.5 w-3.5 text-emerald-600 dark:text-emerald-400" />
          {stand.erledigt} erledigt · {erledigteZeigen ? "ausblenden" : "anzeigen"}
        </button>
      )}

      <ol className="mt-3 space-y-2">
        {stand.schritte.map((s, i) => {
          if (s.erledigt && !erledigteZeigen) return null;
          const Symbol = SYMBOL[s.id] ?? Bot;
          const istNaechster = s.id === naechster?.id;
          if (!alleZeigen && !istNaechster) return null;
          return (
            <li
              key={s.id}
              className={cn(
                "flex flex-wrap items-start gap-3 rounded-lg border p-3 transition-colors",
                istNaechster ? "border-primary/30 bg-card" : "border-transparent",
              )}
            >
              <div
                className={cn(
                  "mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full",
                  s.erledigt ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400" : "bg-foreground/[0.06] text-muted-foreground",
                )}
              >
                {s.erledigt ? <Check className="h-4 w-4" /> : <Symbol className="h-4 w-4" />}
              </div>
              {/* Auf dem Handy rutscht der Knopf unter den Text, statt ihn in eine Wortspalte zu quetschen. */}
              <div className="min-w-0 flex-1 basis-[calc(100%-2.5rem)] sm:basis-0">
                <p className={cn("text-sm font-medium", s.erledigt && "text-muted-foreground line-through")}>
                  {i + 1}. {s.titel}
                </p>
                {!s.erledigt && (
                  <p className="mt-0.5 text-[13px] text-muted-foreground">
                    {s.text}
                    {s.hinweis && <span className="block mt-1 italic">{s.hinweis}</span>}
                  </p>
                )}
                {!s.erledigt && s.id === "iphone_app" && (istNaechster || auf === s.id) && (
                  <div className="mt-3">
                    <IphoneApp kompakt />
                  </div>
                )}
                {!s.erledigt && s.id === "bridge" && (istNaechster || auf === s.id) && (
                  <div className="mt-3">
                    <BridgeDownload />
                  </div>
                )}
              </div>
              {!s.erledigt && !s.link && !istNaechster && (
                <button
                  type="button"
                  onClick={() => setAuf(auf === s.id ? null : s.id)}
                  aria-expanded={auf === s.id}
                  className="ml-10 inline-flex shrink-0 items-center gap-1.5 rounded-lg px-3 py-1.5 text-[13px] font-medium text-primary hover:bg-primary/10 transition-colors sm:ml-0"
                >
                  {auf === s.id ? "Zuklappen" : "Anzeigen"}
                </button>
              )}
              {!s.erledigt && s.link && (
                <Link
                  href={s.link}
                  className={cn(
                    "ml-10 inline-flex shrink-0 items-center gap-1.5 rounded-lg px-3 py-1.5 text-[13px] font-medium transition-colors sm:ml-0",
                    istNaechster
                      ? "bg-primary text-primary-foreground hover:bg-primary/90"
                      : "text-primary hover:bg-primary/10",
                  )}
                >
                  {istNaechster ? "Jetzt machen" : "Öffnen"}
                  <ArrowRight className="h-3.5 w-3.5" />
                </Link>
              )}
            </li>
          );
        })}
      </ol>
      {!alleZeigen && (
        <button
          type="button"
          onClick={() => setAlleZeigen(true)}
          className="mt-2 inline-flex h-9 items-center rounded-lg px-3 text-[13px] font-medium text-primary hover:bg-primary/10"
        >
          Alle {stand.gesamt} Schritte zeigen
        </button>
      )}
    </section>
  );
}

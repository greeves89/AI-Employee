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
export function ErsteSchritteKarte() {
  const uid = useAuthStore((s) => s.user?.id);
  const [stand, setStand] = useState<ErsteSchritte | null>(null);
  const [zu, setZu] = useState(true);
  // iPhone-App und Bridge haben keine Zielseite: ihr Inhalt klappt in der Karte auf.
  const [auf, setAuf] = useState<string | null>(null);

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

  return (
    <section
      aria-labelledby="erste-schritte-titel"
      className="rounded-xl border border-primary/20 bg-gradient-to-br from-primary/[0.06] to-transparent p-6"
    >
      <div className="flex items-start justify-between gap-4">
        <div>
          <h3 id="erste-schritte-titel" className="text-lg font-semibold tracking-tight">
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

      <ol className="mt-5 space-y-2">
        {stand.schritte.map((s, i) => {
          const Symbol = SYMBOL[s.id] ?? Bot;
          const istNaechster = s.id === naechster?.id;
          return (
            <li
              key={s.id}
              className={cn(
                "flex items-start gap-3 rounded-lg border p-3 transition-colors",
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
              <div className="min-w-0 flex-1">
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
                  className="inline-flex shrink-0 items-center gap-1.5 rounded-lg px-3 py-1.5 text-[13px] font-medium text-primary hover:bg-primary/10 transition-colors"
                >
                  {auf === s.id ? "Zuklappen" : "Anzeigen"}
                </button>
              )}
              {!s.erledigt && s.link && (
                <Link
                  href={s.link}
                  className={cn(
                    "inline-flex shrink-0 items-center gap-1.5 rounded-lg px-3 py-1.5 text-[13px] font-medium transition-colors",
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
    </section>
  );
}

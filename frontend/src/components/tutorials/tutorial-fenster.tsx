"use client";

import { useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import { create } from "zustand";
import { Check, PlayCircle } from "lucide-react";
import { getErsteSchritte, type ErsteSchritte } from "@/lib/api";
import { Fenster } from "@/components/ui/fenster";
import { markTutorialSeen, useAuthStore } from "@/lib/auth";
import { TUTORIALS, tutorialFinden } from "@/lib/tutorials";
import { TutorialVideo } from "@/components/tutorials/tutorial-video";
import { cn } from "@/lib/utils";

/**
 * Willkommensfenster mit den Klick-Tutorials.
 *
 * Oeffnet sich beim ersten Start von selbst (``tutorial_seen_at`` leer); beim
 * Schliessen merkt sich der Server das je Nutzer — nicht der Browser, sonst kaeme
 * es auf jedem Geraet wieder. Danach oeffnet es nur noch Hilfe & FAQ.
 */
interface TutorialState {
  offen: boolean;
  aktiv: string;
  automatisch: boolean;
  oeffnen: (id?: string, automatisch?: boolean) => void;
  waehlen: (id: string) => void;
  schliessen: () => void;
}

export const useTutorials = create<TutorialState>((set) => ({
  offen: false,
  aktiv: TUTORIALS[0].id,
  automatisch: false,
  oeffnen: (id, automatisch = false) =>
    set({ offen: true, automatisch, aktiv: id && tutorialFinden(id) ? id : TUTORIALS[0].id }),
  waehlen: (aktiv) => set({ aktiv }),
  schliessen: () => set({ offen: false, automatisch: false }),
}));

// Hier kein Fenster von selbst: Anmeldung, Einrichtung, Vollbild-Ansichten.
const OHNE_WILLKOMMEN = ["/login", "/register", "/onboarding", "/kiosk", "/p/"];

export function TutorialFenster() {
  const { offen, aktiv, automatisch, oeffnen, waehlen, schliessen } = useTutorials();
  const user = useAuthStore((s) => s.user);
  const setUser = useAuthStore((s) => s.setUser);
  const pathname = usePathname();
  const schonGeoeffnet = useRef(false);
  // Beim ersten Start derselbe Weg wie auf dem Dashboard (#1047) — eine Quelle: der Server.
  const [weg, setWeg] = useState<ErsteSchritte | null>(null);

  useEffect(() => {
    if (!offen || !automatisch) return;
    let aktiv = true;
    getErsteSchritte()
      .then((s) => aktiv && setWeg(s))
      .catch(() => aktiv && setWeg(null));
    return () => {
      aktiv = false;
    };
  }, [offen, automatisch]);

  useEffect(() => {
    if (schonGeoeffnet.current || !user || user.tutorial_seen_at) return;
    if (OHNE_WILLKOMMEN.some((p) => pathname.startsWith(p))) return;
    schonGeoeffnet.current = true;
    oeffnen(undefined, true);
  }, [user, pathname, oeffnen]);

  const zu = () => {
    if (automatisch && user && !user.tutorial_seen_at) {
      markTutorialSeen()
        .then((r) => setUser({ ...user, tutorial_seen_at: r.tutorial_seen_at }))
        .catch(() => {});
    }
    schliessen();
  };

  const t = tutorialFinden(aktiv) ?? TUTORIALS[0];
  return (
    <Fenster offen={offen} schliessen={zu} titel={automatisch ? "Willkommen — so funktioniert AI Employee" : "Klick-Tutorials"} breite="max-w-5xl">
      {automatisch && weg && (
        <div className="border-b border-foreground/[0.06] px-5 pt-5 pb-4">
          <div className="text-sm font-medium">In {weg.gesamt} Schritten startklar</div>
          <p className="mt-0.5 text-xs text-muted-foreground">
            Diese Schritte stehen auch auf deinem Dashboard und haken sich dort von selbst ab.
          </p>
          <ol className="mt-3 grid grid-cols-1 gap-2 sm:grid-flow-col sm:auto-cols-fr">
            {weg.schritte.map((s, i) => (
              <li
                key={s.id}
                className={cn(
                  "flex items-start gap-2 rounded-lg border px-2.5 py-2 text-xs",
                  s.erledigt ? "border-emerald-500/30 bg-emerald-500/[0.06]" : "border-foreground/[0.08]",
                )}
              >
                <span
                  className={cn(
                    "flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[11px] font-medium",
                    s.erledigt ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400" : "bg-foreground/[0.06]",
                  )}
                >
                  {s.erledigt ? <Check className="h-3 w-3" /> : i + 1}
                </span>
                <span className="font-medium leading-snug">{s.titel}</span>
              </li>
            ))}
          </ol>
        </div>
      )}
      <div className="flex flex-col gap-4 p-5 md:flex-row">
        <nav className="flex shrink-0 flex-col gap-1.5 md:w-64">
          {TUTORIALS.map((x, i) => (
            <button
              key={x.id}
              onClick={() => waehlen(x.id)}
              className={cn(
                "flex items-start gap-3 rounded-xl border px-3 py-2.5 text-left transition-colors",
                x.id === t.id
                  ? "border-primary/40 bg-primary/[0.08]"
                  : "border-foreground/[0.06] hover:bg-foreground/[0.04]",
              )}
            >
              <span className="mt-0.5 font-mono text-[11px] text-muted-foreground">{String(i + 1).padStart(2, "0")}</span>
              <span className="min-w-0">
                <span className="block text-sm font-medium">{x.titel}</span>
                <span className="block text-xs text-muted-foreground">
                  {x.kurz}
                  {x.dauer && ` · ${x.dauer}`}
                </span>
              </span>
            </button>
          ))}
        </nav>
        <div className="min-w-0 flex-1">
          <TutorialVideo id={t.id} className="rounded-xl" />
          <div className="mt-3 flex items-start gap-2">
            <PlayCircle className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
            <div>
              <div className="text-sm font-medium">{t.titel}</div>
              <div className="text-xs text-muted-foreground">
                {t.kurz} Mit Sprecher — Untertitel lassen sich im Video zuschalten.
              </div>
            </div>
          </div>
          <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-foreground/[0.06] pt-4">
            <span className="text-xs text-muted-foreground">Jederzeit wieder unter Hilfe &amp; FAQ.</span>
            <button
              onClick={zu}
              className="rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90"
            >
              {automatisch ? "Los geht's" : "Schließen"}
            </button>
          </div>
        </div>
      </div>
    </Fenster>
  );
}

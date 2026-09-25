"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Info, KeyRound, X } from "lucide-react";
import { getBase } from "@/lib/config";
import { useAuthStore } from "@/lib/auth";
import { useSidebarCollapsed } from "@/hooks/use-sidebar";
import { cn } from "@/lib/utils";

/** Hoehe des Streifens. Wird als CSS-Variable gesetzt, damit Hauptbereich und
 *  Vollbild-Ansichten (Chat) ihm Platz machen, statt von ihm verdeckt zu werden. */
const HOEHE = "2.75rem";
const VARIABLE = "--betreiber-hinweis-h";

/** Alle 30 Minuten nachsehen — wie der Versionscheck, denselben Endpunkt. */
const PRUEF_INTERVALL = 30 * 60 * 1000;

/** Direkt zum Eintragen — Reiter "System", Abschnitt "Lizenz" der Einstellungen. */
export const LIZENZ_EINTRAGEN_PFAD = "/settings?tab=system#lizenz";

/** Merkt sich das Wegklicken je Text: ein NEUER Hinweis erscheint wieder. */
const WEGGEKLICKT_SCHLUESSEL = "betreiber-hinweis-weggeklickt";

const EMAIL = /[\w.+-]+@[\w-]+\.[\w.-]+/;

/** Hinweis des Anbieters als dezent gelber Streifen am unteren Rand.
 *
 *  Er kommt aus der Antwort auf das taegliche Lebenszeichen der Anlage.
 *  Er sperrt nichts und blockiert keine Arbeit — deshalb ein Streifen und kein
 *  Dialog. Wegklickbar fuer die Sitzung; zusaetzlich steht er als
 *  Benachrichtigung bei den Administratoren und bleibt dort nachlesbar. */
export function BetreiberHinweis() {
  const { collapsed } = useSidebarCollapsed();
  // Eintragen duerfen nur Administratoren — allen anderen fuehrte der Knopf ins Leere.
  const istAdmin = useAuthStore((s) => s.user?.role === "admin");
  const [hinweis, setHinweis] = useState("");
  const [weggeklickt, setWeggeklickt] = useState<string | null>(null);

  useEffect(() => {
    try {
      setWeggeklickt(sessionStorage.getItem(WEGGEKLICKT_SCHLUESSEL));
    } catch {
      // ohne Speicher erscheint er eben nach jedem Laden wieder
    }

    async function laden() {
      try {
        const res = await fetch(`${getBase()}/version/`, { credentials: "include" });
        if (!res.ok) return;
        const daten: { betreiber_hinweis?: string } = await res.json();
        setHinweis((daten.betreiber_hinweis || "").trim());
      } catch {
        // kein Hinweis ist kein Fehler
      }
    }

    laden();
    const intervall = setInterval(laden, PRUEF_INTERVALL);
    return () => clearInterval(intervall);
  }, []);

  const sichtbar = hinweis.length > 0 && weggeklickt !== hinweis;

  // Platz schaffen, solange er sichtbar ist — und wieder freigeben.
  useEffect(() => {
    const wurzel = document.documentElement;
    if (sichtbar) wurzel.style.setProperty(VARIABLE, HOEHE);
    else wurzel.style.removeProperty(VARIABLE);
    return () => {
      wurzel.style.removeProperty(VARIABLE);
    };
  }, [sichtbar]);

  if (!sichtbar) return null;

  function wegklicken() {
    try {
      sessionStorage.setItem(WEGGEKLICKT_SCHLUESSEL, hinweis);
    } catch {
      // dann nur fuer diese Ansicht
    }
    setWeggeklickt(hinweis);
  }

  // Eine Mailadresse im Text wird anklickbar — der Hinweis bittet ja um Kontakt.
  const treffer = hinweis.match(EMAIL);
  const inhalt = treffer ? (
    <>
      {hinweis.slice(0, treffer.index)}
      <a href={`mailto:${treffer[0]}`} className="font-medium underline underline-offset-2 hover:no-underline">
        {treffer[0]}
      </a>
      {hinweis.slice((treffer.index ?? 0) + treffer[0].length)}
    </>
  ) : hinweis;

  return (
    <div
      role="status"
      style={{ height: HOEHE }}
      className={cn(
        "fixed bottom-0 right-0 left-0 z-20 flex items-center gap-2.5 px-4",
        "border-t border-amber-200 bg-amber-50 text-amber-900",
        "dark:border-amber-500/25 dark:bg-amber-950/70 dark:text-amber-200",
        "backdrop-blur-sm transition-[left] duration-300",
        collapsed ? "lg:left-[64px]" : "lg:left-[260px]"
      )}
    >
      <Info className="h-4 w-4 shrink-0 text-amber-600 dark:text-amber-400" aria-hidden />
      <p className="min-w-0 flex-1 truncate text-sm" title={hinweis}>
        {inhalt}
      </p>
      {istAdmin && (
        <Link
          href={LIZENZ_EINTRAGEN_PFAD}
          className={cn(
            "inline-flex shrink-0 items-center gap-1.5 rounded-md px-2.5 py-1 text-xs font-medium transition-colors",
            "bg-amber-500 text-white hover:bg-amber-600",
            "dark:bg-amber-500/90 dark:text-amber-950 dark:hover:bg-amber-400"
          )}
        >
          <KeyRound className="h-3.5 w-3.5" aria-hidden />
          <span className="hidden sm:inline">Lizenzschlüssel eintragen</span>
          <span className="sm:hidden">Lizenz</span>
        </Link>
      )}
      <button
        type="button"
        onClick={wegklicken}
        aria-label="Hinweis ausblenden"
        className="shrink-0 rounded-md p-1 text-amber-700/70 transition-colors hover:bg-amber-100 hover:text-amber-900 dark:text-amber-300/70 dark:hover:bg-amber-500/15 dark:hover:text-amber-100"
      >
        <X className="h-4 w-4" />
      </button>
    </div>
  );
}

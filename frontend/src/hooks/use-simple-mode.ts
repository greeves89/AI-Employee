"use client";

import { create } from "zustand";
import { useAuthStore } from "@/lib/auth";

/**
 * Einfache Ansicht fuer alle, die KEINE Systemadmins sind.
 *
 * Die Oberflaeche zeigt Admins, was sie zum Betrieb brauchen: Tokens, Kosten je
 * Zug, Werkzeugaufrufe, Container-Werte, Agenten-IDs. Fuer Mitglieder ist das
 * Rauschen — die Rueckmeldung war wiederholt "zu technisch". Wer kein Admin ist,
 * bekommt deshalb die einfache Ansicht: Ergebnis zuerst, Technik weg.
 *
 * Bis v1.339.5 gab es diese Ansicht schon (Chat, Agentenkarte, Task-Detail,
 * Agent-Anlage werten sie aus), aber niemand kam hinein: sie hing allein an
 * einem Wert im Browser-Speicher, den nichts je setzte.
 *
 * Admins koennen die Mitglieder-Ansicht zur Kontrolle einschalten
 * ("Mitglieder-Ansicht" im Nutzermenue) — gemerkt je Browser.
 */

const VORSCHAU_SCHLUESSEL = "ui_mitglieder_ansicht";

function vorschauLesen(): boolean {
  try {
    return typeof window !== "undefined" && localStorage.getItem(VORSCHAU_SCHLUESSEL) === "1";
  } catch {
    return false;
  }
}

interface VorschauState {
  mitgliederAnsicht: boolean;
  setMitgliederAnsicht: (an: boolean) => void;
}

const useVorschau = create<VorschauState>((set) => ({
  mitgliederAnsicht: vorschauLesen(),
  setMitgliederAnsicht: (an: boolean) => {
    try {
      localStorage.setItem(VORSCHAU_SCHLUESSEL, an ? "1" : "0");
    } catch {
      // ohne Speicher gilt die Wahl nur fuer diese Sitzung
    }
    set({ mitgliederAnsicht: an });
  },
}));

export function useSimpleMode() {
  const rolle = useAuthStore((s) => s.user?.role);
  const mitgliederAnsicht = useVorschau((s) => s.mitgliederAnsicht);
  const setMitgliederAnsicht = useVorschau((s) => s.setMitgliederAnsicht);
  const istAdmin = rolle === "admin";
  // Solange der Nutzer noch nicht geladen ist, nichts verstecken — sonst
  // flackert bei Admins beim Laden kurz die einfache Ansicht auf.
  const simpleMode = rolle === undefined ? false : istAdmin ? mitgliederAnsicht : true;
  return { simpleMode, istAdmin, mitgliederAnsicht, setMitgliederAnsicht };
}

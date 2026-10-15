"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import * as api from "@/lib/api";
import { useToast } from "@/components/ui/dialog-provider";
import {
  AGENTS_PAGE_KEY,
  DEFAULT_AGENTS_PAGE_PREFS,
  normalizeAgentsPagePrefs,
  type AgentsPagePrefs,
} from "@/lib/agents-page-prefs";

/** Sortierung, Spalten und Gruppierung ändern sich oft in kurzer Folge — gesammelt speichern. */
const SAVE_DEBOUNCE_MS = 1000;
const RETRY_DELAY_MS = 1500;

export type PrefsUpdate = (prev: AgentsPagePrefs) => AgentsPagePrefs;

/**
 * Einstellungen der Agentenseite am Konto (Schlüssel `agents_page`).
 *
 * - Laden scheitert → Standardwerte, nichts blockiert: die Seite rendert sofort
 *   mit den Standardwerten und übernimmt den gespeicherten Stand, sobald er da ist.
 * - Hat die Person schon etwas geändert, bevor er da war, gewinnt ihre Änderung;
 *   gespeicherte Filter werden zusammengeführt statt überschrieben.
 * - Gespeichert wird immer der ganze Schlüssel, der Reihe nach (keine Überholer).
 *   Scheitert es, folgt ein Wiederholungsversuch, danach der Hinweis
 *   „Einstellungen nicht gespeichert“.
 */
export function useAgentsPagePrefs() {
  const toast = useToast();
  const [prefs, setPrefs] = useState<AgentsPagePrefs>(DEFAULT_AGENTS_PAGE_PREFS);
  const [saveFailed, setSaveFailed] = useState(false);

  const prefsRef = useRef(prefs);
  const loadedRef = useRef(false);
  const touchedRef = useRef(false);
  const dirtyRef = useRef(false);
  const lastSavedRef = useRef<string | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const queueRef = useRef<Promise<void>>(Promise.resolve());
  const toastRef = useRef(toast);
  toastRef.current = toast;

  const send = useCallback(() => {
    if (!loadedRef.current) return; // nach dem Laden wird nachgeholt
    dirtyRef.current = false;
    queueRef.current = queueRef.current.then(async () => {
      const body = JSON.stringify(prefsRef.current);
      if (body === lastSavedRef.current) return;
      const changes = { [AGENTS_PAGE_KEY]: prefsRef.current };
      for (let attempt = 0; attempt < 2; attempt++) {
        try {
          await api.patchUiPreferences(changes);
          lastSavedRef.current = body;
          setSaveFailed(false);
          return;
        } catch {
          if (attempt === 0) await new Promise((r) => setTimeout(r, RETRY_DELAY_MS));
        }
      }
      setSaveFailed(true);
      toastRef.current.warning("Einstellungen nicht gespeichert", "Die Änderung gilt nur bis zum Neuladen.");
    });
  }, []);

  useEffect(() => {
    let cancelled = false;
    api
      .getUiPreferences()
      .then((raw) => {
        if (cancelled) return;
        const server = normalizeAgentsPagePrefs(raw?.[AGENTS_PAGE_KEY]);
        lastSavedRef.current = JSON.stringify(server);
        if (!touchedRef.current) {
          prefsRef.current = server;
          setPrefs(server);
          return;
        }
        const local = prefsRef.current;
        const known = new Set(server.filter.map((f) => f.id));
        const merged: AgentsPagePrefs = {
          ...local,
          filter: [...server.filter, ...local.filter.filter((f) => !known.has(f.id))],
        };
        prefsRef.current = merged;
        setPrefs(merged);
        dirtyRef.current = true;
      })
      .catch(() => {
        // Standardwerte behalten. Nichts gespeichert zu haben ist kein Fehler.
      })
      .finally(() => {
        if (cancelled) return;
        loadedRef.current = true;
        if (dirtyRef.current && !timerRef.current) send();
      });
    return () => {
      cancelled = true;
    };
  }, [send]);

  // Eine noch ausstehende entprellte Änderung beim Verlassen nicht verlieren.
  useEffect(() => {
    const flush = () => {
      if (!timerRef.current) return;
      clearTimeout(timerRef.current);
      timerRef.current = null;
      if (!loadedRef.current) return;
      const body = JSON.stringify(prefsRef.current);
      if (body === lastSavedRef.current) return;
      api.patchUiPreferences({ [AGENTS_PAGE_KEY]: prefsRef.current }, { keepalive: true }).catch(() => {});
    };
    window.addEventListener("pagehide", flush);
    return () => {
      window.removeEventListener("pagehide", flush);
      flush();
    };
  }, []);

  /** Einstellungen ändern. `immediate` für Dinge, die beim nächsten Laden sicher da
   *  sein müssen (Ansicht, gespeicherte Filter); sonst etwa 1 s entprellt. */
  const update = useCallback(
    (fn: PrefsUpdate, { immediate = false }: { immediate?: boolean } = {}) => {
      touchedRef.current = true;
      const next = fn(prefsRef.current);
      if (next === prefsRef.current) return;
      prefsRef.current = next;
      setPrefs(next);
      dirtyRef.current = true;
      if (timerRef.current) clearTimeout(timerRef.current);
      timerRef.current = null;
      if (immediate) {
        send();
      } else {
        timerRef.current = setTimeout(() => {
          timerRef.current = null;
          send();
        }, SAVE_DEBOUNCE_MS);
      }
    },
    [send],
  );

  return { prefs, update, saveFailed };
}

// Polling nur, solange der Tab sichtbar ist.
//
// Warum es das gibt: Jede offene Ansicht fragt im Takt nach (Freigaben alle 3 s,
// Aufgaben alle 4 s, Agent alle 15 s …) — auch in Tabs, die gerade niemand
// ansieht. Alles zaehlt gegen dasselbe Rate-Limit pro Nutzer (120/min,
// APIRateLimitMiddleware). Zwei offene Chat-Tabs lagen am 24.09.2026 allein mit
// den Freigaben bei 40 Anfragen pro Minute; zusammen mit einem hakenden
// Zweitgeraet war das Kontingent weg und JEDES Geraet bekam 429.
//
// Verhalten:
// - Im Hintergrund faellt der Takt aus (kein Netzverkehr).
// - Wird der Tab wieder sichtbar, laeuft `fn` sofort einmal — die Anzeige ist
//   also nicht veraltet, nur weil sie eine Weile im Hintergrund lag.
// - Ohne `document` (SSR) verhaelt es sich wie ein normales setInterval.

export function isTabVisible(): boolean {
  return typeof document === "undefined" || document.visibilityState !== "hidden";
}

/**
 * Wie `setInterval`, aber nur bei sichtbarem Tab. Gibt die Aufraeum-Funktion
 * zurueck — direkt als Rueckgabe eines useEffect verwendbar.
 */
export function setVisibleInterval(fn: () => void, ms: number): () => void {
  const timer = setInterval(() => {
    if (isTabVisible()) fn();
  }, ms);
  const onVisibility = () => {
    if (isTabVisible()) fn();
  };
  if (typeof document !== "undefined") {
    document.addEventListener("visibilitychange", onVisibility);
  }
  return () => {
    clearInterval(timer);
    if (typeof document !== "undefined") {
      document.removeEventListener("visibilitychange", onVisibility);
    }
  };
}

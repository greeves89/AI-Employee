"use client";

import { useEffect } from "react";
import { ensureServiceWorker } from "@/lib/webpush";

/**
 * Registriert den Service Worker beim Laden.
 *
 * Nicht erst beim Einschalten der Meldungen: Ohne registrierten Worker bieten die
 * Browser die App gar nicht erst zur Installation an — der Umschalter in den
 * Einstellungen wäre dann der einzige Weg dorthin, und niemand sucht ihn.
 *
 * Rendert nichts.
 */
export function PwaRegistrar() {
  useEffect(() => {
    ensureServiceWorker();
  }, []);
  return null;
}

"use client";

/**
 * Währung und Kurs für eine Komponente.
 *
 * Vor dem ersten Ergebnis steht ``DEFAULT_MONEY`` — also USD, ohne Umrechnung.
 * Das ist Absicht: eine Zahl, die kurz die Währung wechselt, ist ehrlicher als
 * eine, die sofort in Euro erscheint, weil ein Kurs geraten wurde.
 */

import { useEffect, useMemo, useState } from "react";

import {
  DEFAULT_MONEY,
  alsEingabe,
  ausEingabe,
  formatMoney,
  loadMoneyConfig,
  moneyTitle,
  vonUsd,
  waehrungsCode,
  waehrungsZeichen,
  type MoneyConfig,
} from "@/lib/money";

export function useMoney() {
  const [cfg, setCfg] = useState<MoneyConfig>(DEFAULT_MONEY);

  useEffect(() => {
    let alive = true;
    loadMoneyConfig().then((c) => {
      if (alive) setCfg(c);
    });
    return () => {
      alive = false;
    };
  }, []);

  return useMemo(
    () => ({
      cfg,
      /** Betrag (in USD gespeichert) für die Anzeige. */
      fmt: (usd: number) => formatMoney(usd, cfg),
      /** Der Originalbetrag als `title` — gehört an jede umgerechnete Zahl. */
      title: (usd: number) => moneyTitle(usd, cfg),
      /** „€" oder „$" — für Beschriftungen von Feldern und Achsen. */
      symbol: waehrungsZeichen(cfg),
      /** „EUR" oder „USD" — für Beschriftungen wie „EUR je Tag". */
      code: waehrungsCode(cfg),
      /** USD → Zahl in der Anzeigewährung (Diagramme). */
      value: (usd: number) => vonUsd(usd, cfg),
      /** Vorbelegung eines Geldfelds (leer = kein Limit). */
      toInput: (usd: number | null | undefined) => alsEingabe(usd, cfg),
      /** Feldinhalt → USD zum Speichern; null = kein Limit, undefined = ungültig. */
      fromInput: (text: string) => ausEingabe(text, cfg),
    }),
    [cfg],
  );
}

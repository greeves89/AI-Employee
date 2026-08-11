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
  formatMoney,
  loadMoneyConfig,
  moneyTitle,
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
    }),
    [cfg],
  );
}

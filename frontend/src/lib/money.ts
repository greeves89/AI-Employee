/**
 * Geldbeträge — EINE Stelle für die Anzeige.
 *
 * Abgerechnet wird bei den Anbietern in USD, und so werden Kosten auch
 * gespeichert. Umgerechnet wird ausschliesslich hier, beim Anzeigen. Andersherum
 * — der Bestand in EUR — hinge jeder alte Betrag an dem Tageskurs, zu dem er
 * zufällig eingetragen wurde, und liesse sich nie wieder geradeziehen.
 *
 * Der Kurs kommt aus den Plattform-Einstellungen (`usd_eur_rate`) und wird
 * einmal je Seitenaufruf geholt. Bis er da ist, wird in USD angezeigt: eine
 * Zahl, die kurz die Währung wechselt, ist ehrlicher als eine, die mit einem
 * geratenen Kurs erscheint.
 */

import { getSettings } from "./api";

export interface MoneyConfig {
  /** "EUR" oder "USD" */
  currency: string;
  /** USD → EUR. Bei currency === "USD" ohne Belang. */
  rate: number;
}

/** Bis die Einstellungen da sind: keine Umrechnung. */
export const DEFAULT_MONEY: MoneyConfig = { currency: "USD", rate: 1 };

let cached: Promise<MoneyConfig> | null = null;

/** Zuletzt geladener Stand — für Hilfsfunktionen ausserhalb von Komponenten,
 *  die keinen Hook aufrufen können. */
let current: MoneyConfig = DEFAULT_MONEY;

/** Holt Währung und Kurs — einmal je Seitenaufruf, danach aus dem Zwischenspeicher. */
export function loadMoneyConfig(): Promise<MoneyConfig> {
  if (!cached) {
    cached = getSettings()
      .then((s) => {
        current = {
          currency: s.display_currency || "USD",
          // Ein unbrauchbarer Kurs darf nicht zu 0,00 € führen — dann lieber USD.
          rate: Number(s.usd_eur_rate) > 0 ? Number(s.usd_eur_rate) : 1,
        };
        return current;
      })
      .catch(() => DEFAULT_MONEY);
  }
  return cached;
}

/** Nur für Tests: den Zwischenspeicher leeren. */
export function resetMoneyConfig(): void {
  cached = null;
  current = DEFAULT_MONEY;
}

/**
 * Wie viele Nachkommastellen sind hier sinnvoll?
 *
 * Zwei feste Stellen verschlucken einen Aufruf für 0,003 € zu „0,00 €" — er sähe
 * kostenlos aus. Vier feste Stellen machen aus 138,44 € ein „138,4410", das man
 * nicht mehr lesen kann. Also nach Größe: kleine Beträge genauer.
 */
function digitsFor(value: number): number {
  const v = Math.abs(value);
  if (v === 0) return 2;
  if (v < 0.01) return 4;
  if (v < 1) return 3;
  return 2;
}

/**
 * Formatiert einen in USD gespeicherten Betrag für die Anzeige.
 *
 * Deutsche Schreibweise: Punkt als Tausender-, Komma als Dezimaltrennung.
 */
export function formatMoney(usd: number, cfg: MoneyConfig = current): string {
  const n = Number(usd);
  if (!Number.isFinite(n)) return "—";
  const toEur = cfg.currency === "EUR";
  const value = toEur ? n * cfg.rate : n;
  const digits = digitsFor(value);
  return new Intl.NumberFormat("de-DE", {
    style: "currency",
    currency: toEur ? "EUR" : "USD",
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(value);
}

/**
 * Der Originalbetrag als Beschriftung — gehört an jede umgerechnete Zahl.
 * Abgerechnet wurde in USD; wer die Rechnung nachvollziehen will, braucht sie.
 */
export function moneyTitle(usd: number, cfg: MoneyConfig = current): string {
  const n = Number(usd);
  if (!Number.isFinite(n)) return "";
  const exact = new Intl.NumberFormat("de-DE", {
    minimumFractionDigits: 4, maximumFractionDigits: 4,
  }).format(n);
  if (cfg.currency !== "EUR") return `${exact} USD`;
  const rate = new Intl.NumberFormat("de-DE", {
    minimumFractionDigits: 2, maximumFractionDigits: 4,
  }).format(cfg.rate);
  return `abgerechnet: ${exact} USD · Kurs ${rate}`;
}
